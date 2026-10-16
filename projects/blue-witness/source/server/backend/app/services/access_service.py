from dataclasses import dataclass
from datetime import (
    datetime,
    timedelta,
    timezone,
)
import hashlib
import secrets

from app.db.access_repository import (
    create_access_session,
    event_exists,
    get_access_session,
    get_event_access,
    record_failed_attempt,
    reset_failed_attempts,
    save_access_code,
)


ACCESS_CODE_ALPHABET = (
    "ABCDEFGHJKMNPQRSTUVWXYZ"
    "23456789"
)

ACCESS_CODE_LENGTH = 12

MAX_FAILED_ATTEMPTS = 5

LOCK_DURATION_MINUTES = 10

SESSION_DURATION_MINUTES = 30


@dataclass(frozen=True)
class IssuedAccessCode:
    """
    새로 발급한 조회 코드 결과임.

    원본 access_code는 이 순간에만 반환하고
    DB에는 해시만 저장함.
    """

    event_id: str
    access_code: str
    expires_at: str


@dataclass(frozen=True)
class AuthenticatedSession:
    """
    조회 코드 검증에 성공한 결과임.

    session_token은 브라우저 쿠키에 저장할 원본 값임.
    DB에는 이 값의 해시만 저장됨.
    """

    event_id: str
    session_token: str
    expires_at: str


def utc_now() -> datetime:
    """
    현재 UTC 시각을 timezone-aware datetime으로 반환함.

    timezone-aware는 다음처럼 시간대 정보가
    포함된 datetime을 의미함.

    예:
        2026-07-31T12:00:00+00:00

    서버 운영체제의 지역 시간대와 관계없이
    만료시간 계산은 UTC를 기준으로 수행함.
    """

    return datetime.now(
        timezone.utc
    )


def datetime_to_text(
    value: datetime,
) -> str:
    """
    datetime을 SQLite에 저장할
    ISO 8601 문자열로 변환함.

    timezone이 없는 datetime이 전달되면
    UTC로 지정한 뒤 문자열로 변환함.

    예:
        2026-07-31T12:00:00+00:00
    """

    if value.tzinfo is None:
        value = value.replace(
            tzinfo=timezone.utc
        )

    normalized_value = (
        value.astimezone(
            timezone.utc
        )
    )

    return normalized_value.isoformat()


def text_to_datetime(
    value: str | datetime,
) -> datetime:
    """
    SQLite에서 읽은 시간값을
    UTC timezone-aware datetime으로 변환함.

    SQLite의 시간값에는 다음 두 형태가
    모두 들어올 수 있음.

    timezone이 포함된 값:
        2026-07-31T12:00:00+00:00

    timezone이 없는 값:
        2026-07-31 12:00:00

    Python에서는 timezone이 없는 datetime과
    timezone이 있는 datetime을 직접 비교할 수 없음.

    따라서 이 함수에서 모든 값을
    UTC timezone-aware datetime으로 통일함.
    """

    if isinstance(value, datetime):
        parsed_value = value

    elif isinstance(value, str):
        normalized_text = (
            value.strip()
        )

        if not normalized_text:
            raise ValueError(
                "비어 있는 시간 문자열임."
            )

        # ISO 8601에서 Z는 UTC를 의미함.
        # datetime.fromisoformat()이 일관되게
        # 처리할 수 있도록 +00:00으로 변경함.
        if normalized_text.endswith("Z"):
            normalized_text = (
                normalized_text[:-1]
                + "+00:00"
            )

        parsed_value = (
            datetime.fromisoformat(
                normalized_text
            )
        )

    else:
        raise TypeError(
            "시간값은 str 또는 "
            "datetime이어야 함."
        )

    # SQLite CURRENT_TIMESTAMP 또는
    # 과거 코드에서 저장한 값은
    # timezone 정보가 없을 수 있음.
    #
    # 프로젝트의 DB 시간은 UTC 기준이므로
    # naive datetime에 UTC를 지정함.
    if parsed_value.tzinfo is None:
        return parsed_value.replace(
            tzinfo=timezone.utc
        )

    # 이미 timezone이 있는 값은
    # 동일한 UTC 기준으로 변환함.
    return parsed_value.astimezone(
        timezone.utc
    )


