from dataclasses import dataclass
import sqlite3

from app.db.database import (
    get_database_connection,
)
from app.db.vehicle_repository import (
    normalize_license_plate,
)


VALID_VEHICLE_STATUSES = {
    "ACTIVE",
    "INACTIVE",
    "TRANSFERRED",
    "RETIRED",
}


@dataclass(frozen=True)
class VehicleMaintenanceResult:
    vehicle_id: str
    license_plate: str
    vehicle_status: str


@dataclass(frozen=True)
class NodeReplacementResult:
    vehicle_id: str
    old_node_id: str | None
    new_node_id: str
    api_key_last4: str


def change_vehicle_license_plate(
    vehicle_id: str,
    new_license_plate: str,
    reason: str,
    admin_user_id: int,
) -> VehicleMaintenanceResult:
    """차량번호를 변경하고 전후 값을 감사 이력에 기록함."""

    normalized_plate = normalize_license_plate(
        new_license_plate
    )

    connection = get_database_connection()

    try:
        connection.execute("BEGIN IMMEDIATE")

        vehicle = connection.execute(
            """
            SELECT vehicle_id, license_plate, status
            FROM vehicles
            WHERE vehicle_id = ?
            """,
            (vehicle_id,),
        ).fetchone()

        if vehicle is None:
            raise ValueError("등록된 차량을 찾을 수 없음.")

        duplicate = connection.execute(
            """
            SELECT vehicle_id
            FROM vehicles
            WHERE REPLACE(UPPER(license_plate), ' ', '') = ?
              AND vehicle_id <> ?
            """,
            (normalized_plate, vehicle_id),
        ).fetchone()

        if duplicate is not None:
            raise ValueError("이미 다른 차량에 등록된 차량번호임.")

        old_plate = str(vehicle["license_plate"])

        if old_plate == normalized_plate:
            raise ValueError("기존 차량번호와 동일함.")

        connection.execute(
            """
            UPDATE vehicles
            SET license_plate = ?
            WHERE vehicle_id = ?
            """,
            (normalized_plate, vehicle_id),
        )

        connection.execute(
            """
            INSERT INTO vehicle_plate_history (
                vehicle_id,
                old_license_plate,
                new_license_plate,
                changed_by_user_id,
                change_reason
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                vehicle_id,
                old_plate,
                normalized_plate,
                admin_user_id,
                reason,
            ),
        )

        connection.commit()

        return VehicleMaintenanceResult(
            vehicle_id=vehicle_id,
            license_plate=normalized_plate,
            vehicle_status=str(vehicle["status"]),
        )

    except Exception:
        connection.rollback()
        raise

    finally:
        connection.close()


def set_vehicle_status(
    vehicle_id: str,
    vehicle_status: str,
) -> VehicleMaintenanceResult:
    """
    차량을 삭제하지 않고 운영 상태를 변경함.

    ACTIVE가 아닌 상태로 바꾸면 연결된 Pi 인증도 즉시
    비활성화하여 폐차·양도 차량의 업로드를 차단함.
    """

    normalized_status = vehicle_status.strip().upper()

    if normalized_status not in VALID_VEHICLE_STATUSES:
        raise ValueError("지원하지 않는 차량 상태임.")

    connection = get_database_connection()

    try:
        connection.execute("BEGIN IMMEDIATE")

        vehicle = connection.execute(
            """
            SELECT vehicle_id, license_plate
            FROM vehicles
            WHERE vehicle_id = ?
            """,
            (vehicle_id,),
        ).fetchone()

        if vehicle is None:
            raise ValueError("등록된 차량을 찾을 수 없음.")

        connection.execute(
            """
            UPDATE vehicles
            SET status = ?
            WHERE vehicle_id = ?
            """,
            (normalized_status, vehicle_id),
        )

        if normalized_status != "ACTIVE":
            connection.execute(
                """
                UPDATE vehicle_node_credentials
                SET is_active = 0,
                    updated_at = CURRENT_TIMESTAMP
                WHERE vehicle_id = ?
                """,
                (vehicle_id,),
            )

        connection.commit()

        return VehicleMaintenanceResult(
            vehicle_id=vehicle_id,
            license_plate=str(vehicle["license_plate"]),
            vehicle_status=normalized_status,
        )

    except Exception:
        connection.rollback()
        raise

    finally:
        connection.close()


def replace_vehicle_node(
    vehicle_id: str,
    new_node_id: str,
    new_api_key_hash: str,
    new_api_key_last4: str,
    reason: str,
    admin_user_id: int,
) -> NodeReplacementResult:
    """
    차량의 Pi 인증을 새 노드와 새 키로 원자적으로 교체함.

    현재 연결 테이블은 차량당 한 행만 허용하므로 기존 행을
    갱신하고, 교체 전 정보는 replacement_history에 보존함.
    기존 API Key는 갱신 즉시 인증에 실패함.
    """

    connection = get_database_connection()

    try:
        connection.execute("BEGIN IMMEDIATE")

        vehicle = connection.execute(
            """
            SELECT status
            FROM vehicles
            WHERE vehicle_id = ?
            """,
            (vehicle_id,),
        ).fetchone()

        if vehicle is None:
            raise ValueError("등록된 차량을 찾을 수 없음.")

        if str(vehicle["status"]) != "ACTIVE":
            raise ValueError("ACTIVE 차량만 Pi를 연결할 수 있음.")

        collision = connection.execute(
            """
            SELECT vehicle_id
            FROM vehicle_node_credentials
            WHERE node_id = ? COLLATE NOCASE
              AND vehicle_id <> ?
            """,
            (new_node_id, vehicle_id),
        ).fetchone()

        if collision is not None:
            raise ValueError("새 node_id가 다른 차량에서 사용 중임.")

        previous = connection.execute(
            """
            SELECT node_id, api_key_last4
            FROM vehicle_node_credentials
            WHERE vehicle_id = ?
            """,
            (vehicle_id,),
        ).fetchone()

        old_node_id = (
            None if previous is None else str(previous["node_id"])
        )
        old_key_last4 = (
            None if previous is None else str(previous["api_key_last4"])
        )

        if previous is None:
            connection.execute(
                """
                INSERT INTO vehicle_node_credentials (
                    node_id, vehicle_id, api_key_hash,
                    api_key_last4, is_active
                )
                VALUES (?, ?, ?, ?, 1)
                """,
                (
                    new_node_id,
                    vehicle_id,
                    new_api_key_hash,
                    new_api_key_last4,
                ),
            )
        else:
            connection.execute(
                """
                UPDATE vehicle_node_credentials
                SET node_id = ?,
                    api_key_hash = ?,
                    api_key_last4 = ?,
                    is_active = 1,
                    last_authenticated_at = NULL,
                    updated_at = CURRENT_TIMESTAMP
                WHERE vehicle_id = ?
                """,
                (
                    new_node_id,
                    new_api_key_hash,
                    new_api_key_last4,
                    vehicle_id,
                ),
            )

        connection.execute(
            """
            INSERT INTO vehicle_node_replacement_history (
                vehicle_id, old_node_id, old_api_key_last4,
                new_node_id, new_api_key_last4,
                changed_by_user_id, change_reason
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                vehicle_id,
                old_node_id,
                old_key_last4,
                new_node_id,
                new_api_key_last4,
                admin_user_id,
                reason,
            ),
        )

        connection.commit()

        return NodeReplacementResult(
            vehicle_id=vehicle_id,
            old_node_id=old_node_id,
            new_node_id=new_node_id,
            api_key_last4=new_api_key_last4,
        )

    except sqlite3.IntegrityError as error:
        connection.rollback()
        raise ValueError("노드 또는 API Key가 이미 사용 중임.") from error

    except Exception:
        connection.rollback()
        raise

    finally:
        connection.close()
