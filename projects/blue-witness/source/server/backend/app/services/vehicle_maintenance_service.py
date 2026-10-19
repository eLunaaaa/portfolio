# 파일 역할: 차량번호·운영 상태·장치 교체 서비스. 입력 정규화와 장치 키 발급을 DB 변경 계층에 연결한다.
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


# 기능: 차량 정보 변경 사유를 정규화한다.
# 입력: 변경 사유 문자열 reason.
# 출력: 앞뒤 공백을 제거한 사유; 2자 미만이면 ValueError.
# 구조·이유: 유효한 사유를 요구해 관리자 변경 이력에 빈 설명이 남지 않게 한다.
def normalize_reason(reason: str) -> str:
    normalized = reason.strip()

    if len(normalized) < 2:
        raise ValueError("변경 사유를 2자 이상 입력해야 함.")

    return normalized


# 기능: 차량번호 변경과 변경 사유 기록을 요청한다.
# 입력: 차량 ID, 새 차량번호, 사유, 작업 관리자 ID.
# 출력: 저장소 계층이 반환하는 VehicleMaintenanceResult.
# 구조·이유: 차량 ID를 대문자로 정규화하고 사유를 검증한 뒤 DB 변경 함수에 전달한다.
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


# 기능: 차량의 운영 상태를 변경한다.
# 입력: vehicle_id와 변경할 vehicle_status.
# 출력: 상태 변경 결과 VehicleMaintenanceResult.
# 구조·이유: 차량 ID 형식을 통일한 뒤 저장소의 상태 변경 처리를 호출한다.
def change_status(
    vehicle_id: str,
    vehicle_status: str,
) -> VehicleMaintenanceResult:
    return set_vehicle_status(
        vehicle_id=vehicle_id.strip().upper(),
        vehicle_status=vehicle_status,
    )


# 기능: 차량 장치를 교체하고 새 장치 인증 키를 발급한다.
# 입력: 차량 ID, 새 장치 ID, 변경 사유, 관리자 ID.
# 출력: 새 키 원문과 장치 변경 결과를 담은 ReplacedNodeWithSecret.
# 구조·이유: 임의 키 생성 → 해시·끝 4자리 저장 → 교체 결과 반환. DB에는 키 원문 대신 해시를 넘긴다.
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