def normalize_access_code(
    access_code: str,
) -> str:
    """
    사용자가 입력한 코드를
    비교 가능한 형태로 정규화함.

    다음 입력은 모두 같은 코드로 처리됨.

        A7K9-P2MX-Q4RD
        a7k9-p2mx-q4rd
        A7K9 P2MX Q4RD
    """

    return (
        access_code
        .replace("-", "")
        .replace(" ", "")
        .upper()
    )


def generate_access_code() -> str:
    """
    암호학적으로 안전한 무작위
    조회 코드를 생성함.

    혼동 문자를 제외한 문자 집합에서
    12자를 선택하고 4자리마다
    하이픈을 추가함.
    """

    raw_code = "".join(
        secrets.choice(
            ACCESS_CODE_ALPHABET
        )
        for _ in range(
            ACCESS_CODE_LENGTH
        )
    )

    groups = [
        raw_code[index:index + 4]
        for index in range(
            0,
            len(raw_code),
            4,
        )
    ]

    return "-".join(groups)


def hash_secret(
    secret_value: str,
) -> str:
    """
    조회 코드 또는 세션 토큰을
    SHA-256으로 해시함.

    DB 유출 시 원본 코드와 세션 토큰이
    바로 노출되는 것을 방지함.
    """

    return hashlib.sha256(
        secret_value.encode("utf-8")
    ).hexdigest()


def hash_access_code(
    access_code: str,
) -> str:
    """
    조회 코드를 정규화한 다음 해시함.

    저장할 때와 검증할 때 반드시
    같은 정규화 과정을 사용해야 함.
    """

    normalized_code = (
        normalize_access_code(
            access_code
        )
    )

    return hash_secret(
        normalized_code
    )


def issue_access_code(
    event_id: str,
    valid_hours: float = 24.0,
) -> IssuedAccessCode:
    """
    기존 사고에 새로운 조회 코드를 발급함.

    처리 순서:
    1. events에 사고가 존재하는지 확인함.
    2. 12자리 무작위 코드를 생성함.
    3. 코드 해시를 계산함.
    4. UTC 기준 만료시각을 계산함.
    5. DB에 해시와 만료시각을 저장함.
    6. 원본 코드는 운영자에게 한 번 반환함.
    """

    if not event_id:
        raise ValueError(
            "event_id가 필요함."
        )

    if valid_hours <= 0:
        raise ValueError(
            "valid_hours는 0보다 커야 함."
        )

    if not event_exists(event_id):
        raise ValueError(
            f"events 테이블에 사고가 없음: "
            f"{event_id}"
        )

    access_code = generate_access_code()

    code_hash = hash_access_code(
        access_code
    )

    expires_at_datetime = (
        utc_now()
        + timedelta(
            hours=valid_hours
        )
    )

    expires_at = datetime_to_text(
        expires_at_datetime
    )

    save_access_code(
        event_id=event_id,
        code_hash=code_hash,
        expires_at=expires_at,
    )

    return IssuedAccessCode(
        event_id=event_id,
        access_code=access_code,
        expires_at=expires_at,
    )


