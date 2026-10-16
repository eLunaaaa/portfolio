from datetime import (
    datetime,
    timezone,
    timedelta,
)
from pathlib import Path
import time
import json

from app.db.account_repository import (
    UserRecord,
)

from app.db.vehicle_repository import (
    list_user_vehicles,
)
from app.db.portal_repository import (
    EventRecord,
    NodeRecord,
    get_user_event,
    list_admin_users,
    list_all_events,
    list_user_events,
    list_vehicle_nodes,
    record_node_heartbeat,
)
from app.schemas.portal import (
    AdminOverview,
    AdminUserSummary,
    EventSummary,
    HeartbeatResponse,
    NodeSummary,
    UserPortalOverview,
    VehicleSummary,
)


BACKEND_ROOT = Path(__file__).resolve().parents[2]

NODE_ONLINE_THRESHOLD_SECONDS = 30.0

KST = timezone(
    timedelta(hours=9)
)


def event_time_to_kst(
    event_time_ns: int,
) -> str:
    """
    Unix Epoch 나노초를 사람이 읽을 수 있는
    한국 시각 문자열로 변환함.

    DB 계산에는 나노초 정수를 유지하고,
    화면 표시용으로만 문자열을 생성함.
    """

    event_datetime = datetime.fromtimestamp(
        event_time_ns / 1_000_000_000,
        tz=timezone.utc,
    ).astimezone(KST)

    return (
        event_datetime
        .isoformat(timespec="milliseconds")
    )


def find_merged_video(
    event_id: str,
) -> tuple[Path | None, str | None]:
    """
    사고의 병합 영상이 존재하는지 확인함.

    extended 결과를 우선 사용하고,
    없으면 common 결과를 확인함.

    반환:
        영상 경로
        병합 모드
    """

    merge_directory = (
        BACKEND_ROOT
        / "storage"
        / "merged"
        / event_id
    )

    def latest_revision(
        mode: str,
    ) -> Path | None:
        candidates: list[tuple[int, Path]] = []

        for path in merge_directory.glob(
            f"merged_{mode}_r*.mp4"
        ):
            revision_text = (
                path.stem.rsplit("_r", 1)[-1]
            )

            if not revision_text.isdigit():
                continue

            if not path.is_file() or path.stat().st_size <= 0:
                continue

            candidates.append(
                (int(revision_text), path)
            )

        if not candidates:
            return None

        return max(
            candidates,
            key=lambda item: item[0],
        )[1]

    latest_extended = latest_revision(
        "extended"
    )

    if latest_extended is not None:
        return latest_extended, "extended"

    latest_common = latest_revision(
        "common"
    )

    if latest_common is not None:
        return latest_common, "common"

    extended_path = (
        merge_directory
        / "merged_extended.mp4"
    )

    common_path = (
        merge_directory
        / "merged_common.mp4"
    )

    if (
        extended_path.exists()
        and extended_path.stat().st_size > 0
    ):
        return extended_path, "extended"

    if (
        common_path.exists()
        and common_path.stat().st_size > 0
    ):
        return common_path, "common"

    return None, None


def find_video_view(event_id: str, view: str = "all") -> tuple[Path | None, str | None]:
    """현재 전체 병합본과 동일 리비전으로 생성된 보기만 반환함."""
    path, mode = find_merged_video(event_id)
    if view == "all" or path is None:
        return path, mode
    if view not in {"witness", "own"}:
        return None, None
    manifest = path.with_name(path.stem.replace("merged_", "merge_plan_", 1) + ".json")
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None, None
    if view not in data.get("views", {}):
        return None, None
    # 요청값이나 JSON의 경로를 직접 사용하지 않고 선택된 병합 파일에서 파생함.
    variant = path.with_name(f"{path.stem}_{view}.mp4")
    if not variant.is_file() or variant.stat().st_size <= 0:
        return None, None
    return variant, mode


def available_video_views(event_id: str) -> dict[str, bool]:
    """영상 미수신·시연 제외·이전 병합본의 미생성 모드를 화면에 알림."""
    return {view: find_video_view(event_id, view)[0] is not None
            for view in ("all", "witness", "own")}


def determine_merge_status(
    evidence_count: int,
    merged_path: Path | None,
) -> str:
    """
    DB와 파일 상태를 이용해 사고 처리 상태를 결정함.

    MERGED:
        병합 영상 파일이 존재함.

    READY_TO_MERGE:
        서로 다른 차량 영상이 2개 이상이지만
        아직 병합 결과가 없음.

    COLLECTING:
        영상이 1개만 들어옴.

    REGISTERED:
        사고만 등록되고 영상은 아직 없음.
    """

    if merged_path is not None:
        return "MERGED"

    if evidence_count >= 2:
        return "READY_TO_MERGE"

    if evidence_count == 1:
        return "COLLECTING"

    return "REGISTERED"


def event_record_to_summary(
    record: EventRecord,
) -> EventSummary:
    """
    DB의 EventRecord를 화면용 EventSummary로 변환함.

    파일 시스템에서 병합 결과 존재 여부도 확인함.
    """

    merged_path, merged_mode = (
        find_merged_video(
            record.event_id
        )
    )

    merge_status = determine_merge_status(
        evidence_count=record.evidence_count,
        merged_path=merged_path,
    )

    return EventSummary(
        event_id=record.event_id,
        accident_vehicle_id=(
            record.accident_vehicle_id
        ),
        event_time_ns=record.event_time_ns,
        event_time_kst=event_time_to_kst(
            record.event_time_ns
        ),
        evidence_count=(
            record.evidence_count
        ),
        evidence_vehicle_ids=list(
            record.evidence_vehicle_ids
        ),
        latest_evidence_received_at=(
            record.latest_evidence_received_at
        ),
        merge_status=merge_status,
        merged_mode=merged_mode,
        merged_available=(
            merged_path is not None
        ),
    )


