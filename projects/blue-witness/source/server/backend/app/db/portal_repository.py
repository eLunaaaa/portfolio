from dataclasses import dataclass
import sqlite3

from app.db.database import DATABASE_PATH


@dataclass(frozen=True)
class EventRecord:
    """
    관리자 또는 사용자 화면에서 보여줄
    사고 한 건의 DB 조회 결과임.
    """

    event_id: str
    accident_vehicle_id: str
    event_time_ns: int
    evidence_count: int
    evidence_vehicle_ids: tuple[str, ...]
    latest_evidence_received_at: str | None


@dataclass(frozen=True)
class NodeRecord:
    """
    차량 노드의 마지막 Heartbeat 정보임.
    """

    vehicle_id: str
    ip_address: str | None
    software_version: str | None
    last_seen_at: str
    created_at: str


@dataclass(frozen=True)
class AdminUserRecord:
    """
    관리자 화면에서 보여줄 사용자 요약 정보임.
    """

    id: int
    username: str
    full_name: str
    role: str
    is_active: bool
    vehicle_count: int
    created_at: str


def connect_database() -> sqlite3.Connection:
    """
    SQLite DB에 연결함.

    외래키 검사를 활성화하고
    조회 결과를 컬럼 이름으로 읽게 함.
    """

    connection = sqlite3.connect(
        DATABASE_PATH
    )
    connection.row_factory = sqlite3.Row

    connection.execute(
        "PRAGMA foreign_keys = ON"
    )

    return connection


def get_table_columns(
    connection: sqlite3.Connection,
    table_name: str,
) -> set[str]:
    """
    실제 테이블의 컬럼 목록을 조회함.

    과거 DB 버전마다 사고 시각 컬럼명이
    달라질 가능성에 대비하여 사용함.
    """

    rows = connection.execute(
        f"PRAGMA table_info({table_name})"
    ).fetchall()

    return {
        str(row["name"])
        for row in rows
    }


def find_event_time_column(
    event_columns: set[str],
) -> str:
    """
    events 테이블에서 사고 시각 컬럼을 찾음.

    현재는 event_time_ns가 기본이지만
    이전 이름도 확인함.
    """

    candidates = (
        "event_time_ns",
        "accident_time_ns",
        "event_timestamp_ns",
        "event_time_utc_ns",
    )

    for candidate in candidates:
        if candidate in event_columns:
            return candidate

    raise RuntimeError(
        "events 테이블에서 사고 시각 컬럼을 "
        f"찾을 수 없음: {sorted(event_columns)}"
    )


def validate_event_schema(
    connection: sqlite3.Connection,
) -> str:
    """
    사고 목록 조회에 필요한 events 컬럼을 검증하고
    사용할 사고 시각 컬럼명을 반환함.

    코드와 DB 스키마가 맞지 않을 때
    복잡한 SQL 오류 대신 명확한 메시지를 냄.
    """

    event_columns = get_table_columns(
        connection,
        "events",
    )

    required_columns = {
        "event_id",
        "accident_vehicle_id",
    }

    missing_columns = (
        required_columns - event_columns
    )

    if missing_columns:
        raise RuntimeError(
            "events 테이블에 필수 컬럼이 없음: "
            f"{sorted(missing_columns)}"
        )

    return find_event_time_column(
        event_columns
    )


def split_vehicle_ids(
    value: str | None,
) -> tuple[str, ...]:
    """
    SQLite GROUP_CONCAT 결과를 차량 ID 튜플로 변환함.

    DB 결과:
        CAR_02,CAR_03,CAR_04

    Python 결과:
        ("CAR_02", "CAR_03", "CAR_04")
    """

    if value is None or not value.strip():
        return ()

    return tuple(
        sorted(
            {
                vehicle_id.strip()
                for vehicle_id in value.split(",")
                if vehicle_id.strip()
            }
        )
    )


