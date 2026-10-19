"""시연용 2단계 확인과 차량 변경용 일회성 권한 관리.

실제 신원·차량 소유 인증을 수행하지 않는다. 인증기관 연동 전까지
로그인 사용자에게 화면상 확인 단계를 제공하는 데모 구현이다.
"""

from datetime import datetime, timedelta, timezone
import hashlib
import secrets
import sqlite3

from app.db.database import get_database_connection


class VehicleOperationDeniedError(PermissionError):
    """차량 소유권 또는 일회용 확인 권한이 유효하지 않음."""


# 기능: 사용자가 해당 차량을 소유하는지 확인한다.
# 입력: 진행 중인 SQLite connection, 사용자 ID, 차량 ID.
# 출력: 소유 관계가 있으면 None; 없으면 VehicleOperationDeniedError.
# 구조·이유: 같은 연결에서 user_vehicles를 조회해 이후 차량 조작 전에 소유권을 검사한다.
def require_owned_vehicle(
    connection: sqlite3.Connection,
    user_id: int,
    vehicle_id: str,
) -> None:
    row = connection.execute(
        "SELECT 1 FROM user_vehicles WHERE user_id=? AND vehicle_id=?",
        (user_id, vehicle_id),
    ).fetchone()
    if row is None:
        raise VehicleOperationDeniedError("내 차량이 아니거나 등록되지 않은 차량임.")


def issue_vehicle_operation_grant(
    user_id: int,
    purpose: str,
    vehicle_id: str | None,
) -> str:
    """두 데모 확인 후 5분 유효한 단일 작업 권한을 발급함."""
    if purpose not in {"register", "update", "delete"}:
        raise ValueError("지원하지 않는 차량 작업임.")
    if (purpose == "register") != (vehicle_id is None):
        raise ValueError("차량 작업 대상이 올바르지 않음.")

    token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    expires_at = (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()
    connection = get_database_connection()
    try:
        connection.execute("BEGIN IMMEDIATE")
        if vehicle_id is not None:
            require_owned_vehicle(connection, user_id, vehicle_id)
        connection.execute(
            """INSERT INTO vehicle_operation_grants
               (token_hash, user_id, purpose, vehicle_id, expires_at)
               VALUES (?, ?, ?, ?, ?)""",
            (token_hash, user_id, purpose, vehicle_id, expires_at),
        )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
    return token


def consume_vehicle_operation_grant(
    connection: sqlite3.Connection,
    user_id: int,
    purpose: str,
    vehicle_id: str | None,
    token: str,
) -> None:
    """변경 트랜잭션 안에서 권한을 한 번만 사용함."""
    if not token:
        raise VehicleOperationDeniedError("본인·차량 확인이 필요함.")
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    row = connection.execute(
        """SELECT user_id, purpose, vehicle_id, expires_at, consumed_at
           FROM vehicle_operation_grants WHERE token_hash=?""",
        (token_hash,),
    ).fetchone()
    if (
        row is None
        or row["user_id"] != user_id
        or row["purpose"] != purpose
        or row["vehicle_id"] != vehicle_id
        or row["consumed_at"] is not None
        or datetime.fromisoformat(row["expires_at"]) <= datetime.now(timezone.utc)
    ):
        raise VehicleOperationDeniedError("차량 확인이 만료되었거나 작업 대상이 다름.")
    connection.execute(
        "UPDATE vehicle_operation_grants SET consumed_at=? WHERE token_hash=?",
        (datetime.now(timezone.utc).isoformat(), token_hash),
    )
