from dataclasses import dataclass
from datetime import (
    datetime,
    timedelta,
    timezone,
)
import hashlib
import re
import secrets
import sqlite3

from pwdlib import PasswordHash
from app.services.password_policy import validate_password_policy

from app.db.account_repository import (
    UserRecord,
    assign_vehicle_to_user,
    create_user,
    create_user_session,
    get_user_by_id,
    get_user_by_username,
    get_user_session,
    revoke_user_session,
    upsert_vehicle,
)


USER_SESSION_HOURS = 8

USERNAME_PATTERN = re.compile(
    r"^[A-Za-z0-9_]{4,32}$"
)

password_hash = PasswordHash.recommended()

# 존재하지 않는 아이디의 로그인에서도
# 비밀번호 검증 시간을 비슷하게 만들기 위한 더미 해시임.
DUMMY_PASSWORD_HASH = password_hash.hash(
    "DummyPasswordOnlyForTiming123!"
)


@dataclass(frozen=True)
class AccountLoginResult:
    """
    로그인 성공 결과임.
    """

    user: UserRecord
    session_token: str
    expires_at: str


def utc_now() -> datetime:
    """
    현재 UTC 시각을 반환함.
    """

    return datetime.now(
        timezone.utc
    )


def normalize_username(
    username: str,
) -> str:
    """
    아이디 앞뒤 공백을 제거하고 소문자로 통일함.
    """

    return username.strip().lower()


def validate_account_input(
    username: str,
    password: str,
    full_name: str,
) -> tuple[str, str]:
    """
    회원가입 입력값을 검증함.

    반환:
        정규화된 username
        정리된 full_name
    """

    normalized_username = normalize_username(
        username
    )
    normalized_full_name = full_name.strip()

    if not USERNAME_PATTERN.fullmatch(
        normalized_username
    ):
        raise ValueError(
            "아이디는 영문자·숫자·밑줄을 사용하여 "
            "4~32자로 입력해야 함."
        )

    validate_password_policy(password)

    if not normalized_full_name:
        raise ValueError(
            "이름이 필요함."
        )

    if len(normalized_full_name) > 64:
        raise ValueError(
            "이름은 64자를 넘을 수 없음."
        )

    return (
        normalized_username,
        normalized_full_name,
    )


def register_account(
    username: str,
    password: str,
    full_name: str,
    role: str = "USER",
) -> UserRecord:
    """
    사용자 또는 관리자 계정을 생성함.

    공개 회원가입 API는 role을 USER로 고정함.
    ADMIN은 CLI에서만 이 함수를 직접 호출함.
    """

    if role not in {"USER", "ADMIN"}:
        raise ValueError(
            "role은 USER 또는 ADMIN이어야 함."
        )

    (
        normalized_username,
        normalized_full_name,
    ) = validate_account_input(
        username=username,
        password=password,
        full_name=full_name,
    )

    generated_password_hash = (
        password_hash.hash(password)
    )

    try:
        return create_user(
            username=normalized_username,
            password_hash=(
                generated_password_hash
            ),
            full_name=normalized_full_name,
            role=role,
        )

    except sqlite3.IntegrityError as error:
        raise ValueError(
            "이미 사용 중인 아이디임."
        ) from error


def authenticate_account(
    username: str,
    password: str,
) -> AccountLoginResult:
    """
    아이디와 비밀번호를 검증하고
    8시간 로그인 세션을 생성함.
    """

    normalized_username = normalize_username(
        username
    )

    user = get_user_by_username(
        normalized_username
    )

    if user is None:
        # 존재하지 않는 사용자도 Argon2 검증을 한 번 수행하여
        # 응답시간 차이를 줄임.
        password_hash.verify(
            password,
            DUMMY_PASSWORD_HASH,
        )

        raise PermissionError(
            "아이디 또는 비밀번호가 올바르지 않음."
        )

    password_matches = password_hash.verify(
        password,
        user.password_hash,
    )

    if not password_matches:
        raise PermissionError(
            "아이디 또는 비밀번호가 올바르지 않음."
        )

    if not user.is_active:
        raise PermissionError(
            "비활성화된 계정임."
        )

    session_token = secrets.token_urlsafe(
        32
    )

    token_hash = hashlib.sha256(
        session_token.encode("utf-8")
    ).hexdigest()

    expires_at_datetime = (
        utc_now()
        + timedelta(
            hours=USER_SESSION_HOURS
        )
    )

    expires_at = (
        expires_at_datetime.isoformat()
    )

    create_user_session(
        token_hash=token_hash,
        user_id=user.id,
        expires_at=expires_at,
    )

    return AccountLoginResult(
        user=user,
        session_token=session_token,
        expires_at=expires_at,
    )


def authorize_user_session(
    session_token: str,
) -> UserRecord:
    """
    브라우저 로그인 세션을 검증하고
    현재 로그인 사용자를 반환함.
    """

    if not session_token:
        raise PermissionError(
            "로그인이 필요함."
        )

    token_hash = hashlib.sha256(
        session_token.encode("utf-8")
    ).hexdigest()

    session = get_user_session(
        token_hash
    )

    if session is None:
        raise PermissionError(
            "유효하지 않은 로그인 세션임."
        )

    if session.revoked_at is not None:
        raise PermissionError(
            "로그아웃된 세션임."
        )

    expires_at = datetime.fromisoformat(
        session.expires_at
    )

    if utc_now() >= expires_at:
        raise PermissionError(
            "로그인 세션이 만료되었음."
        )

    user = get_user_by_id(
        session.user_id
    )

    if user is None or not user.is_active:
        raise PermissionError(
            "사용할 수 없는 계정임."
        )

    return user


def logout_user_session(
    session_token: str,
) -> None:
    """
    원본 세션 토큰을 해시한 뒤 DB 세션을 폐기함.
    """

    if not session_token:
        return

    token_hash = hashlib.sha256(
        session_token.encode("utf-8")
    ).hexdigest()

    revoke_user_session(
        token_hash
    )


def assign_vehicle(
    username: str,
    vehicle_id: str,
    license_plate: str,
    display_name: str | None,
) -> None:
    """
    관리자가 차량을 생성하고 사용자에게 할당함.
    """

    user = get_user_by_username(
        normalize_username(username)
    )

    if user is None:
        raise ValueError(
            f"사용자를 찾을 수 없음: {username}"
        )

    normalized_vehicle_id = (
        vehicle_id.strip().upper()
    )
    normalized_license_plate = (
        license_plate.strip().upper()
    )

    if not normalized_vehicle_id:
        raise ValueError(
            "vehicle_id가 필요함."
        )

    if not normalized_license_plate:
        raise ValueError(
            "차량번호가 필요함."
        )

    upsert_vehicle(
        vehicle_id=normalized_vehicle_id,
        license_plate=(
            normalized_license_plate
        ),
        display_name=(
            None
            if display_name is None
            else display_name.strip()
        ),
    )

    assign_vehicle_to_user(
        user_id=user.id,
        vehicle_id=normalized_vehicle_id,
    )
