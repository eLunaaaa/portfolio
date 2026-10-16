from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import sqlite3

from app.db.database import DATABASE_PATH


@dataclass(frozen=True)
class ActiveAccessSession:
    """
    유효한 사고 접근 세션 정보임.
    """

    event_id: str
    expires_at: datetime


class EventAccessDeniedError(Exception):
    """
    사고 접근 세션이 없거나 유효하지 않을 때
    발생시키는 예외임.
    """


def connect_database() -> sqlite3.Connection:
    """
    사고 접근 세션을 확인할 SQLite DB를 엶.
    """

    if not DATABASE_PATH.exists():
        raise RuntimeError(
            "SQLite DB를 찾을 수 없음: "
            f"{DATABASE_PATH}"
        )

    connection = sqlite3.connect(
        DATABASE_PATH
    )

    connection.row_factory = sqlite3.Row

    connection.execute(
        "PRAGMA foreign_keys = ON"
    )

    connection.execute(
        "PRAGMA busy_timeout = 5000"
    )

    return connection


def hash_session_token(
    raw_token: str,
) -> str:
    """
    브라우저 쿠키의 원본 세션 토큰을
    SHA-256 해시 문자열로 변환함.
    """

    return sha256(
        raw_token.encode("utf-8")
    ).hexdigest()


def parse_database_datetime(
    value: str,
) -> datetime:
    """
    SQLite에 저장된 날짜 문자열을
    UTC datetime으로 변환함.
    """

    normalized_value = value.replace(
        "Z",
        "+00:00",
    )

    parsed_value = datetime.fromisoformat(
        normalized_value
    )

    if parsed_value.tzinfo is None:
        parsed_value = parsed_value.replace(
            tzinfo=timezone.utc
        )

    return parsed_value.astimezone(
        timezone.utc
    )


def find_active_access_session(
    raw_session_token: str | None,
) -> ActiveAccessSession | None:
    """
    쿠키로 전달된 사고 접근 세션을 조회함.

    다음 조건을 모두 만족해야 유효함.

    1. 쿠키가 존재함
    2. DB에 동일한 token_hash가 존재함
    3. revoked_at이 NULL임
    4. expires_at이 현재보다 미래임
    """

    if not raw_session_token:
        return None

    token_hash = hash_session_token(
        raw_session_token
    )

    with connect_database() as connection:
        row = connection.execute(
            """
            SELECT
                event_id,
                expires_at
            FROM access_sessions
            WHERE token_hash = ?
              AND revoked_at IS NULL
            """,
            (token_hash,),
        ).fetchone()

    if row is None:
        return None

    expires_at = parse_database_datetime(
        row["expires_at"]
    )

    if expires_at <= datetime.now(
        timezone.utc
    ):
        return None

    return ActiveAccessSession(
        event_id=row["event_id"],
        expires_at=expires_at,
    )


def has_event_access(
    raw_session_token: str | None,
    event_id: str,
) -> ActiveAccessSession | None:
    """
    현재 사고 접근 세션이 요청한 event_id와
    일치하는지 검사함.

    일치하면 세션 정보를 반환하고,
    일치하지 않으면 None을 반환함.
    """

    session = find_active_access_session(
        raw_session_token
    )

    if session is None:
        return None

    if session.event_id != event_id:
        return None

    return session


def require_event_access(
    raw_session_token: str | None,
    event_id: str,
) -> ActiveAccessSession:
    """
    요청한 사고에 대한 유효한 접근 세션을 강제함.

    접근 권한이 없으면
    EventAccessDeniedError를 발생시킴.
    """

    session = has_event_access(
        raw_session_token,
        event_id,
    )

    if session is None:
        raise EventAccessDeniedError(
            "사고 접근 코드 확인이 필요하거나 "
            "접근 세션이 만료됨."
        )

    return session
