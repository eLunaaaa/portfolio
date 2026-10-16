from pydantic import (
    BaseModel,
    Field,
)


class HeartbeatRequest(BaseModel):
    """
    Raspberry Pi가 서버로 보내는 연결 상태 정보임.
    """

    vehicle_id: str = Field(
        min_length=1,
        max_length=32,
        pattern=r"^[A-Za-z0-9_-]+$",
    )

    software_version: str | None = Field(
        default=None,
        max_length=64,
    )


class HeartbeatResponse(BaseModel):
    """
    서버가 Heartbeat 저장 후 반환하는 응답임.
    """

    received: bool
    vehicle_id: str
    server_time_ns: int
    last_seen_at: str


class VehicleSummary(BaseModel):
    """
    사용자에게 보여줄 소유 차량 요약임.
    """

    vehicle_id: str
    license_plate: str

    vehicle_model: str | None
    display_name: str | None

    icon_key: str = "sedan"
    status: str = "ACTIVE"


class EventSummary(BaseModel):
    """
    관리자와 사용자에게 보여줄 사고 요약임.
    """

    event_id: str
    accident_vehicle_id: str

    event_time_ns: int
    event_time_kst: str

    evidence_count: int
    evidence_vehicle_ids: list[str]

    latest_evidence_received_at: (
        str | None
    )

    merge_status: str
    merged_mode: str | None
    merged_available: bool


class NodeSummary(BaseModel):
    """
    관리자에게 보여줄 차량 노드 상태임.
    """

    vehicle_id: str
    ip_address: str | None
    software_version: str | None

    last_seen_at: str
    seconds_since_last_seen: float

    status: str


class AdminUserSummary(BaseModel):
    """
    관리자 화면의 사용자 요약임.
    """

    id: int
    username: str
    full_name: str
    role: str
    is_active: bool
    vehicle_count: int
    created_at: str


class UserPortalOverview(BaseModel):
    """
    일반 사용자 포털 첫 화면의 전체 데이터임.
    """

    username: str
    full_name: str

    vehicles: list[VehicleSummary]
    events: list[EventSummary]


class AdminOverview(BaseModel):
    """
    관리자 포털 첫 화면의 전체 데이터임.
    """

    total_events: int
    total_nodes: int
    online_nodes: int
    total_users: int

    events: list[EventSummary]
    nodes: list[NodeSummary]
    users: list[AdminUserSummary]

class EventAccessStatus(BaseModel):
    event_id: str
    unlocked: bool
    access_session_expires_at: str | None
    merged_video_available: bool

class IssuedEventAccessCode(BaseModel):
    event_id: str
    access_code: str
    expires_at: str
    valid_for_seconds: int
    message: str
