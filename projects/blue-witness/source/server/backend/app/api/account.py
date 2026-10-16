from fastapi import (
    APIRouter,
    Cookie,
    Depends,
    HTTPException,
    Response,
    Request,
    UploadFile,
    File,
    status,
)

from app.api.dependencies import (
    LEGACY_USER_SESSION_COOKIE,
    USER_SESSION_COOKIE,
    get_current_user,
)
from app.db.account_repository import (
    UserRecord,
)
from app.schemas.account import (
    AccountResponse,
    LoginRequest,
    LoginResponse,
    RegisterRequest,
    PasswordChangeRequest,
)
from app.services.account_service import (
    authenticate_account,
    logout_user_session,
    register_account,
)

from app.config import get_setting
from fastapi.responses import FileResponse
from urllib.parse import urlsplit
from starlette.concurrency import run_in_threadpool
from app.services.profile_service import (
    MAX_PROFILE_BYTES, resolve_profile_image, store_profile_image, change_own_password,
)


COOKIE_SECURE = (
    get_setting(
        "EVIDENCE_COOKIE_SECURE",
        "ACE_COOKIE_SECURE",
        "false",
    ).lower()
    == "true"
)


router = APIRouter(
    prefix="/auth",
)


def require_same_origin(request: Request) -> None:
    """쿠키 인증을 사용하는 변경 요청은 다른 Origin에서 보낼 수 없게 함."""
    if request.headers.get("sec-fetch-site") == "cross-site":
        raise HTTPException(403, "다른 사이트에서의 변경 요청은 허용하지 않습니다.")
    origin = request.headers.get("origin")
    if origin:
        try:
            parsed = urlsplit(origin)
        except ValueError as error:
            raise HTTPException(403, "요청 출처를 확인할 수 없습니다.") from error
        if parsed.netloc.lower() != request.headers.get("host", "").lower() or parsed.scheme != request.url.scheme:
            raise HTTPException(403, "別のサイトからの変更は許可されていません。")


@router.get("/profile/image", response_class=FileResponse)
def profile_image(current_user: UserRecord = Depends(get_current_user)) -> FileResponse:
    """로그인 본인 사진만 반환하며 다른 사용자 ID를 입력받지 않음."""
    return FileResponse(resolve_profile_image(current_user.id), headers={
        "Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff",
    })


@router.post("/profile/image", dependencies=[Depends(require_same_origin)])
async def upload_profile_image(
    file: UploadFile = File(...), current_user: UserRecord = Depends(get_current_user),
) -> dict:
    """업로드는 크기를 제한하고 CPU 작업은 이벤트 루프 밖에서 처리함."""
    try:
        data = await file.read(MAX_PROFILE_BYTES + 1)
        if len(data) > MAX_PROFILE_BYTES:
            raise HTTPException(413, "사진은 5MB 이하로 선택해주세요.")
        name = await run_in_threadpool(store_profile_image, current_user.id, data)
        return {"updated": True, "image_url": f"/api/v1/auth/profile/image?v={name}"}
    except ValueError as error:
        raise HTTPException(400, str(error)) from error
    except RuntimeError as error:
        raise HTTPException(503, str(error)) from error
    finally:
        await file.close()


@router.post("/password", dependencies=[Depends(require_same_origin)])
def change_password(
    payload: PasswordChangeRequest, response: Response,
    current_user: UserRecord = Depends(get_current_user),
) -> dict:
    try:
        change_own_password(current_user, payload.current_password, payload.new_password, payload.confirm_password)
    except PermissionError as error:
        raise HTTPException(403, str(error)) from error
    except ValueError as error:
        raise HTTPException(400, str(error)) from error
    for key in [USER_SESSION_COOKIE, LEGACY_USER_SESSION_COOKIE]:
        response.delete_cookie(key=key, path="/", secure=COOKIE_SECURE, httponly=True, samesite="lax")
    return {"changed": True, "reauthentication_required": True}