def parse_database_datetime(
    value: str,
) -> datetime:
    """
    DB의 시각 문자열을 UTC datetime으로 변환함.

    Heartbeat는 ISO 형식으로 저장하지만,
    SQLite CURRENT_TIMESTAMP 형식도 처리할 수 있게 함.
    """

    try:
        parsed = datetime.fromisoformat(
            value
        )

    except ValueError:
        parsed = datetime.strptime(
            value,
            "%Y-%m-%d %H:%M:%S",
        )

    if parsed.tzinfo is None:
        parsed = parsed.replace(
            tzinfo=timezone.utc
        )

    return parsed.astimezone(
        timezone.utc
    )


def node_record_to_summary(
    record: NodeRecord,
) -> NodeSummary:
    """
    마지막 Heartbeat 이후 경과시간을 계산하여
    ONLINE 또는 OFFLINE 상태를 결정함.

    DB에 ONLINE 문자열을 저장하지 않는 이유:
    시간이 지나면 별도 UPDATE 없이도
    자동으로 OFFLINE이 되어야 하기 때문임.
    """

    last_seen = parse_database_datetime(
        record.last_seen_at
    )

    current_time = datetime.now(
        timezone.utc
    )

    seconds_since_last_seen = max(
        (
            current_time - last_seen
        ).total_seconds(),
        0.0,
    )

    if (
        seconds_since_last_seen
        <= NODE_ONLINE_THRESHOLD_SECONDS
    ):
        node_status = "ONLINE"
    else:
        node_status = "OFFLINE"

    return NodeSummary(
        vehicle_id=record.vehicle_id,
        ip_address=record.ip_address,
        software_version=(
            record.software_version
        ),
        last_seen_at=record.last_seen_at,
        seconds_since_last_seen=round(
            seconds_since_last_seen,
            3,
        ),
        status=node_status,
    )


def receive_heartbeat(
    vehicle_id: str,
    ip_address: str | None,
    software_version: str | None,
) -> HeartbeatResponse:
    """
    Pi의 Heartbeat를 현재 서버시각으로 기록함.

    클라이언트가 보낸 시각을 사용하지 않고
    서버가 실제로 받은 시각을 사용함.

    차량이 잘못된 미래 시각을 보내 ONLINE 상태를
    계속 유지하는 문제를 방지함.
    """

    current_time = datetime.now(
        timezone.utc
    )

    last_seen_at = current_time.isoformat()

    normalized_vehicle_id = (
        vehicle_id.strip().upper()
    )

    normalized_version = (
        None
        if software_version is None
        else software_version.strip()
    )

    record_node_heartbeat(
        vehicle_id=normalized_vehicle_id,
        ip_address=ip_address,
        software_version=normalized_version,
        last_seen_at=last_seen_at,
    )

    return HeartbeatResponse(
        received=True,
        vehicle_id=normalized_vehicle_id,
        server_time_ns=time.time_ns(),
        last_seen_at=last_seen_at,
    )


def get_user_portal_overview(
    user: UserRecord,
) -> UserPortalOverview:
    """
    로그인 사용자의 차량과 사고 목록을 구성함.

    차량은 vehicle_repository에서 조회함.

    기존 정보:
        vehicle_id
        license_plate
        display_name

    새 정보:
        vehicle_model
        icon_key

    기존 차량은 vehicle_model이 None일 수 있으며,
    icon_key는 마이그레이션에서 sedan으로 설정됨.
    """

    vehicle_records = list_user_vehicles(
        user.id
    )

    event_records = list_user_events(
        user.id
    )

    vehicles = [
        VehicleSummary(
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
            icon_key=(
                record.icon_key
            ),
            status=record.status,
        )
        for record in vehicle_records
    ]

    events = [
        event_record_to_summary(record)
        for record in event_records
    ]

    return UserPortalOverview(
        username=user.username,
        full_name=user.full_name,
        vehicles=vehicles,
        events=events,
    )


def get_user_event_summary(
    user: UserRecord,
    event_id: str,
) -> EventSummary | None:
    """
    특정 사고가 현재 로그인 사용자 소유인지 확인하고
    사고 요약을 반환함.

    소유하지 않은 사고라면 None을 반환함.
    """

    record = get_user_event(
        user_id=user.id,
        event_id=event_id,
    )

    if record is None:
        return None

    return event_record_to_summary(
        record
    )


def get_admin_overview() -> AdminOverview:
    """
    관리자 화면에 필요한 전체 데이터를 구성함.

    전체 사고
    차량 노드
    사용자 목록
    요약 개수

    를 하나의 응답으로 반환함.
    """

    event_records = list_all_events()
    node_records = list_vehicle_nodes()
    user_records = list_admin_users()

    events = [
        event_record_to_summary(record)
        for record in event_records
    ]

    nodes = [
        node_record_to_summary(record)
        for record in node_records
    ]

    users = [
        AdminUserSummary(
            id=record.id,
            username=record.username,
            full_name=record.full_name,
            role=record.role,
            is_active=record.is_active,
            vehicle_count=(
                record.vehicle_count
            ),
            created_at=record.created_at,
        )
        for record in user_records
    ]

    online_nodes = sum(
        1
        for node in nodes
        if node.status == "ONLINE"
    )

    return AdminOverview(
        total_events=len(events),
        total_nodes=len(nodes),
        online_nodes=online_nodes,
        total_users=len(users),
        events=events,
        nodes=nodes,
        users=users,
    )
