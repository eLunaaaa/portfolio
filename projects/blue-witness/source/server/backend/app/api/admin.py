from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Request,
    Query,
    status,
)
from fastapi.responses import FileResponse
from typing import Literal
from app.services.portal_service import find_video_view, available_video_views

from app.api.dependencies import (
    get_current_admin,
)
from app.db.account_repository import (
    UserRecord,
)
from app.schemas.admin_actions import (
    AdminActionResponse,
    ConfirmationRequest,
    VehicleLicensePlateChangeRequest,
    VehicleMaintenanceResponse,
    VehicleNodeReplacementRequest,
    VehicleNodeReplacementResponse,
    VehicleStatusChangeRequest,
)
from app.schemas.portal import (
    AdminOverview,
)
from app.services.admin_cleanup_service import (
    delete_event,
    delete_user,
    purge_trash,
    reset_demo_data,
)
from app.services.portal_service import (
    find_merged_video,
    get_admin_overview,
)
from app.services.vehicle_maintenance_service import (
    change_plate,
    change_status,
    replace_node,
)


router = APIRouter(
    prefix="/admin"
)


@router.get("/communication-logs")
def admin_communication_logs(
    request: Request,
    current_admin: UserRecord = Depends(get_current_admin),
    kind: str | None = Query(default=None, pattern=r"^(health|heartbeat|evidence_upload|evidence_status|time_sync)$"),
    failures_only: bool = False,
    limit: int = Query(default=200, ge=1, le=500),
    vehicle_id: str | None = Query(default=None, pattern=r"^[A-Za-z0-9_-]{1,128}$"),
) -> dict:
    """관리자 인증 후 제한된 최근 Pi 통신 기록과 마지막 접속 시각을 조회한다."""
    return request.app.state.communication_log.snapshot(kind, failures_only, limit, vehicle_id)


@router.patch(
    "/vehicles/{vehicle_id}/license-plate",
    response_model=VehicleMaintenanceResponse,
)
def admin_change_vehicle_license_plate(
    vehicle_id: str,
    payload: VehicleLicensePlateChangeRequest,
    current_admin: UserRecord = Depends(get_current_admin),
) -> VehicleMaintenanceResponse:
    """본인 확인 후 차량번호를 바꾸고 변경 이력을 남김."""

    try:
        result = change_plate(
            vehicle_id=vehicle_id,
            new_license_plate=payload.new_license_plate,
            reason=payload.reason,
            admin_user_id=current_admin.id,
        )
    except ValueError as error:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(error),
        ) from error

    return VehicleMaintenanceResponse(
        success=True,
        vehicle_id=result.vehicle_id,
        license_plate=result.license_plate,
        vehicle_status=result.vehicle_status,
        message="차량번호를 변경하고 이력을 기록함.",
    )


@router.patch(
    "/vehicles/{vehicle_id}/status",
    response_model=VehicleMaintenanceResponse,
)
def admin_change_vehicle_status(
    vehicle_id: str,
    payload: VehicleStatusChangeRequest,
    current_admin: UserRecord = Depends(get_current_admin),
) -> VehicleMaintenanceResponse:
    """양도·폐차 차량을 삭제하지 않고 비활성 상태로 전환함."""

    try:
        result = change_status(
            vehicle_id=vehicle_id,
            vehicle_status=payload.vehicle_status,
        )
    except ValueError as error:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(error),
        ) from error

    return VehicleMaintenanceResponse(
        success=True,
        vehicle_id=result.vehicle_id,
        license_plate=result.license_plate,
        vehicle_status=result.vehicle_status,
        message=(
            "차량 상태를 변경함. ACTIVE가 아니면 연결된 Pi도 비활성화됨."
        ),
    )


@router.post(
    "/vehicles/{vehicle_id}/nodes/replace",
    response_model=VehicleNodeReplacementResponse,
)
def admin_replace_vehicle_node(
    vehicle_id: str,
    payload: VehicleNodeReplacementRequest,
    current_admin: UserRecord = Depends(get_current_admin),
) -> VehicleNodeReplacementResponse:
    """
    수리로 바뀐 Pi에 새 Node ID와 API Key를 발급함.

    기존 API Key는 즉시 무효화되며 새 키 원문은 이 응답에서만
    한 번 표시되므로 관리자가 안전하게 새 Pi에 전달해야 함.
    """

    try:
        result = replace_node(
            vehicle_id=vehicle_id,
            new_node_id=payload.new_node_id,
            reason=payload.reason,
            admin_user_id=current_admin.id,
        )
    except ValueError as error:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(error),
        ) from error

    return VehicleNodeReplacementResponse(
        success=True,
        vehicle_id=result.vehicle_id,
        old_node_id=result.old_node_id,
        new_node_id=result.new_node_id,
        api_key=result.api_key,
        api_key_last4=result.api_key_last4,
        message=(
            "Pi 교체 완료. API Key 원문은 다시 조회할 수 없으므로 지금 저장해야 함."
        ),
    )