def user_to_response(
    user: UserRecord,
) -> AccountResponse:
    """
    DB 사용자 객체에서 password_hash를 제외하고
    안전한 API 응답으로 변환함.
    """

    return AccountResponse(
        id=user.id,
        username=user.username,
        full_name=user.full_name,
        role=user.role,
        is_active=user.is_active,
        created_at=user.created_at,
    )


@router.post(
    "/register",
    response_model=AccountResponse,
    status_code=status.HTTP_201_CREATED,
)
def register(
    payload: RegisterRequest,
) -> AccountResponse:
    """
    일반 사용자 회원가입 API임.

    role은 항상 USER로 고정함.
    """

    try:
        user = register_account(
            username=payload.username,
            password=payload.password,
            full_name=payload.full_name,
            role="USER",
        )

    except ValueError as error:
        raise HTTPException(
            status_code=(
                status.HTTP_400_BAD_REQUEST
            ),
            detail=str(error),
        ) from error

    return user_to_response(user)


@router.post(
    "/login",
    response_model=LoginResponse,
)
def login(
    payload: LoginRequest,
    response: Response,
) -> LoginResponse:
    """
    아이디·비밀번호를 검증하고
    user_session 쿠키를 발급함.
    """

    try:
        login_result = authenticate_account(
            username=payload.username,
            password=payload.password,
        )

    except PermissionError as error:
        raise HTTPException(
            status_code=(
                status.HTTP_401_UNAUTHORIZED
            ),
            detail=str(error),
        ) from error

    response.set_cookie(
        key=USER_SESSION_COOKIE,
        value=login_result.session_token,

        # JavaScript에서 로그인 토큰을 읽을 수 없게 함.
        httponly=True,

        # 다른 사이트에서 보내는 요청 위험을 줄임.
        samesite="lax",

        # 환경변수에 따라 HTTP·HTTPS 설정을 전환함.
        secure=COOKIE_SECURE,

        # 사용자 로그인은 8시간 동안 유지함.
        max_age=8 * 60 * 60,

        path="/",
    )

    # 배포 전환 중인 기존 브라우저도 유지하도록
    # 한 릴리스 동안 예전 쿠키 이름을 함께 발급함.
    response.set_cookie(
        key=LEGACY_USER_SESSION_COOKIE,
        value=login_result.session_token,
        httponly=True,
        samesite="lax",
        secure=COOKIE_SECURE,
        max_age=8 * 60 * 60,
        path="/",
    )

    return LoginResponse(
        authenticated=True,
        account=user_to_response(
            login_result.user
        ),
        session_expires_at=(
            login_result.expires_at
        ),
    )


@router.get(
    "/me",
    response_model=AccountResponse,
)
def me(
    current_user: UserRecord = Depends(
        get_current_user
    ),
) -> AccountResponse:
    """
    현재 로그인한 사용자 정보를 반환함.
    """

    return user_to_response(
        current_user
    )


@router.post(
    "/logout",
    status_code=status.HTTP_204_NO_CONTENT,
)
def logout(
    response: Response,
    user_session: str | None = Cookie(default=None),
    legacy_user_session: str | None = Cookie(
        default=None,
        alias=LEGACY_USER_SESSION_COOKIE,
        include_in_schema=False,
    ),
) -> Response:
    """
    DB 세션을 폐기하고 브라우저 쿠키를 삭제함.
    """

    session_token = user_session or legacy_user_session
    if session_token is not None:
        logout_user_session(
            session_token
        )

    response.delete_cookie(
        key=USER_SESSION_COOKIE,
        path="/",
        secure=COOKIE_SECURE,
        httponly=True,
        samesite="lax",
    )
    response.delete_cookie(
        key=LEGACY_USER_SESSION_COOKIE,
        path="/",
        secure=COOKIE_SECURE,
        httponly=True,
        samesite="lax",
    )

    response.status_code = (
        status.HTTP_204_NO_CONTENT
    )

    return response
