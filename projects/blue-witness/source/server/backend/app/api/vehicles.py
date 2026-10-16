from fastapi import (
    APIRouter,
    Depends,
    Header,
    HTTPException,
    status,
)

from app.api.dependencies import (
    get_current_user,
)
from app.db.account_repository import (
    UserRecord,
)
from app.db.vehicle_repository import (
    VehicleAlreadyRegisteredError,
    unlink_user_vehicle,
    update_owned_vehicle,
    update_user_vehicle_nickname,
)
from app.schemas.portal import (
    VehicleSummary,
)
from app.schemas.vehicle import (
    VehicleCreateRequest,
    VehicleNicknameRequest,
    VehicleUpdateRequest,
    VehicleVerificationRequest,
    VehicleVerificationResponse,
)
from app.services.vehicle_service import (
    register_vehicle_for_user,
    vehicle_record_to_summary,
)
from app.services.vehicle_operation_service import (
    VehicleOperationDeniedError,
    issue_vehicle_operation_grant,
)


router = APIRouter(
    prefix="/portal/vehicles",
)


@router.post("/verification", response_model=VehicleVerificationResponse)
def verify_vehicle_operation(
    payload: VehicleVerificationRequest,
    current_user: UserRecord = Depends(get_current_user),
) -> VehicleVerificationResponse:
    """시연용 확인: 실제 신원·차량 증명 대신 두 확인 단계를 기록함."""
    if not payload.identity_confirmed or not payload.vehicle_confirmed:
        raise HTTPException(status_code=400, detail="본인·차량 확인 단계를 완료해주세요.")
    try:
        token = issue_vehicle_operation_grant(
            current_user.id, payload.purpose, payload.vehicle_id
        )
    except VehicleOperationDeniedError as error:
        raise HTTPException(status_code=403, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    return VehicleVerificationResponse(verification_token=token)


@router.post(
    "",
    response_model=VehicleSummary,
    status_code=status.HTTP_201_CREATED,
)
def create_portal_vehicle(
    payload: VehicleCreateRequest,
    verification_token: str = Header(alias="X-Vehicle-Verification"),
    current_user: UserRecord = Depends(
        get_current_user
    ),
) -> VehicleSummary:
    """
    로그인 사용자의 차량을 등록함.

    요청 주소:
        POST /api/v1/portal/vehicles

    201:
        정상적으로 등록됨.

    409:
        같은 차량번호가 이미 등록됨.

    400 또는 422:
        입력값 형식이 잘못됨.
    """

    try:
        return register_vehicle_for_user(
            user=current_user,
            payload=payload,
            verification_token=verification_token,
        )

    except VehicleAlreadyRegisteredError as error:
        raise HTTPException(
            status_code=(
                status.HTTP_409_CONFLICT
            ),
            detail=str(error),
        ) from error

    except ValueError as error:
        raise HTTPException(
            status_code=(
                status.HTTP_400_BAD_REQUEST
            ),
            detail=str(error),
        ) from error

    except VehicleOperationDeniedError as error:
        raise HTTPException(status_code=403, detail=str(error)) from error


@router.patch("/{vehicle_id}/nickname", response_model=VehicleSummary)
def change_vehicle_nickname(
    vehicle_id: str,
    payload: VehicleNicknameRequest,
    current_user: UserRecord = Depends(get_current_user),
) -> VehicleSummary:
    """별명은 인증 단계 없이 현재 사용자에게만 변경함."""
    try:
        return vehicle_record_to_summary(
            update_user_vehicle_nickname(
                current_user.id, vehicle_id, payload.display_name
            )
        )
    except VehicleOperationDeniedError as error:
        raise HTTPException(status_code=403, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@router.patch("/{vehicle_id}", response_model=VehicleSummary)
def edit_portal_vehicle(
    vehicle_id: str,
    payload: VehicleUpdateRequest,
    verification_token: str = Header(alias="X-Vehicle-Verification"),
    current_user: UserRecord = Depends(get_current_user),
) -> VehicleSummary:
    """확인된 소유자의 차량 정보와 별명을 함께 저장함."""
    try:
        return vehicle_record_to_summary(
            update_owned_vehicle(
                current_user.id, vehicle_id, payload.license_plate,
                payload.vehicle_model, payload.icon_key, verification_token,
                display_name=payload.display_name,
            )
        )
    except VehicleAlreadyRegisteredError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except VehicleOperationDeniedError as error:
        raise HTTPException(status_code=403, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@router.delete("/{vehicle_id}", status_code=204)
def delete_portal_vehicle(
    vehicle_id: str,
    verification_token: str = Header(alias="X-Vehicle-Verification"),
    current_user: UserRecord = Depends(get_current_user),
) -> None:
    """내 차량 목록에서 연결만 해제하고 사고 증거는 보존함."""
    try:
        unlink_user_vehicle(current_user.id, vehicle_id, verification_token)
    except VehicleOperationDeniedError as error:
        raise HTTPException(status_code=403, detail=str(error)) from error
