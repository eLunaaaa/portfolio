# 파일 역할: 브라우저 경로별 HTML 화면을 제공하는 FastAPI 라우터. 데이터 조회·권한 검사는 각 API와 의존성에서 수행한다.
from pathlib import Path

from fastapi import APIRouter, Depends
from app.api.dependencies import get_current_admin, get_current_user
from fastapi.responses import (
    FileResponse,
    RedirectResponse,
)


BACKEND_ROOT = (
    Path(__file__).resolve().parents[2]
)

# HTML은 백엔드 Python 패키지와 분리된
# backend/frontend/pages에서 제공함.
WEB_ROOT = (
    BACKEND_ROOT
    / "frontend"
)

PAGES_ROOT = (
    WEB_ROOT
    / "pages"
)


router = APIRouter()


@router.get("/account", include_in_schema=False, dependencies=[Depends(get_current_user)])
def account_page() -> FileResponse:
    """내 정보 페이지는 로그인한 사용자에게만 제공함."""
    return FileResponse(PAGES_ROOT / "account.html")


# 기능: 첫 접속을 로그인 화면으로 안내한다.
# 입력: 추가 인자 없음; GET / 요청.
# 출력: /login으로 이동시키는 RedirectResponse.
# 구조·이유: 초기 진입점을 하나로 모아 사용자가 로그인 화면에서 서비스를 시작하게 한다.
@router.get(
    "/",
    include_in_schema=False,
)
def root_page() -> RedirectResponse:
    return RedirectResponse(
        url="/login"
    )


# 기능: 로그인 화면 HTML을 제공한다.
# 입력: 추가 인자 없음; GET /login 요청.
# 출력: login.html의 FileResponse.
# 구조·이유: 페이지 파일을 전달하며 실제 로그인 처리는 별도의 인증 API가 담당한다.
@router.get(
    "/login",
    include_in_schema=False,
)
def login_page() -> FileResponse:
    return FileResponse(
        PAGES_ROOT / "login.html"
    )


# 기능: 사용자 포털 화면의 HTML을 제공한다.
# 입력: 추가 인자 없음; GET /portal 요청.
# 출력: portal.html의 FileResponse.
# 구조·이유: 화면 제공과 사고·차량 데이터 조회를 분리하며 API가 각 요청의 접근 권한을 확인한다.
@router.get(
    "/portal",
    include_in_schema=False,
)
def portal_page() -> FileResponse:
    return FileResponse(
        PAGES_ROOT / "portal.html"
    )


# 기능: 관리 화면의 HTML을 제공한다.
# 입력: 추가 인자 없음; GET /admin 요청.
# 출력: admin.html의 FileResponse.
# 구조·이유: 이 함수는 화면 파일을 전달하고 관리 데이터·명령의 권한 검사는 별도 API에서 수행한다.
@router.get(
    "/admin",
    include_in_schema=False,
)
def admin_page() -> FileResponse:
    return FileResponse(
        PAGES_ROOT / "admin.html"
    )


@router.get("/admin/logs", include_in_schema=False, dependencies=[Depends(get_current_admin)])
def admin_logs_page() -> FileResponse:
    """로그 페이지 HTML도 API와 동일하게 관리자 세션으로 보호한다."""
    return FileResponse(PAGES_ROOT / "admin-logs.html")