def row_to_event(
    row: sqlite3.Row,
) -> EventRecord:
    """
    사고 SQL 조회 결과를 EventRecord로 변환함.

    관리자 조회와 사용자 조회에서
    동일한 변환 코드를 사용하기 위해 분리함.
    """

    return EventRecord(
        event_id=str(row["event_id"]),
        accident_vehicle_id=str(
            row["accident_vehicle_id"]
        ),
        event_time_ns=int(
            row["event_time_ns"]
        ),
        evidence_count=int(
            row["evidence_count"]
        ),
        evidence_vehicle_ids=split_vehicle_ids(
            row["evidence_vehicle_ids"]
        ),
        latest_evidence_received_at=(
            None
            if row[
                "latest_evidence_received_at"
            ] is None
            else str(
                row[
                    "latest_evidence_received_at"
                ]
            )
        ),
    )


def list_all_events() -> list[EventRecord]:
    """
    관리자용 전체 사고 목록을 조회함.

    사고별로 다음 정보를 집계함.

    - 사고 차량
    - 사고 시각
    - 증거 영상 전송 차량 수
    - 참여 차량 ID
    - 가장 최근 영상 수신시각
    """

    connection = connect_database()

    try:
        event_time_column = (
            validate_event_schema(connection)
        )

        query = f"""
            SELECT
                ev.event_id,
                ev.accident_vehicle_id,

                ev.{event_time_column}
                    AS event_time_ns,

                COUNT(
                    DISTINCT e.vehicle_id
                ) AS evidence_count,

                GROUP_CONCAT(
                    DISTINCT e.vehicle_id
                ) AS evidence_vehicle_ids,

                MAX(
                    e.received_at
                ) AS latest_evidence_received_at

            FROM events AS ev

            LEFT JOIN evidence AS e
              ON e.event_id = ev.event_id

            GROUP BY
                ev.event_id,
                ev.accident_vehicle_id,
                ev.{event_time_column}

            ORDER BY
                ev.{event_time_column} DESC
        """

        rows = connection.execute(
            query
        ).fetchall()

        return [
            row_to_event(row)
            for row in rows
        ]

    finally:
        connection.close()


def list_user_events(
    user_id: int,
) -> list[EventRecord]:
    """
    로그인 사용자가 소유한 차량에서 발생한
    사고만 조회함.

    핵심 권한 조건:

        user_vehicles.vehicle_id
        =
        events.accident_vehicle_id

    목격 차량으로 영상을 보낸 차량을 소유했다고 해서
    사고 당사자의 영상을 볼 수 있는 것은 아님.
    """

    connection = connect_database()

    try:
        event_time_column = (
            validate_event_schema(connection)
        )

        query = f"""
            SELECT
                ev.event_id,
                ev.accident_vehicle_id,

                ev.{event_time_column}
                    AS event_time_ns,

                COUNT(
                    DISTINCT e.vehicle_id
                ) AS evidence_count,

                GROUP_CONCAT(
                    DISTINCT e.vehicle_id
                ) AS evidence_vehicle_ids,

                MAX(
                    e.received_at
                ) AS latest_evidence_received_at

            FROM events AS ev

            JOIN user_vehicles AS uv
              ON uv.vehicle_id =
                 ev.accident_vehicle_id

            LEFT JOIN evidence AS e
              ON e.event_id = ev.event_id

            WHERE uv.user_id = ?

            GROUP BY
                ev.event_id,
                ev.accident_vehicle_id,
                ev.{event_time_column}

            ORDER BY
                ev.{event_time_column} DESC
        """

        rows = connection.execute(
            query,
            (user_id,),
        ).fetchall()

        return [
            row_to_event(row)
            for row in rows
        ]

    finally:
        connection.close()


