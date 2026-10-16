from pydantic import (
    BaseModel,
    Field,
)
from typing import Literal


class ConfirmationRequest(BaseModel):
    """
    관리자가 삭제 대상을 직접 확인하기 위해
    입력하는 확인 문구임.
    """

    confirmation: str = Field(
        min_length=1,
        max_length=128,
    )


class AdminActionResponse(BaseModel):
    """
    관리자 삭제·초기화 작업 결과임.
    """

    success: bool
    action: str
    message: str
    deleted_counts: dict[str, int] = Field(
        default_factory=dict
    )
    trash_location: str | None = None


class VehicleLicensePlateChangeRequest(BaseModel):
    """고객센터에서 확인한 차량번호 변경 요청임."""

    new_license_plate: str = Field(min_length=2, max_length=20)
    reason: str = Field(min_length=2, max_length=200)


class VehicleStatusChangeRequest(BaseModel):
    """차량을 삭제하지 않고 운영 상태를 변경하는 요청임."""

    vehicle_status: Literal[
        "ACTIVE",
        "INACTIVE",
        "TRANSFERRED",
        "RETIRED",
    ]


class VehicleMaintenanceResponse(BaseModel):
    success: bool
    vehicle_id: str
    license_plate: str
    vehicle_status: str
    message: str


class VehicleNodeReplacementRequest(BaseModel):
    """수리·고장으로 교체한 Pi를 기존 차량에 연결하는 요청임."""

    new_node_id: str = Field(min_length=1, max_length=64)
    reason: str = Field(min_length=2, max_length=200)


class VehicleNodeReplacementResponse(BaseModel):
    """새 API Key 원문은 이 응답에서만 한 번 반환됨."""

    success: bool
    vehicle_id: str
    old_node_id: str | None
    new_node_id: str
    api_key: str
    api_key_last4: str
    message: str
