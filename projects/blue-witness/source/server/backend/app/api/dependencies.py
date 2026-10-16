from fastapi import (
    Cookie,
    Depends,
    HTTPException,
    status,
)

from app.db.account_repository import (
    UserRecord,
)
from app.services.account_service import (
    authorize_user_session,
)


USER_SESSION_COOKIE = "user_session"
LEGACY_USER_SESSION_COOKIE = "ace_user_session"
EVENT_ACCESS_SESSION_COOKIE = "event_access_session"
LEGACY_EVENT_ACCESS_SESSION_COOKIE = "ace_session"


def get_event_access_session(
    event_access_session: str | None = Cookie(default=None),
    legacy_event_access_session: str | None = Cookie(
        default=None,
        alias=LEGACY_EVENT_ACCESS_SESSION_COOKIE,
        include_in_schema=False,
    ),
) -> str | None:
    """새 접근 쿠키를 우선하며 기존 브라우저 세션도 허용함."""

    return event_access_session or legacy_event_access_session


def get_current_user(
    user_session: str | None = Cookie(default=None),
    legacy_user_session: str | None = Cookie(
        default=None,
        alias=LEGACY_USER_SESSION_COOKIE,
        include_in_schema=False,
    ),
) -> UserRecord:
    """
    user_session 쿠키를 검증하고
    현재 로그인 사용자를 반환함.

    사용자 API와 관리자 API가 공통으로 사용함.
    """

    session_token = user_session or legacy_user_session

    if session_token is None:
        raise HTTPException(
            status_code=(
                status.HTTP_401_UNAUTHORIZED
            ),
            detail="로그인이 필요함.",
        )

    try:
        return authorize_user_session(
            session_token
        )

    except PermissionError as error:
        raise HTTPException(
            status_code=(
                status.HTTP_401_UNAUTHORIZED
            ),
            detail=str(error),
        ) from error


def get_current_admin(
    current_user: UserRecord = Depends(
        get_current_user
    ),
) -> UserRecord:
    """
    현재 사용자가 ADMIN인지 확인함.

    일반 사용자가 관리자 API를 호출하면
    403 Forbidden을 반환함.
    """

    if current_user.role != "ADMIN":
        raise HTTPException(
            status_code=(
                status.HTTP_403_FORBIDDEN
            ),
            detail="관리자 권한이 필요함.",
        )

    return current_user
