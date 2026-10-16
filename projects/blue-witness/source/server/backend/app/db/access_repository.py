from dataclasses import dataclass
from datetime import datetime
import sqlite3

from app.db.database import DATABASE_PATH


@dataclass(frozen=True)
class EventAccessRecord:
    """
    event_access 테이블 한 행을 표현함.

    사고 조회 코드 검증에 필요한 정보를 담음.
    """

    event_id: str
    code_hash: str
    expires_at: str
    failed_attempts: int
    locked_until: str | None
    revoked_at: str | None
    created_at: str


@dataclass(frozen=True)
class AccessSessionRecord:
    """
    access_sessions 테이블 한 행을 표현함.

    브라우저가 어떤 사고를 볼 수 있는지와
    세션 만료시각을 담음.
    """

    token_hash: str
    event_id: str
    expires_at: str
    revoked_at: str | None
    created_at: str


def connect_database() -> sqlite3.Connection:
    """
    SQLite DB에 연결함.

    row_factory를 설정하여 다음과 같이
    컬럼 이름으로 결과를 읽을 수 있게 함.

        row["event_id"]
    """

    connection = sqlite3.connect(
        DATABASE_PATH
    )
    connection.row_factory = sqlite3.Row

    connection.execute(
        "PRAGMA foreign_keys = ON"
    )

    return connection


def event_exists(event_id: str) -> bool:
    """
    조회 코드를 발급하려는 사고가
    events 테이블에 실제로 존재하는지 확인함.

    존재하지 않는 사고에 조회 코드가 만들어지는 것을 막음.
    """

    connection = connect_database()

    try:
        row = connection.execute(
            """
            SELECT 1
            FROM events
            WHERE event_id = ?
            LIMIT 1
            """,
            (event_id,),
        ).fetchone()

        return row is not None

    finally:
        connection.close()


def save_access_code(
    event_id: str,
    code_hash: str,
    expires_at: str,
) -> None:
    """
    사고 조회 코드의 해시와 만료시각을 저장함.

    이미 같은 event_id가 존재하면 다음 값을 갱신함.

    - code_hash
    - expires_at
    - 실패 횟수
    - 잠금 상태
    - 폐기 상태

    새 코드를 재발급하면 기존 코드는 즉시 무효가 됨.
    """

    connection = connect_database()

    try:
        connection.execute(
            """
            INSERT INTO event_access (
                event_id,
                code_hash,
                expires_at,
                failed_attempts,
                locked_until,
                revoked_at
            )
            VALUES (?, ?, ?, 0, NULL, NULL)

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
                event_id,
                code_hash,
                expires_at,
            ),
        )

        # 새 조회 코드를 발급하면 과거 세션도 폐기함.
        connection.execute(
            """
            UPDATE access_sessions
            SET revoked_at = CURRENT_TIMESTAMP
            WHERE event_id = ?
              AND revoked_at IS NULL
            """,
            (event_id,),
        )

        connection.commit()

    finally:
        connection.close()


def get_event_access(
    event_id: str,
) -> EventAccessRecord | None:
    """
    사고 ID에 연결된 조회 코드 정보를 가져옴.

    해당 사고에 조회 코드가 없으면 None을 반환함.
    """

    connection = connect_database()

    try:
        row = connection.execute(
            """
            SELECT
                event_id,
                code_hash,
                expires_at,
                failed_attempts,
                locked_until,
                revoked_at,
                created_at
            FROM event_access
            WHERE event_id = ?
            """,
            (event_id,),
        ).fetchone()

        if row is None:
            return None

        return EventAccessRecord(
            event_id=str(row["event_id"]),
            code_hash=str(row["code_hash"]),
            expires_at=str(row["expires_at"]),
            failed_attempts=int(
                row["failed_attempts"]
            ),
            locked_until=(
                None
                if row["locked_until"] is None
                else str(row["locked_until"])
            ),
            revoked_at=(
                None
                if row["revoked_at"] is None
                else str(row["revoked_at"])
            ),
            created_at=str(row["created_at"]),
        )

    finally:
        connection.close()


def record_failed_attempt(
    event_id: str,
    locked_until: str | None,
) -> None:
    """
    조회 코드가 틀렸을 때 실패 횟수를 증가시킴.

    다섯 번째 실패라면 locked_until도 함께 저장함.
    """

    connection = connect_database()

    try:
        connection.execute(
            """
            UPDATE event_access
            SET
                failed_attempts =
                    failed_attempts + 1,

                locked_until =
                    CASE
                        WHEN ? IS NOT NULL
                        THEN ?
                        ELSE locked_until
                    END
            WHERE event_id = ?
            """,
            (
                locked_until,
                locked_until,
                event_id,
            ),
        )

        connection.commit()

    finally:
        connection.close()


def reset_failed_attempts(
    event_id: str,
) -> None:
    """
    인증 성공 또는 잠금기간 종료 후
    실패 횟수와 잠금 상태를 초기화함.
    """

    connection = connect_database()

    try:
        connection.execute(
            """
            UPDATE event_access
            SET
                failed_attempts = 0,
                locked_until = NULL
            WHERE event_id = ?
            """,
            (event_id,),
        )

        connection.commit()

    finally:
        connection.close()


def create_access_session(
    token_hash: str,
    event_id: str,
    expires_at: str,
) -> None:
    """
    조회 코드 검증에 성공한 브라우저의
    세션 토큰 해시를 저장함.
    """

    connection = connect_database()

    try:
        connection.execute(
            """
            INSERT INTO access_sessions (
                token_hash,
                event_id,
                expires_at,
                revoked_at
            )
            VALUES (?, ?, ?, NULL)
            """,
            (
                token_hash,
                event_id,
                expires_at,
            ),
        )

        connection.commit()

    finally:
        connection.close()


def get_access_session(
    token_hash: str,
) -> AccessSessionRecord | None:
    """
    세션 토큰 해시로 세션 정보를 조회함.

    브라우저가 보낸 원본 토큰을 해시한 뒤
    이 함수로 DB 세션을 찾음.
    """

    connection = connect_database()

    try:
        row = connection.execute(
            """
            SELECT
                token_hash,
                event_id,
                expires_at,
                revoked_at,
                created_at
            FROM access_sessions
            WHERE token_hash = ?
            """,
            (token_hash,),
        ).fetchone()

        if row is None:
            return None

        return AccessSessionRecord(
            token_hash=str(row["token_hash"]),
            event_id=str(row["event_id"]),
            expires_at=str(row["expires_at"]),
            revoked_at=(
                None
                if row["revoked_at"] is None
                else str(row["revoked_at"])
            ),
            created_at=str(row["created_at"]),
        )

    finally:
        connection.close()
