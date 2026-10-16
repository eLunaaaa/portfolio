from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import secrets
import sqlite3

from app.db.database import DATABASE_PATH


ACCESS_CODE_ALPHABET = (
    "ABCDEFGHJKLMNPQRSTUVWXYZ"
    "23456789"
)

DEFAULT_ACCESS_CODE_MINUTES = 5


@dataclass(frozen=True)
class IssuedAccessCode:
    event_id: str
    access_code: str
    expires_at: str
    valid_for_seconds: int


def connect_database() -> sqlite3.Connection:
    """
    접근 코드 정보를 저장하기 위한
    SQLite 연결을 생성함.
    """

    connection = sqlite3.connect(
        DATABASE_PATH,
        timeout=5.0,
    )

    connection.row_factory = sqlite3.Row

    connection.execute(
        "PRAGMA foreign_keys = ON",
    )

    connection.execute(
        "PRAGMA busy_timeout = 5000",
    )

    return connection


def generate_access_code() -> str:
    """
    ABCD-EFGH-2345 형식의
    무작위 접근 코드를 생성함.
    """

    groups = []

    for _ in range(3):
        group = "".join(
            secrets.choice(
                ACCESS_CODE_ALPHABET,
            )
            for _ in range(4)
        )

        groups.append(group)

    return "-".join(groups)


def normalize_access_code(
    access_code: str,
) -> str:
    """
    코드 앞뒤 공백을 제거하고
    대문자로 통일함.
    """

    return access_code.strip().upper()


def hash_access_code(
    access_code: str,
) -> str:
    """
    접근 코드 원문 대신 DB에 저장할
    SHA-256 해시를 생성함.
    """

    normalized_code = normalize_access_code(
        access_code,
    )

    return hashlib.sha256(
        normalized_code.encode("utf-8"),
    ).hexdigest()


def format_database_datetime(
    value: datetime,
) -> str:
    """
    SQLite에 저장할 UTC 시각 문자열로 변환함.
    """

    return value.astimezone(
        timezone.utc,
    ).strftime(
        "%Y-%m-%d %H:%M:%S",
    )


def issue_access_code_for_event(
    event_id: str,
    valid_minutes: int = DEFAULT_ACCESS_CODE_MINUTES,
) -> IssuedAccessCode:
    """
    사고 접근 코드를 발급하고,
    해당 코드의 해시와 만료 시각을 DB에 저장함.

    같은 사고에 코드를 다시 발급하면
    이전 코드와 기존 접근 세션은 무효화됨.
    """

    clean_event_id = event_id.strip()

    if not clean_event_id:
        raise ValueError(
            "사고 ID가 비어 있음.",
        )

    if valid_minutes < 1:
        raise ValueError(
            "접근 코드 유효시간은 "
            "1분 이상이어야 함.",
        )

    if valid_minutes > 60:
        raise ValueError(
            "접근 코드 유효시간은 "
            "60분을 초과할 수 없음.",
        )

    access_code = generate_access_code()

    code_hash = hash_access_code(
        access_code,
    )

    valid_for_seconds = (
        valid_minutes * 60
    )

    expires_at_datetime = (
        datetime.now(timezone.utc)
        + timedelta(minutes=valid_minutes)
    )

    expires_at = format_database_datetime(
        expires_at_datetime,
    )

    connection = connect_database()

    try:
        connection.execute(
            "BEGIN IMMEDIATE",
        )

        event_row = connection.execute(
            """
            SELECT event_id
            FROM events
            WHERE event_id = ?
            """,
            (clean_event_id,),
        ).fetchone()

        if event_row is None:
            raise ValueError(
                "접근 코드를 발급할 사고가 없음.",
            )

        connection.execute(
            """
            INSERT INTO event_access (
                event_id,
                code_hash,
                expires_at,
                failed_attempts,
                locked_until,
                revoked_at,
                created_at
            )
            VALUES (
                ?,
                ?,
                ?,
                0,
                NULL,
                NULL,
                CURRENT_TIMESTAMP
            )
            ON CONFLICT(event_id)
            DO UPDATE SET
                code_hash = excluded.code_hash,
                expires_at = excluded.expires_at,
                failed_attempts = 0,
                locked_until = NULL,
                revoked_at = NULL,
                created_at = CURRENT_TIMESTAMP
            """,
            (
                clean_event_id,
                code_hash,
                expires_at,
            ),
        )

        connection.execute(
            """
            UPDATE access_sessions
            SET revoked_at = CURRENT_TIMESTAMP
            WHERE event_id = ?
              AND revoked_at IS NULL
            """,
            (clean_event_id,),
        )

        connection.commit()

    except Exception:
        connection.rollback()
        raise

    finally:
        connection.close()

    return IssuedAccessCode(
        event_id=clean_event_id,
        access_code=access_code,
        expires_at=expires_at,
        valid_for_seconds=valid_for_seconds,
    )