def get_user_event(
    user_id: int,
    event_id: str,
) -> EventRecord | None:
    """
    특정 사고가 로그인 사용자의 차량 사고인지 확인함.

    조건을 만족하지 않으면 None을 반환함.

    이후 8-3 영상 API에서도 이 함수를 사용하여
    URL의 event_id만 바꿔 다른 사고 영상을
    보는 문제를 막음.
    """

    connection = connect_database()

    try:
        event_time_column = (
            validate_event_schema(connection)
        )

        query = f"""
            SELECT
                ev.event_id,
                ev.accident_vehicle_id,

                ev.{event_time_column}
                    AS event_time_ns,

                COUNT(
                    DISTINCT e.vehicle_id
                ) AS evidence_count,

                GROUP_CONCAT(
                    DISTINCT e.vehicle_id
                ) AS evidence_vehicle_ids,

                MAX(
                    e.received_at
                ) AS latest_evidence_received_at

            FROM events AS ev

            JOIN user_vehicles AS uv
              ON uv.vehicle_id =
                 ev.accident_vehicle_id

            LEFT JOIN evidence AS e
              ON e.event_id = ev.event_id

            WHERE uv.user_id = ?
              AND ev.event_id = ?

            GROUP BY
                ev.event_id,
                ev.accident_vehicle_id,
                ev.{event_time_column}
        """

        row = connection.execute(
            query,
            (
                user_id,
                event_id,
            ),
        ).fetchone()

        if row is None:
            return None

        return row_to_event(row)

    finally:
        connection.close()


def record_node_heartbeat(
    vehicle_id: str,
    ip_address: str | None,
    software_version: str | None,
    last_seen_at: str,
) -> None:
    """
    Raspberry Pi가 보낸 Heartbeat를 저장함.

    처음 접속한 차량:
        새 행 생성함.

    기존 차량:
        IP, 버전, 마지막 접속시각 갱신함.
    """

    connection = connect_database()

    try:
        connection.execute(
            """
            INSERT INTO vehicle_nodes (
                vehicle_id,
                ip_address,
                software_version,
                last_seen_at
            )
            VALUES (?, ?, ?, ?)

            ON CONFLICT(vehicle_id)
            DO UPDATE SET
                ip_address =
                    excluded.ip_address,

                software_version =
                    excluded.software_version,

                last_seen_at =
                    excluded.last_seen_at
            """,
            (
                vehicle_id,
                ip_address,
                software_version,
                last_seen_at,
            ),
        )

        connection.commit()

    finally:
        connection.close()


def list_vehicle_nodes() -> list[NodeRecord]:
    """
    관리자 화면에 표시할 모든 차량 노드의
    마지막 Heartbeat 정보를 조회함.

    이 함수는 ONLINE 여부를 판단하지 않음.
    ONLINE 판단은 현재 시각이 필요한 Service가 담당함.
    """

    connection = connect_database()

    try:
        rows = connection.execute(
            """
            SELECT
                vehicle_id,
                ip_address,
                software_version,
                last_seen_at,
                created_at
            FROM vehicle_nodes
            ORDER BY vehicle_id
            """
        ).fetchall()

        return [
            NodeRecord(
                vehicle_id=str(
                    row["vehicle_id"]
                ),
                ip_address=(
                    None
                    if row["ip_address"] is None
                    else str(row["ip_address"])
                ),
                software_version=(
                    None
                    if row["software_version"]
                    is None
                    else str(
                        row["software_version"]
                    )
                ),
                last_seen_at=str(
                    row["last_seen_at"]
                ),
                created_at=str(
                    row["created_at"]
                ),
            )
            for row in rows
        ]

    finally:
        connection.close()


def list_admin_users() -> list[AdminUserRecord]:
    """
    관리자 화면용 사용자 목록을 조회함.

    사용자별 할당 차량 수를 함께 계산함.
    비밀번호 해시는 절대 조회하지 않음.
    """

    connection = connect_database()

    try:
        rows = connection.execute(
            """
            SELECT
                u.id,
                u.username,
                u.full_name,
                u.role,
                u.is_active,
                u.created_at,

                COUNT(
                    DISTINCT uv.vehicle_id
                ) AS vehicle_count

            FROM users AS u

            LEFT JOIN user_vehicles AS uv
              ON uv.user_id = u.id

            GROUP BY
                u.id,
                u.username,
                u.full_name,
                u.role,
                u.is_active,
                u.created_at

            ORDER BY u.id
            """
        ).fetchall()

        return [
            AdminUserRecord(
                id=int(row["id"]),
                username=str(
                    row["username"]
                ),
                full_name=str(
                    row["full_name"]
                ),
                role=str(row["role"]),
                is_active=bool(
                    row["is_active"]
                ),
                vehicle_count=int(
                    row["vehicle_count"]
                ),
                created_at=str(
                    row["created_at"]
                ),
            )
            for row in rows
        ]

    finally:
        connection.close()
