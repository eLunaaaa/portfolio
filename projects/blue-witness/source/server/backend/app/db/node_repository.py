from dataclasses import dataclass
import sqlite3

from app.db.database import (
    get_database_connection,
)


@dataclass(frozen=True)
class NodeCredentialRecord:
    """
    DB에서 조회한 Pi 노드 인증 정보임.

    API Key 원문은 포함하지 않고
    해시값만 포함함.
    """

    node_id: str
    vehicle_id: str

    api_key_hash: str
    api_key_last4: str

    is_active: bool

    last_authenticated_at: (
        str | None
    )

    created_at: str
    updated_at: str


def row_to_node_credential(
    row: sqlite3.Row,
) -> NodeCredentialRecord:
    """
    SQLite Row를 NodeCredentialRecord로
    변환함.

    여러 조회 함수가 같은 변환 코드를
    반복하지 않도록 분리한 함수임.
    """

    return NodeCredentialRecord(
        node_id=str(
            row["node_id"]
        ),
        vehicle_id=str(
            row["vehicle_id"]
        ),
        api_key_hash=str(
            row["api_key_hash"]
        ),
        api_key_last4=str(
            row["api_key_last4"]
        ),
        is_active=bool(
            row["is_active"]
        ),
        last_authenticated_at=(
            None
            if row[
                "last_authenticated_at"
            ] is None
            else str(
                row[
                    "last_authenticated_at"
                ]
            )
        ),
        created_at=str(
            row["created_at"]
        ),
        updated_at=str(
            row["updated_at"]
        ),
    )


def get_node_credential(
    node_id: str,
) -> NodeCredentialRecord | None:
    """
    node_id로 Pi 인증 정보를 조회함.

    등록되지 않은 Pi라면
    None을 반환함.
    """

    connection = (
        get_database_connection()
    )

    try:
        row = connection.execute(
            """
            SELECT
                node_id,
                vehicle_id,
                api_key_hash,
                api_key_last4,
                is_active,
                last_authenticated_at,
                created_at,
                updated_at
            FROM vehicle_node_credentials
            WHERE node_id = ?
            """,
            (node_id,),
        ).fetchone()

        if row is None:
            return None

        return row_to_node_credential(
            row
        )

    finally:
        connection.close()


def save_node_credential(
    node_id: str,
    vehicle_id: str,
    api_key_hash: str,
    api_key_last4: str,
    rotate: bool,
) -> NodeCredentialRecord:
    """
    Pi와 차량의 매칭 정보를 저장함.

    rotate=False:
        새로운 Pi만 등록할 수 있음.

    rotate=True:
        기존 Pi의 API Key를 교체함.

    차량 매칭을 실수로 바꾸지 못하게 하고,
    차량 한 대에 Pi 한 대만 연결함.
    """

    connection = (
        get_database_connection()
    )

    try:
        connection.execute(
            "BEGIN IMMEDIATE"
        )

        vehicle_row = connection.execute(
            """
            SELECT vehicle_id
            FROM vehicles
            WHERE vehicle_id = ?
            """,
            (vehicle_id,),
        ).fetchone()

        if vehicle_row is None:
            raise ValueError(
                "등록된 차량을 찾을 수 없음: "
                f"{vehicle_id}"
            )

        existing_node = connection.execute(
            """
            SELECT
                node_id,
                vehicle_id
            FROM vehicle_node_credentials
            WHERE node_id = ?
            """,
            (node_id,),
        ).fetchone()

        existing_vehicle_binding = (
            connection.execute(
                """
                SELECT node_id
                FROM vehicle_node_credentials
                WHERE vehicle_id = ?
                """,
                (vehicle_id,),
            ).fetchone()
        )

        if (
            existing_vehicle_binding
            is not None
            and str(
                existing_vehicle_binding[
                    "node_id"
                ]
            )
            != node_id
        ):
            raise ValueError(
                "해당 차량은 이미 다른 Pi와 "
                "연결되어 있음: "
                f"{existing_vehicle_binding['node_id']}"
            )

        if existing_node is None:
            connection.execute(
                """
                INSERT INTO
                vehicle_node_credentials (
                    node_id,
                    vehicle_id,
                    api_key_hash,
                    api_key_last4,
                    is_active
                )
                VALUES (?, ?, ?, ?, 1)
                """,
                (
                    node_id,
                    vehicle_id,
                    api_key_hash,
                    api_key_last4,
                ),
            )

        else:
            existing_vehicle_id = str(
                existing_node["vehicle_id"]
            )

            if not rotate:
                raise ValueError(
                    "이미 등록된 Pi 노드임. "
                    "키를 변경하려면 "
                    "--rotate 옵션이 필요함."
                )

            if (
                existing_vehicle_id
                != vehicle_id
            ):
                raise ValueError(
                    "키 교체 과정에서는 차량 매칭을 "
                    "변경할 수 없음. "
                    f"현재 차량: {existing_vehicle_id}"
                )

            connection.execute(
                """
                UPDATE vehicle_node_credentials
                SET
                    api_key_hash = ?,
                    api_key_last4 = ?,
                    is_active = 1,
                    updated_at =
                        CURRENT_TIMESTAMP
                WHERE node_id = ?
                """,
                (
                    api_key_hash,
                    api_key_last4,
                    node_id,
                ),
            )

        connection.commit()

        row = connection.execute(
            """
            SELECT
                node_id,
                vehicle_id,
                api_key_hash,
                api_key_last4,
                is_active,
                last_authenticated_at,
                created_at,
                updated_at
            FROM vehicle_node_credentials
            WHERE node_id = ?
            """,
            (node_id,),
        ).fetchone()

        if row is None:
            raise RuntimeError(
                "등록한 노드를 다시 "
                "조회할 수 없음."
            )

        return row_to_node_credential(
            row
        )

    except Exception:
        connection.rollback()
        raise

    finally:
        connection.close()


def mark_node_authenticated(
    node_id: str,
) -> None:
    """
    Pi가 정상 인증된 시각을 기록함.

    Heartbeat 또는 영상 업로드처럼
    인증된 요청이 들어올 때마다 갱신됨.
    """

    connection = (
        get_database_connection()
    )

    try:
        connection.execute(
            """
            UPDATE vehicle_node_credentials
            SET
                last_authenticated_at =
                    CURRENT_TIMESTAMP,

                updated_at =
                    CURRENT_TIMESTAMP
            WHERE node_id = ?
            """,
            (node_id,),
        )

        connection.commit()

    finally:
        connection.close()
