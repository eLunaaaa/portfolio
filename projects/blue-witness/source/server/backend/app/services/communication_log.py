"""관리자 디버깅용 최근 Pi 통신 기록과 스트리밍을 보존하는 ASGI middleware.

Health·Heartbeat·Evidence·시간 요청의 허용된 필드만 메모리 ring buffer에 기록한다.
요청/응답 body, API Key, 쿠키, query string은 읽거나 저장하지 않는다.
최근 2,000건만 프로세스별로 유지하며 재시작 시 초기화된다. 업로드와 ACK에는
개입하지 않고 응답 전송 완료 여부를 관찰한다. TLS/TCP 연결 실패는 HTTP가
서버에 도착하지 않으므로 기록할 수 없다.
"""
from collections import deque
from datetime import datetime, timezone
from threading import Lock
from time import perf_counter
import re


def safe_identifier(value):
    """식별자에 비정상 문자열·제어 문자·임의 secret이 들어오면 기록하지 않는다."""
    return value if isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,128}", value) else None


class CommunicationLog:
    """동시 요청의 bounded 최근 기록을 관리하며 관리자 조회에 복사본만 반환한다."""

    def __init__(self, capacity=2000):
        self.entries = deque(maxlen=capacity)
        self.lock = Lock()
        self.sequence = 0
        self.started_at = datetime.now(timezone.utc).isoformat()

    def append(self, entry):
        """응답을 마친 요청을 순서 번호와 함께 추가하고 가장 오래된 기록을 버린다."""
        with self.lock:
            self.sequence += 1
            self.entries.append({**entry, "id": self.sequence})

    def snapshot(self, kind=None, failures_only=False, limit=200, vehicle_id=None):
        """필터된 최신 요청과 전체 보관 기록 기준의 마지막 연결 정보를 반환한다."""
        with self.lock:
            entries = [dict(row) for row in self.entries]
        peers = {}
        for row in sorted(entries, key=lambda item: item["received_at"]):
            key = row["node_id"] or row["client_ip"]
            if not key:
                continue
            peer = peers.setdefault(key, {"node_id": row["node_id"], "vehicle_id": row["vehicle_id"],
                "client_ip": row["client_ip"], "last_health_at": None, "recent_requests": []})
            peer.update({"client_ip": row["client_ip"], "last_seen_at": row["received_at"],
                         "last_kind": row["kind"], "last_status": row["status_code"]})
            if row["kind"] == "health":
                peer["last_health_at"] = row["received_at"]
            peer["recent_requests"] = ([row] + peer["recent_requests"])[:3]
        selected = [row for row in reversed(entries)
                    if (kind is None or row["kind"] == kind)
                    and (vehicle_id is None or row["vehicle_id"] == vehicle_id)
                    and (not failures_only or row["status_code"] >= 400 or not row["completed"])]
        return {"entries": selected[:limit], "matching_count": len(selected),
                "retained_count": len(entries), "capacity": self.entries.maxlen,
                "started_at": self.started_at,
                "server_time": datetime.now(timezone.utc).isoformat(), "online_window_seconds": 30,
                "peers": sorted(peers.values(), key=lambda row: row["last_seen_at"], reverse=True)}


def request_kind(scope):
    """Pi 통신 endpoint만 구분하여 관리자 조회와 일반 브라우저 요청은 제외한다."""
    path = scope.get("path", "")
    if path == "/api/v1/health":
        return "health"
    if path == "/api/v1/nodes/heartbeat":
        return "heartbeat"
    if re.fullmatch(r"/api/v1/events/[^/]+/evidence/status", path):
        return "evidence_status"
    if re.fullmatch(r"/api/v1/events/[^/]+/evidence", path):
        return "evidence_upload"
    if path.startswith("/api/v1/time"):
        return "time_sync"
    return None


class CommunicationLogMiddleware:
    """수신·송신을 그대로 중계하고 본문을 보관하지 않은 채 완료 시 기록한다."""

    def __init__(self, app, journal):
        self.app = app
        self.journal = journal

    # 요청 수신 시각과 응답 완료/단절 상태를 관찰한다. body를 소비하거나 합치지
    # 않으므로 MP4 streaming·ACK·status 응답 계약은 기존 그대로 유지된다.
    async def __call__(self, scope, receive, send):
        kind = request_kind(scope) if scope["type"] == "http" else None
        if kind is None:
            await self.app(scope, receive, send)
            return
        started = perf_counter()
        received_at = datetime.now(timezone.utc).isoformat()
        status_code = 500
        completed = False

        async def observe_send(message):
            nonlocal status_code, completed
            if message["type"] == "http.response.start":
                status_code = message["status"]
            await send(message)
            if message["type"] == "http.response.body" and not message.get("more_body", False):
                completed = True

        try:
            await self.app(scope, receive, observe_send)
        finally:
            state = scope.get("state", {})
            node = state.get("authenticated_node")
            # node ID 외의 header는 접근·보관하지 않는다. 주장 ID는 인증 신원과 분리한다.
            declared = next((value.decode("latin-1") for name, value in scope.get("headers", [])
                             if name.lower() in {b"x-evidence-node-id", b"x-ace-node-id"}), None)
            event_id = safe_identifier(scope.get("path_params", {}).get("event_id"))
            self.journal.append({
                "received_at": received_at, "duration_ms": round((perf_counter() - started) * 1000, 1),
                "method": scope.get("method", ""), "kind": kind,
                "client_ip": scope.get("client", (None,))[0] if scope.get("client") else None,
                "node_id": safe_identifier(node.node_id) if node else None,
                "vehicle_id": safe_identifier(node.vehicle_id) if node else None,
                "declared_node_id": safe_identifier(declared), "authenticated": node is not None,
                "event_id": event_id, "status_code": status_code, "completed": completed,
                "result": ("응답 전송 미완료" if not completed else
                           "노드 인증 실패" if status_code == 401 else
                           "권한/차량 정보 불일치" if status_code == 403 else
                           "저장 기록 없음" if kind == "evidence_status" and status_code == 404 else
                           "요청 데이터 오류" if status_code == 422 else
                           "서버 처리 오류" if status_code >= 500 else
                           "요청 실패" if status_code >= 400 else
                           "영상 저장 ACK 응답" if kind == "evidence_upload" else
                           "저장 확인 완료" if kind == "evidence_status" else "정상 응답"),
            })