@router.get(
    "/overview",
    response_model=AdminOverview,
)
def admin_overview(
    current_admin: UserRecord = Depends(
        get_current_admin
    ),
) -> AdminOverview:
    """
    관리자에게 전체 사고·차량·사용자
    현황을 반환함.
    """

    return get_admin_overview()


@router.get("/events/{event_id}/video/modes")
def admin_video_modes(event_id: str, current_admin: UserRecord = Depends(get_current_admin)) -> dict:
    """관리자 로그인 후에만 사고별 영상 보기 모드의 사용 가능 여부를 반환함."""
    return {"views": available_video_views(event_id)}


@router.get(
    "/events/{event_id}/video",
    response_class=FileResponse,
)
def admin_event_video(
    event_id: str,
    view: Literal["all", "witness", "own"] = "all",
    current_admin: UserRecord = Depends(
        get_current_admin
    ),
) -> FileResponse:
    """
    관리자에게 사고 병합 영상을 반환함.

    일반 사용자 영상 API와 달리 사고 접근 코드는
    요구하지 않지만, ADMIN 로그인은 반드시 검증함.
    """

    merged_video_result = (find_merged_video(event_id) if view == "all"
                           else find_video_view(event_id, view))

    if merged_video_result is None:
        raise HTTPException(
            status_code=(
                status.HTTP_409_CONFLICT
            ),
            detail=(
                "병합 영상이 아직 생성되지 않음."
            ),
        )

    video_path, merge_mode = (
        merged_video_result
    )

    if video_path is None:
        raise HTTPException(
            status_code=(
                status.HTTP_409_CONFLICT
            ),
            detail=(
                "병합 영상이 아직 생성되지 않음."
            ),
        )

    resolved_video_path = (
        video_path.resolve()
    )

    if not resolved_video_path.is_file():
        raise HTTPException(
            status_code=(
                status.HTTP_404_NOT_FOUND
            ),
            detail=(
                "병합 영상 파일을 찾을 수 없음."
            ),
        )

    if resolved_video_path.stat().st_size <= 0:
        raise HTTPException(
            status_code=(
                status.HTTP_409_CONFLICT
            ),
            detail=(
                "병합 영상 파일이 비어 있음."
            ),
        )

    return FileResponse(
        path=str(resolved_video_path),
        media_type="video/mp4",
        headers={
            "X-Evidence-Merge-Mode": (
                merge_mode or "unknown"
            ),
            "Cache-Control": (
                "private, no-store"
            ),
        },
    )


@router.delete(
    "/events/{event_id}",
    response_model=AdminActionResponse,
)
def admin_delete_event(
    event_id: str,
    payload: ConfirmationRequest,
    current_admin: UserRecord = Depends(
        get_current_admin
    ),
) -> AdminActionResponse:
    """
    특정 사고와 연결 데이터를 삭제함.
    """

    try:
        return delete_event(
            event_id=event_id,
            confirmation=(
                payload.confirmation
            ),
        )

    except ValueError as error:
        raise HTTPException(
            status_code=(
                status.HTTP_400_BAD_REQUEST
            ),
            detail=str(error),
        ) from error


@router.delete(
    "/users/{user_id}",
    response_model=AdminActionResponse,
)
def admin_delete_user(
    user_id: int,
    payload: ConfirmationRequest,
    current_admin: UserRecord = Depends(
        get_current_admin
    ),
) -> AdminActionResponse:
    """
    일반 사용자 계정을 삭제함.
    """

    try:
        return delete_user(
            current_admin_user_id=(
                current_admin.id
            ),
            target_user_id=user_id,
            confirmation=(
                payload.confirmation
            ),
        )

    except ValueError as error:
        raise HTTPException(
            status_code=(
                status.HTTP_400_BAD_REQUEST
            ),
            detail=str(error),
        ) from error


@router.post(
    "/reset-demo",
    response_model=AdminActionResponse,
)
def admin_reset_demo(
    payload: ConfirmationRequest,
    current_admin: UserRecord = Depends(
        get_current_admin
    ),
) -> AdminActionResponse:
    """
    현재 관리자만 유지하고
    전체 시연 데이터를 초기화함.
    """

    try:
        return reset_demo_data(
            current_admin_user_id=(
                current_admin.id
            ),
            confirmation=(
                payload.confirmation
            ),
        )

    except ValueError as error:
        raise HTTPException(
            status_code=(
                status.HTTP_400_BAD_REQUEST
            ),
            detail=str(error),
        ) from error


@router.post(
    "/trash/purge",
    response_model=AdminActionResponse,
)
def admin_purge_trash(
    payload: ConfirmationRequest,
    current_admin: UserRecord = Depends(
        get_current_admin
    ),
) -> AdminActionResponse:
    """
    휴지통 파일을 영구 삭제함.
    """

    try:
        return purge_trash(
            confirmation=(
                payload.confirmation
            )
        )

    except ValueError as error:
        raise HTTPException(
            status_code=(
                status.HTTP_400_BAD_REQUEST
            ),
            detail=str(error),
        ) from error
