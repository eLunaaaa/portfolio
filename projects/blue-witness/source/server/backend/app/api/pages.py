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


@router.get(
    "/",
    include_in_schema=False,
)
def root_page() -> RedirectResponse:
    return RedirectResponse(
        url="/login"
    )


@router.get(
    "/login",
    include_in_schema=False,
)
def login_page() -> FileResponse:
    return FileResponse(
        PAGES_ROOT / "login.html"
    )


@router.get(
    "/portal",
    include_in_schema=False,
)
def portal_page() -> FileResponse:
    return FileResponse(
        PAGES_ROOT / "portal.html"
    )


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
