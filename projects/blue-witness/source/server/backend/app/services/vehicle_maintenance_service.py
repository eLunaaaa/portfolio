from dataclasses import dataclass
import secrets

from app.db.vehicle_maintenance_repository import (
    NodeReplacementResult,
    VehicleMaintenanceResult,
    change_vehicle_license_plate,
    replace_vehicle_node,
    set_vehicle_status,
)
from app.services.node_auth_service import (
    hash_node_api_key,
    normalize_node_id,
)


@dataclass(frozen=True)
class ReplacedNodeWithSecret:
    vehicle_id: str
    old_node_id: str | None
    new_node_id: str
    api_key: str
    api_key_last4: str


def normalize_reason(reason: str) -> str:
    normalized = reason.strip()

    if len(normalized) < 2:
        raise ValueError("변경 사유를 2자 이상 입력해야 함.")

    return normalized


def change_plate(
    vehicle_id: str,
    new_license_plate: str,
    reason: str,
    admin_user_id: int,
) -> VehicleMaintenanceResult:
    return change_vehicle_license_plate(
        vehicle_id=vehicle_id.strip().upper(),
        new_license_plate=new_license_plate,
        reason=normalize_reason(reason),
        admin_user_id=admin_user_id,
    )


def change_status(
    vehicle_id: str,
    vehicle_status: str,
) -> VehicleMaintenanceResult:
    return set_vehicle_status(
        vehicle_id=vehicle_id.strip().upper(),
        vehicle_status=vehicle_status,
    )


def replace_node(
    vehicle_id: str,
    new_node_id: str,
    reason: str,
    admin_user_id: int,
) -> ReplacedNodeWithSecret:
    normalized_node_id = normalize_node_id(new_node_id)
    raw_api_key = "node_" + secrets.token_urlsafe(32)
    last4 = raw_api_key[-4:]

    result: NodeReplacementResult = replace_vehicle_node(
        vehicle_id=vehicle_id.strip().upper(),
        new_node_id=normalized_node_id,
        new_api_key_hash=hash_node_api_key(raw_api_key),
        new_api_key_last4=last4,
        reason=normalize_reason(reason),
        admin_user_id=admin_user_id,
    )

    return ReplacedNodeWithSecret(
        vehicle_id=result.vehicle_id,
        old_node_id=result.old_node_id,
        new_node_id=result.new_node_id,
        api_key=raw_api_key,
        api_key_last4=result.api_key_last4,
    )
