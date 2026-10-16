from dataclasses import dataclass
import sqlite3

from app.db.database import DATABASE_PATH


@dataclass(frozen=True)
class UserRecord:
    """
    users 테이블의 사용자 한 명을 나타냄.

    API와 Service가 SQLite Row를 직접 다루지 않고
    명확한 Python 객체를 사용하도록 함.
    """

    id: int
    username: str
    password_hash: str
    full_name: str
    role: str
    is_active: bool
    created_at: str


@dataclass(frozen=True)
class UserSessionRecord:
    """
    로그인 세션 한 개를 나타냄.
    """

    token_hash: str
    user_id: int
    expires_at: str
    revoked_at: str | None
    created_at: str


@dataclass(frozen=True)
class VehicleRecord:
    """
    차량 한 대와 차량번호 정보를 나타냄.
    """

    vehicle_id: str
    license_plate: str
    display_name: str | None
    created_at: str


def connect_database() -> sqlite3.Connection:
    """
    SQLite에 연결하고 외래키 검사를 활성화함.
    """

    connection = sqlite3.connect(
        DATABASE_PATH
    )
    connection.row_factory = sqlite3.Row

    connection.execute(
        "PRAGMA foreign_keys = ON"
    )

    return connection


def row_to_user(
    row: sqlite3.Row,
) -> UserRecord:
    """
    SQLite Row를 UserRecord로 변환함.

    여러 조회 함수가 동일한 변환 코드를
    반복하지 않도록 분리함.
    """

    return UserRecord(
        id=int(row["id"]),
        username=str(row["username"]),
        password_hash=str(
            row["password_hash"]
        ),
        full_name=str(row["full_name"]),
        role=str(row["role"]),
        is_active=bool(row["is_active"]),
        created_at=str(row["created_at"]),
    )


def create_user(
    username: str,
    password_hash: str,
    full_name: str,
    role: str,
) -> UserRecord:
    """
    새로운 사용자를 DB에 저장함.

    username에 UNIQUE가 걸려 있으므로
    같은 아이디를 다시 만들면 IntegrityError가 발생함.
    """

    connection = connect_database()

    try:
        cursor = connection.execute(
            """
            INSERT INTO users (
                username,
                password_hash,
                full_name,
                role,
                is_active
            )
            VALUES (?, ?, ?, ?, 1)
            """,
            (
                username,
                password_hash,
                full_name,
                role,
            ),
        )

        connection.commit()

        user_id = int(cursor.lastrowid)

        row = connection.execute(
            """
            SELECT
                id,
                username,
                password_hash,
                full_name,
                role,
                is_active,
                created_at
            FROM users
            WHERE id = ?
            """,
            (user_id,),
        ).fetchone()

        if row is None:
            raise RuntimeError(
                "생성된 사용자를 다시 조회하지 못함."
            )

        return row_to_user(row)

    finally:
        connection.close()


def get_user_by_username(
    username: str,
) -> UserRecord | None:
    """
    로그인 아이디로 사용자를 조회함.
    """

    connection = connect_database()

    try:
        row = connection.execute(
            """
            SELECT
                id,
                username,
                password_hash,
                full_name,
                role,
                is_active,
                created_at
            FROM users
            WHERE username = ?
            """,
            (username,),
        ).fetchone()

        if row is None:
            return None

        return row_to_user(row)

    finally:
        connection.close()


def get_user_by_id(
    user_id: int,
) -> UserRecord | None:
    """
    세션의 user_id를 이용해 사용자를 조회함.
    """

    connection = connect_database()

    try:
        row = connection.execute(
            """
            SELECT
                id,
                username,
                password_hash,
                full_name,
                role,
                is_active,
                created_at
            FROM users
            WHERE id = ?
            """,
            (user_id,),
        ).fetchone()

        if row is None:
            return None

        return row_to_user(row)

    finally:
        connection.close()


def create_user_session(
    token_hash: str,
    user_id: int,
    expires_at: str,
) -> None:
    """
    로그인 세션 토큰의 해시를 저장함.
    """

    connection = connect_database()

    try:
        connection.execute(
            """
            INSERT INTO user_sessions (
                token_hash,
                user_id,
                expires_at,
                revoked_at
            )
            VALUES (?, ?, ?, NULL)
            """,
            (
                token_hash,
                user_id,
                expires_at,
            ),
        )

        connection.commit()

    finally:
        connection.close()


def get_user_session(
    token_hash: str,
) -> UserSessionRecord | None:
    """
    토큰 해시로 로그인 세션을 조회함.
    """

    connection = connect_database()

    try:
        row = connection.execute(
            """
            SELECT
                token_hash,
                user_id,
                expires_at,
                revoked_at,
                created_at
            FROM user_sessions
            WHERE token_hash = ?
            """,
            (token_hash,),
        ).fetchone()

        if row is None:
            return None

        return UserSessionRecord(
            token_hash=str(row["token_hash"]),
            user_id=int(row["user_id"]),
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


def revoke_user_session(
    token_hash: str,
) -> None:
    """
    로그아웃 시 DB 세션을 폐기함.
    """

    connection = connect_database()

    try:
        connection.execute(
            """
            UPDATE user_sessions
            SET revoked_at = CURRENT_TIMESTAMP
            WHERE token_hash = ?
              AND revoked_at IS NULL
            """,
            (token_hash,),
        )

        connection.commit()

    finally:
        connection.close()


def upsert_vehicle(
    vehicle_id: str,
    license_plate: str,
    display_name: str | None,
) -> None:
    """
    차량이 없으면 생성하고,
    이미 있으면 차량번호와 표시 이름을 갱신함.
    """

    connection = connect_database()

    try:
        connection.execute(
            """
            INSERT INTO vehicles (
                vehicle_id,
                license_plate,
                display_name
            )
            VALUES (?, ?, ?)

            ON CONFLICT(vehicle_id)
            DO UPDATE SET
                license_plate =
                    excluded.license_plate,
                display_name =
                    excluded.display_name
            """,
            (
                vehicle_id,
                license_plate,
                display_name,
            ),
        )

        connection.commit()

    finally:
        connection.close()


def assign_vehicle_to_user(
    user_id: int,
    vehicle_id: str,
) -> None:
    """
    사용자와 차량의 소유 관계를 저장함.

    같은 관계가 이미 있으면 중복 생성하지 않음.
    """

    connection = connect_database()

    try:
        connection.execute(
            """
            INSERT OR IGNORE INTO user_vehicles (
                user_id,
                vehicle_id
            )
            VALUES (?, ?)
            """,
            (
                user_id,
                vehicle_id,
            ),
        )

        connection.commit()

    finally:
        connection.close()


def list_user_vehicles(
    user_id: int,
) -> list[VehicleRecord]:
    """
    사용자가 소유한 차량 목록을 조회함.
    """

    connection = connect_database()

    try:
        rows = connection.execute(
            """
            SELECT
                v.vehicle_id,
                v.license_plate,
                v.display_name,
                v.created_at
            FROM vehicles AS v
            JOIN user_vehicles AS uv
              ON uv.vehicle_id = v.vehicle_id
            WHERE uv.user_id = ?
            ORDER BY v.vehicle_id
            """,
            (user_id,),
        ).fetchall()

        return [
            VehicleRecord(
                vehicle_id=str(
                    row["vehicle_id"]
                ),
                license_plate=str(
                    row["license_plate"]
                ),
                display_name=(
                    None
                    if row["display_name"] is None
                    else str(row["display_name"])
                ),
                created_at=str(
                    row["created_at"]
                ),
            )
            for row in rows
        ]

    finally:
        connection.close()