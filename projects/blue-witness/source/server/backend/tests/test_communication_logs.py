"""임시 앱과 인증 mock으로 관리자 권한·비밀값 비저장·업로드 스트리밍을 검증한다.

실행 중인 서버, 운영 DB 및 영상 파일에는 접근하지 않는다. httpx 없이 실제
ASGI 요청/응답을 호출하여 HTTP 기록과 관리자 페이지 보호를 함께 확인한다.
"""
import asyncio
import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse
from app.main import create_app
from app.api.dependencies import get_current_user
from app.services.node_auth_service import AuthenticatedNode
from app.services.communication_log import CommunicationLog, CommunicationLogMiddleware


async def request(app, path, method="GET", headers=(), body=b"", query=b""):
    """외부 네트워크 없이 ASGI scope로 응답 status와 본문을 수집한다."""
    scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
        "method": method, "scheme": "https", "path": path, "raw_path": path.encode(),
        "query_string": query, "headers": [(b"host", b"testserver"), *headers],
        "client": ("192.168.2.2", 12345), "server": ("testserver", 8000)}
    messages = []
    consumed = False
    async def receive():
        nonlocal consumed
        if not consumed:
            consumed = True
            return {"type": "http.request", "body": body, "more_body": False}
        await asyncio.Event().wait()
    async def send(message):
        messages.append(message)
    await app(scope, receive, send)
    status = next(message["status"] for message in messages if message["type"] == "http.response.start")
    content = b"".join(message.get("body", b"") for message in messages if message["type"] == "http.response.body")
    return status, content


class CommunicationLogsTest(unittest.IsolatedAsyncioTestCase):
    async def test_admin_only_page_and_api(self):
        app = create_app()
        for path in ["/admin/logs", "/api/v1/admin/communication-logs"]:
            self.assertEqual((await request(app, path))[0], 401)
        app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=1, role="USER")
        for path in ["/admin/logs", "/api/v1/admin/communication-logs"]:
            self.assertEqual((await request(app, path))[0], 403)
        app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=2, role="ADMIN")
        self.assertEqual((await request(app, "/admin/logs"))[0], 200)
        self.assertEqual((await request(app, "/api/v1/admin/communication-logs"))[0], 200)
        self.assertEqual(app.state.communication_log.snapshot()["retained_count"], 0)

    async def test_health_identity_and_secrets(self):
        app = create_app()
        headers = [(b"x-evidence-node-id", b"NODE_PI_04_TEST"),
                   (b"x-evidence-api-key", b"SECRET_KEY_DO_NOT_LOG"), (b"cookie", b"SECRET_COOKIE")]
        with patch("app.api.health.database_is_ready", return_value=True), patch(
            "app.api.node_dependencies.authenticate_node", return_value=AuthenticatedNode("NODE_PI_04_TEST", "CAR_04")):
            self.assertEqual((await request(app, "/api/v1/health", headers=headers, query=b"secret=QUERY_SECRET"))[0], 200)
            self.assertEqual((await request(app, "/api/v1/health"))[0], 200)
        data = app.state.communication_log.snapshot()
        self.assertEqual(data["entries"][1]["vehicle_id"], "CAR_04")
        self.assertFalse(data["entries"][0]["authenticated"])
        for secret in ["SECRET_KEY", "SECRET_COOKIE", "QUERY_SECRET"]:
            self.assertNotIn(secret, json.dumps(data))

    async def test_failed_auth_and_status_request(self):
        app = create_app()
        headers = [(b"x-evidence-node-id", b"NODE_PI_04_TEST"), (b"x-evidence-api-key", b"wrong"),
                   (b"x-evidence-content-sha256", b"a" * 64)]
        path = "/api/v1/events/EVT_TEST_CAR04/evidence/status"
        with patch("app.api.node_dependencies.authenticate_node", side_effect=PermissionError("인증 실패")):
            self.assertEqual((await request(app, path, headers=headers))[0], 401)
            with patch("app.api.health.database_is_ready", return_value=True):
                self.assertEqual((await request(app, "/api/v1/health", headers=headers))[0], 200)
        with patch("app.api.node_dependencies.authenticate_node", return_value=AuthenticatedNode("NODE_PI_04_TEST", "CAR_04")), patch(
            "app.api.evidence.find_existing_evidence_upload", return_value=None):
            self.assertEqual((await request(app, path, headers=headers))[0], 404)
        data = app.state.communication_log.snapshot("evidence_status", True, 200, "CAR_04")
        self.assertEqual(len(data["entries"]), 1)
        self.assertEqual(data["entries"][0]["result"], "저장 기록 없음")
        self.assertEqual(data["entries"][0]["event_id"], "EVT_TEST_CAR04")

    async def test_streaming_response_not_consumed_or_changed(self):
        app = FastAPI()
        journal = CommunicationLog()
        app.add_middleware(CommunicationLogMiddleware, journal=journal)
        @app.post("/api/v1/events/{event_id}/evidence")
        async def upload(event_id: str, req: Request):
            self.assertEqual(await req.body(), b"TEST_MP4_BODY")
            async def chunks():
                yield b'{"storage_ack":'
                yield b'true,"event_id":"EVT_TEST"}'
            return StreamingResponse(chunks(), media_type="application/json", status_code=201)
        status, body = await request(app, "/api/v1/events/EVT_TEST/evidence", "POST", body=b"TEST_MP4_BODY")
        self.assertEqual(status, 201)
        self.assertTrue(json.loads(body)["storage_ack"])
        self.assertNotIn("TEST_MP4_BODY", json.dumps(journal.snapshot()))
        self.assertTrue(journal.snapshot()["entries"][0]["completed"])

    def test_bounded_log_and_three_latest_per_peer(self):
        journal = CommunicationLog(capacity=5)
        for index in range(9):
            journal.append({"received_at": f"2026-10-06T01:00:{index:02d}+00:00", "node_id": "N",
                "vehicle_id": "CAR_04", "client_ip": "192.168.2.2", "kind": "health",
                "status_code": 200, "completed": True})
        data = journal.snapshot()
        self.assertEqual(data["retained_count"], 5)
        self.assertEqual(len(data["peers"][0]["recent_requests"]), 3)
        self.assertEqual(data["entries"][0]["id"], 9)
