from dataclasses import dataclass
import sqlite3

from app.db.database import DATABASE_PATH


PATH_COLUMN_CANDIDATES = (
    "video_path",
    "file_path",
    "stored_path",
    "stored_file_path",
    "storage_path",
)

EVENT_TIME_COLUMN_CANDIDATES = (
    "event_time_ns",
    "accident_time_ns",
    "event_timestamp_ns",
    "event_time_utc_ns",
)


@dataclass(frozen=True)
class EvidenceRecord:
    """
    events 테이블의 사고 시각과
    evidence 테이블의 차량별 영상 정보를 합친 객체임.
    """

    id: int
    event_id: str
    accident_vehicle_id: str
    vehicle_id: str
    event_time_ns: int
    clip_start_time_ns: int
    clip_end_time_ns: int
    time_sync_status: str
    chrony_offset_ms: float | None
    video_path: str


def connect_database() -> sqlite3.Connection:
    """
    병합 프로그램에서 사용할 SQLite 연결을 생성함.
    """

    connection = sqlite3.connect(
        DATABASE_PATH,
        timeout=5,
    )

    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA busy_timeout = 5000")

    return connection


def get_table_columns(
    connection: sqlite3.Connection,
    table_name: str,
) -> set[str]:
    """
    지정된 테이블의 실제 컬럼 이름을 읽음.
    """

    rows = connection.execute(
        f"PRAGMA table_info({table_name})"
    ).fetchall()

    return {
        str(row["name"])
        for row in rows
    }


def find_column(
    columns: set[str],
    candidates: tuple[str, ...],
    description: str,
) -> str:
    """
    후보 컬럼명 중 실제 DB에 존재하는 컬럼 하나를 찾음.
    """

    for candidate in candidates:
        if candidate in columns:
            return candidate

    raise RuntimeError(
        f"{description} 컬럼을 찾을 수 없음.\n"
        f"현재 컬럼: {sorted(columns)}"
    )


def list_latest_evidence(
    event_id: str,
) -> list[EvidenceRecord]:
    """
    같은 event_id에 속하는 차량별 최신 영상 한 건씩을 조회함.

    사고 발생 시각은 events 테이블에서,
    차량별 영상 구간은 evidence 테이블에서 가져옴.
    """

    with connect_database() as connection:
        evidence_columns = get_table_columns(
            connection,
            "evidence",
        )

        event_columns = get_table_columns(
            connection,
            "events",
        )

        required_evidence_columns = {
            "event_id",
            "vehicle_id",
            "clip_start_time_ns",
            "clip_end_time_ns",
        }

        missing_columns = (
            required_evidence_columns
            - evidence_columns
        )

        if missing_columns:
            raise RuntimeError(
                "evidence 테이블에 필수 컬럼이 없음: "
                f"{sorted(missing_columns)}"
            )

        video_path_column = find_column(
            columns=evidence_columns,
            candidates=PATH_COLUMN_CANDIDATES,
            description="영상 경로",
        )

        event_time_column = find_column(
            columns=event_columns,
            candidates=EVENT_TIME_COLUMN_CANDIDATES,
            description="사고 발생 시각",
        )

        evidence_id_expression = (
            "e.id"
            if "id" in evidence_columns
            else "e.rowid"
        )

        sync_status_expression = (
            "e.time_sync_status"
            if "time_sync_status" in evidence_columns
            else "'UNKNOWN'"
        )

        chrony_offset_expression = (
            "e.chrony_offset_ms"
            if "chrony_offset_ms" in evidence_columns
            else "NULL"
        )

        rows = connection.execute(
            f"""
            SELECT
                {evidence_id_expression} AS id,
                e.event_id,
                ev.accident_vehicle_id,
                e.vehicle_id,
                ev.{event_time_column} AS event_time_ns,
                e.clip_start_time_ns,
                e.clip_end_time_ns,
                {sync_status_expression} AS time_sync_status,
                {chrony_offset_expression} AS chrony_offset_ms,
                e.{video_path_column} AS video_path
            FROM evidence AS e
            INNER JOIN events AS ev
                ON ev.event_id = e.event_id
            WHERE e.event_id = ?
            ORDER BY id DESC
            """,
            (event_id,),
        ).fetchall()

    if not rows:
        raise RuntimeError(
            f"event_id={event_id}에 대한 "
            "evidence 또는 events 데이터가 없음."
        )

    latest_by_vehicle: dict[str, EvidenceRecord] = {}

    for row in rows:
        vehicle_id = str(row["vehicle_id"])

        # 최신 id부터 조회했으므로 차량별 첫 행만 사용함.
        if vehicle_id in latest_by_vehicle:
            continue

        chrony_offset = row["chrony_offset_ms"]

        latest_by_vehicle[vehicle_id] = EvidenceRecord(
            id=int(row["id"]),
            event_id=str(row["event_id"]),
            accident_vehicle_id=str(
                row["accident_vehicle_id"]
            ),
            vehicle_id=vehicle_id,
            event_time_ns=int(row["event_time_ns"]),
            clip_start_time_ns=int(
                row["clip_start_time_ns"]
            ),
            clip_end_time_ns=int(
                row["clip_end_time_ns"]
            ),
            time_sync_status=str(
                row["time_sync_status"]
            ),
            chrony_offset_ms=(
                None
                if chrony_offset is None
                else float(chrony_offset)
            ),
            video_path=str(row["video_path"]),
        )

    return sorted(
        latest_by_vehicle.values(),
        key=lambda record: record.vehicle_id,
    )
