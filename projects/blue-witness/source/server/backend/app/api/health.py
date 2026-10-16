import time
from datetime import datetime, timezone

from fastapi import APIRouter, Response, Request, HTTPException, status
from app.api.node_dependencies import get_authenticated_node

from app.db.database import database_is_ready


router = APIRouter()


@router.get("/health")
def health_check(response: Response, request: Request) -> dict:
    """
    서버와 SQLite DB의 정상 상태를 확인함.
    """

    # Health는 종전처럼 공개한다. 노드 header가 있으면 신원만 검증해서 로그에
    # 표시하고 잘못된 키여도 기존 Health 응답 계약은 변경하지 않는다.
    if request.headers.get("X-Evidence-Node-ID") or request.headers.get("X-ACE-Node-ID"):
        try:
            get_authenticated_node(request,
                node_id=request.headers.get("X-Evidence-Node-ID"),
                legacy_node_id=request.headers.get("X-ACE-Node-ID"),
                api_key=request.headers.get("X-Evidence-API-Key"),
                legacy_api_key=request.headers.get("X-ACE-API-Key"))
        except HTTPException:
            pass  # 공개 Health는 유지하며 로그에서는 미인증 주장 ID로 구분한다.
    database_ready = database_is_ready()

    if not database_ready:
        response.status_code = (
            status.HTTP_503_SERVICE_UNAVAILABLE
        )

    now_utc = datetime.now(timezone.utc)

    return {
        "status": (
            "ok"
            if database_ready
            else "degraded"
        ),
        "service": "vehicle-evidence-server",
        "version": "0.2.0",
        "database": (
            "ok"
            if database_ready
            else "unavailable"
        ),
        "server_time_utc": now_utc.isoformat(),
        "server_time_ns": time.time_ns(),
    }
