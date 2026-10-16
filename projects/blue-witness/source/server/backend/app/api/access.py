from fastapi import (
    APIRouter,
    Cookie,
    HTTPException,
    Response,
    status,
)

from app.schemas.access import (
    AccessSessionResponse,
    AccessVerifyRequest,
    AccessVerifyResponse,
)
from app.services.access_service import (
    authenticate_access_code,
    authorize_session,
)

from app.config import get_setting


COOKIE_SECURE = (
    get_setting(
        "EVIDENCE_COOKIE_SECURE",
        "ACE_COOKIE_SECURE",
        "false",
    ).lower()
    == "true"
)


router = APIRouter(
    prefix="/access",
)


SESSION_COOKIE_NAME = "event_access_session"
LEGACY_SESSION_COOKIE_NAME = "ace_session"


@router.post(
    "/verify",
    response_model=AccessVerifyResponse,
)
def verify_access_code(
    payload: AccessVerifyRequest,
    response: Response,
) -> AccessVerifyResponse:
    """
    사고 ID와 조회 코드를 검증함.

    성공하면 원본 세션 토큰을 HttpOnly 쿠키에 저장함.

    JavaScript가 쿠키 내용을 직접 읽지 못하게 하여
    토큰 노출 위험을 줄임.
    """

    try:
        authenticated = (
            authenticate_access_code(
                event_id=payload.event_id,
                entered_access_code=(
                    payload.access_code
                ),
            )
        )

    except PermissionError as error:
        raise HTTPException(
            status_code=(
                status.HTTP_401_UNAUTHORIZED
            ),
            detail=str(error),
        ) from error

    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=authenticated.session_token,

        # JavaScript에서 토큰을 직접 읽지 못하게 함.
        httponly=True,

        # 다른 사이트에서 요청을 보내는 위험을 줄임.
        samesite="lax",

        # 환경변수에 따라 HTTP·HTTPS 설정을 전환함.
        #
        # 로컬 HTTP:
        # EVIDENCE_COOKIE_SECURE=false
        #
        # Quick Tunnel HTTPS:
        # EVIDENCE_COOKIE_SECURE=true
        secure=COOKIE_SECURE,

        # 브라우저가 30분 동안 쿠키를 유지함.
        max_age=30 * 60,

        path="/",
    )
    response.set_cookie(
        key=LEGACY_SESSION_COOKIE_NAME,
        value=authenticated.session_token,
        httponly=True,
        samesite="lax",
        secure=COOKIE_SECURE,
        max_age=30 * 60,
        path="/",
    )

    return AccessVerifyResponse(
        verified=True,
        event_id=authenticated.event_id,
        session_expires_at=(
            authenticated.expires_at
        ),
    )


@router.get(
    "/session",
    response_model=AccessSessionResponse,
)
def check_access_session(
    event_access_session: str | None = Cookie(default=None),
    legacy_event_access_session: str | None = Cookie(
        default=None,
        alias=LEGACY_SESSION_COOKIE_NAME,
        include_in_schema=False,
    ),
) -> AccessSessionResponse:
    """
    현재 브라우저 쿠키에 유효한 세션이 있는지 확인함.

    프론트엔드가 새로고침되었을 때
    로그인 상태를 복구하는 데 사용함.
    """

    session_token = event_access_session or legacy_event_access_session
    if session_token is None:
        raise HTTPException(
            status_code=(
                status.HTTP_401_UNAUTHORIZED
            ),
            detail="인증 세션이 없음.",
        )

    try:
        event_id = authorize_session(
            session_token
        )

    except PermissionError as error:
        raise HTTPException(
            status_code=(
                status.HTTP_401_UNAUTHORIZED
            ),
            detail=str(error),
        ) from error

    return AccessSessionResponse(
        authenticated=True,
        event_id=event_id,
    )


@router.post(
    "/logout",
    status_code=status.HTTP_204_NO_CONTENT,
)
def logout(
    response: Response,
) -> Response:
    """
    브라우저의 세션 쿠키를 삭제함.

    현재 단계에서는 브라우저 쿠키만 삭제하고,
    이후 보안 단계에서 DB 세션도 폐기하도록 확장할 수 있음.
    """

    response.delete_cookie(
        key=SESSION_COOKIE_NAME,
        path="/",
        secure=COOKIE_SECURE,
        httponly=True,
        samesite="lax",
    )
    response.delete_cookie(
        key=LEGACY_SESSION_COOKIE_NAME,
        path="/",
        secure=COOKIE_SECURE,
        httponly=True,
        samesite="lax",
    )

    response.status_code = (
        status.HTTP_204_NO_CONTENT
    )

    return response