def authenticate_access_code(
    event_id: str,
    entered_access_code: str,
) -> AuthenticatedSession:
    """
    사용자가 입력한 조회 코드를 검증하고
    성공하면 30분 세션을 생성함.

    검증 내용:
    1. 사고 접근 코드 존재 여부
    2. 코드 폐기 여부
    3. 코드 만료 여부
    4. 입력 차단 여부
    5. 코드 해시 일치 여부
    """

    current_time = utc_now()

    access_record = get_event_access(
        event_id
    )

    if access_record is None:
        raise PermissionError(
            "사고 ID 또는 조회 코드가 "
            "올바르지 않음."
        )

    if access_record.revoked_at is not None:
        raise PermissionError(
            "사고 ID 또는 조회 코드가 "
            "올바르지 않음."
        )

    # DB의 만료시각을 UTC timezone-aware
    # datetime으로 변환함.
    expires_at = text_to_datetime(
        access_record.expires_at
    )

    # current_time과 expires_at 모두
    # UTC timezone-aware이므로 안전하게 비교됨.
    if current_time >= expires_at:
        raise PermissionError(
            "조회 코드가 만료되었음."
        )

    if (
        access_record.locked_until
        is not None
    ):
        # 입력 차단시간도 동일하게
        # UTC timezone-aware로 변환함.
        locked_until = text_to_datetime(
            access_record.locked_until
        )

        if current_time < locked_until:
            raise PermissionError(
                "조회 코드 입력이 잠시 차단됨."
            )

        # 잠금기간이 지났다면
        # 실패 기록을 초기화함.
        reset_failed_attempts(
            event_id
        )

        current_failed_attempts = 0

    else:
        current_failed_attempts = (
            access_record.failed_attempts
        )

    entered_code_hash = (
        hash_access_code(
            entered_access_code
        )
    )

    code_matches = (
        secrets.compare_digest(
            entered_code_hash,
            access_record.code_hash,
        )
    )

    if not code_matches:
        new_failed_attempts = (
            current_failed_attempts
            + 1
        )

        locked_until_text = None

        if (
            new_failed_attempts
            >= MAX_FAILED_ATTEMPTS
        ):
            locked_until_datetime = (
                current_time
                + timedelta(
                    minutes=(
                        LOCK_DURATION_MINUTES
                    )
                )
            )

            locked_until_text = (
                datetime_to_text(
                    locked_until_datetime
                )
            )

        record_failed_attempt(
            event_id=event_id,
            locked_until=(
                locked_until_text
            ),
        )

        raise PermissionError(
            "사고 ID 또는 조회 코드가 "
            "올바르지 않음."
        )

    # 코드 검증에 성공했으므로
    # 기존 실패 횟수와 잠금 상태를 초기화함.
    reset_failed_attempts(
        event_id
    )

    # URL과 쿠키에 안전한 긴 무작위
    # 세션 토큰을 생성함.
    session_token = (
        secrets.token_urlsafe(
            32
        )
    )

    # 원본 세션 토큰은 브라우저에 전달하고
    # DB에는 해시만 저장함.
    session_token_hash = (
        hash_secret(
            session_token
        )
    )

    session_expires_at_datetime = (
        current_time
        + timedelta(
            minutes=(
                SESSION_DURATION_MINUTES
            )
        )
    )

    session_expires_at = (
        datetime_to_text(
            session_expires_at_datetime
        )
    )

    create_access_session(
        token_hash=session_token_hash,
        event_id=event_id,
        expires_at=session_expires_at,
    )

    return AuthenticatedSession(
        event_id=event_id,
        session_token=session_token,
        expires_at=session_expires_at,
    )


def authorize_session(
    session_token: str,
) -> str:
    """
    브라우저 쿠키의 사고 접근
    세션 토큰을 검증함.

    성공:
        접근 가능한 event_id를 반환함.

    실패:
        PermissionError를 발생시킴.
    """

    if not session_token:
        raise PermissionError(
            "인증 세션이 없음."
        )

    token_hash = hash_secret(
        session_token
    )

    session_record = (
        get_access_session(
            token_hash
        )
    )

    if session_record is None:
        raise PermissionError(
            "유효하지 않은 인증 세션임."
        )

    if (
        session_record.revoked_at
        is not None
    ):
        raise PermissionError(
            "폐기된 인증 세션임."
        )

    # 세션 만료시각도 UTC timezone-aware
    # datetime으로 변환함.
    expires_at = text_to_datetime(
        session_record.expires_at
    )

    current_time = utc_now()

    if current_time >= expires_at:
        raise PermissionError(
            "인증 세션이 만료되었음."
        )

    access_record = get_event_access(
        session_record.event_id
    )

    if (
        access_record is None
        or access_record.revoked_at
        is not None
    ):
        raise PermissionError(
            "사고 접근 권한이 폐기되었음."
        )

    return session_record.event_id
