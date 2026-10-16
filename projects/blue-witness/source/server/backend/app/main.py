from contextlib import (
    asynccontextmanager,
)
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import (
    RedirectResponse,
)
from fastapi.staticfiles import (
    StaticFiles,
)

from app.api.access import (
    router as access_router,
)
from app.api.account import (
    router as account_router,
)
from app.api.admin import (
    router as admin_router,
)
from app.api.evidence import (
    router as evidence_router,
)
from app.api.health import (
    router as health_router,
)
from app.api.nodes import (
    router as nodes_router,
)
from app.api.pages import (
    router as pages_router,
)
from app.api.portal import (
    router as portal_router,
)
from app.api.time_sync import (
    router as time_sync_router,
)
from app.api.vehicles import (
    router as vehicles_router,
)
from app.db.database import (
    initialize_database,
)
from app.services.merge_worker import (
    start_merge_worker,
    stop_merge_worker,
)
from app.services.communication_log import CommunicationLog, CommunicationLogMiddleware


BACKEND_ROOT = (
    Path(__file__).resolve().parents[1]
)

# 프론트엔드는 Python 패키지(app)와 분리해 관리함.
# URL은 기존과 동일하게 /static을 유지하므로
# 브라우저 코드와 API 주소는 변경되지 않음.
WEB_STATIC_DIRECTORY = (
    BACKEND_ROOT
    / "frontend"
    / "static"
)


@asynccontextmanager
async def lifespan(
    application: FastAPI,
):
    """
    FastAPI 서버의 시작과 종료를 관리함.

    시작:
    1. SQLite 구조를 준비함.
    2. 중단된 병합 작업을 복구함.
    3. 자동 병합 Worker를 실행함.

    종료:
    1. Worker에 종료를 요청함.
    2. 실행 중인 병합이 끝나면 종료함.
    """

    initialize_database()

    merge_worker_control = (
        await start_merge_worker()
    )

    application.state.merge_worker = (
        merge_worker_control
    )

    try:
        yield

    finally:
        await stop_merge_worker(
            merge_worker_control
        )


def create_app() -> FastAPI:
    """
    차량 사고 증거 서버 애플리케이션을 생성함.

    기능별 Router, 정적 파일, HTML 페이지를
    FastAPI에 연결함.
    """

    application = FastAPI(
        title="Vehicle Evidence Server",
        description=(
            "V2V 사고 증거 영상 수집 및 통합 서버"
        ),
        version="0.9.0",
        lifespan=lifespan,
    )
    application.state.communication_log = CommunicationLog()
    application.add_middleware(CommunicationLogMiddleware, journal=application.state.communication_log)

    application.include_router(
        health_router,
        prefix="/api/v1",
        tags=["system"],
    )

    application.include_router(
        evidence_router,
        prefix="/api/v1",
        tags=["evidence"],
    )

    application.include_router(
        time_sync_router,
        prefix="/api/v1",
        tags=["time-sync"],
    )

    application.include_router(
        access_router,
        prefix="/api/v1",
        tags=["access"],
    )

    application.include_router(
        account_router,
        prefix="/api/v1",
        tags=["account"],
    )

    application.include_router(
        nodes_router,
        prefix="/api/v1",
        tags=["nodes"],
    )

    application.include_router(
        portal_router,
        prefix="/api/v1",
        tags=["portal"],
    )

    application.include_router(
        admin_router,
        prefix="/api/v1",
        tags=["admin"],
    )

    application.include_router(
        vehicles_router,
        prefix="/api/v1",
        tags=["vehicles"],
    )

    application.mount(
        "/static",
        StaticFiles(
            directory=str(
                WEB_STATIC_DIRECTORY
            ),
        ),
        name="static",
    )

    application.include_router(
        pages_router,
    )

    @application.middleware("http")
    async def disable_html_page_cache(
        request,
        call_next,
    ):
        """
        로그인·포털·관리자 HTML이 브라우저에
        오래 캐시되는 것을 방지함.
        """

        # 0.0.0.0은 서버가 모든 인터페이스에서
        # 요청을 받기 위한 바인딩 주소일 뿐,
        # 브라우저 접속 주소로 사용하면 안 됨.
        #
        # Chrome은 http://0.0.0.0을 안전한 로컬
        # 컨텍스트로 인정하지 않아 Clipboard API를
        # 차단하므로 127.0.0.1로 자동 이동시킴.
        if request.url.hostname == "0.0.0.0":
            safe_local_url = request.url.replace(
                hostname="127.0.0.1",
            )

            return RedirectResponse(
                url=str(safe_local_url),
                status_code=307,
            )

        response = await call_next(
            request
        )

        no_cache_paths = {
            "/",
            "/login",
            "/portal",
            "/account",
            "/admin",
            "/admin/logs",
            "/api/v1/admin/communication-logs",
        }

        if (
            request.url.path
            in no_cache_paths
        ):
            response.headers[
                "Cache-Control"
            ] = (
                "no-store, no-cache, "
                "must-revalidate, max-age=0"
            )

            response.headers[
                "Pragma"
            ] = "no-cache"

            response.headers[
                "Expires"
            ] = "0"

        return response

    return application


app = create_app()
