from app.db.account_repository import (
    UserRecord,
)
from app.db.vehicle_repository import (
    VehicleRecord,
    create_user_vehicle,
)
from app.schemas.portal import (
    VehicleSummary,
)
from app.schemas.vehicle import (
    VehicleCreateRequest,
)


def vehicle_record_to_summary(
    record: VehicleRecord,
) -> VehicleSummary:
    """
    DB용 VehicleRecord를
    API 응답용 VehicleSummary로 변환함.

    DB 객체를 그대로 외부에 노출하지 않기 위해
    변환 계층을 분리함.
    """

    return VehicleSummary(
        vehicle_id=record.vehicle_id,
        license_plate=(
            record.license_plate
        ),
        vehicle_model=(
            record.vehicle_model
        ),
        display_name=(
            record.display_name
        ),
        icon_key=record.icon_key,
        status=record.status,
    )


def register_vehicle_for_user(
    user: UserRecord,
    payload: VehicleCreateRequest,
    verification_token: str,
) -> VehicleSummary:
    """
    현재 로그인 사용자 소유로 차량을 등록함.

    중요한 점:
    브라우저가 보낸 user_id를 사용하지 않음.

    get_current_user()가 확인한 user.id만 사용하므로
    다른 사용자에게 차량을 등록할 수 없음.
    """

    record = create_user_vehicle(
        user_id=user.id,
        license_plate=(
            payload.license_plate
        ),
        vehicle_model=(
            payload.vehicle_model
        ),
        display_name=(
            payload.display_name
        ),
        icon_key=payload.icon_key,
        verification_token=verification_token,
    )

    return vehicle_record_to_summary(
        record
    )
