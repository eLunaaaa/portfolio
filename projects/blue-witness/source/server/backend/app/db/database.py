import sqlite3
from pathlib import Path

from app.services.timestamp_normalization import (
    normalize_evidence_timestamps,
    timestamp_factor_to_ns,
)


PROJECT_ROOT = (
    Path(__file__).resolve().parents[2]
)

DATABASE_DIRECTORY = (
    PROJECT_ROOT
    / "storage"
    / "database"
)

DEFAULT_DATABASE_PATH = (
    DATABASE_DIRECTORY
    / "evidence.db"
)

LEGACY_DATABASE_PATH = (
    DATABASE_DIRECTORY
    / "ace.db"
)

# 새 설치는 역할 중심 이름을 사용함. 기존 DB만 존재하는 설치에서는
# 파일을 강제로 옮기지 않고 그대로 열어 데이터 유실을 방지함.
DATABASE_PATH = (
    DEFAULT_DATABASE_PATH
    if (
        DEFAULT_DATABASE_PATH.exists()
        or not LEGACY_DATABASE_PATH.exists()
    )
    else LEGACY_DATABASE_PATH
)

SCHEMA_PATH = (
    Path(__file__).resolve().parent
    / "schema.sql"
)

MIGRATIONS_DIRECTORY = (
    Path(__file__).resolve().parent
    / "migrations"
)

MIGRATION_PATHS = (
    MIGRATIONS_DIRECTORY
    / "003_event_access.sql",

    MIGRATIONS_DIRECTORY
    / "004_accounts_and_vehicles.sql",

    MIGRATIONS_DIRECTORY
    / "005_vehicle_node_credentials.sql",

    MIGRATIONS_DIRECTORY
    / "006_event_collection_queue.sql",

    MIGRATIONS_DIRECTORY
    / "007_event_identity_compat.sql",

    MIGRATIONS_DIRECTORY
    / "008_vehicle_maintenance.sql",

    MIGRATIONS_DIRECTORY
    / "009_portal_vehicle_management.sql",
    MIGRATIONS_DIRECTORY
    / "010_user_profiles.sql",
)


def get_database_connection() -> (
    sqlite3.Connection
):
    """
    SQLite 연결을 생성하고
    서버 공통 DB 설정을 적용함.

    row_factory:
        조회 결과를 row["column"] 방식으로
        사용할 수 있게 함.

    foreign_keys:
        부모 데이터가 삭제될 때 관련 데이터도
        외래키 규칙에 따라 처리되게 함.

    busy_timeout:
        다른 요청이 SQLite를 잠시 사용하는 경우
        최대 5초 동안 기다리게 함.
    """

    DATABASE_DIRECTORY.mkdir(
        parents=True,
        exist_ok=True,
    )

    connection = sqlite3.connect(
        DATABASE_PATH,
        timeout=5.0,
    )

    connection.row_factory = sqlite3.Row

    connection.execute(
        "PRAGMA foreign_keys = ON"
    )

    connection.execute(
        "PRAGMA busy_timeout = 5000"
    )

    return connection


def get_table_columns(
    connection: sqlite3.Connection,
    table_name: str,
) -> set[str]:
    """
    지정한 테이블의 컬럼 이름을 조회함.

    기존 DB에 없는 컬럼만 추가하기 위해
    사용하는 공통 함수임.
    """

    rows = connection.execute(
        f"PRAGMA table_info({table_name})"
    ).fetchall()

    return {
        str(row["name"])
        for row in rows
    }


def migrate_vehicle_registration_schema(
    connection: sqlite3.Connection,
) -> None:
    """
    기존 vehicles 테이블에 차량 등록 화면에서
    사용하는 컬럼을 추가함.

    기존 사용자와 차량 데이터는 삭제하지 않음.
    """

    existing_tables = {
        str(row["name"])
        for row in connection.execute(
            """
            SELECT name
            FROM sqlite_master
            WHERE type = 'table'
            """
        ).fetchall()
    }

    if "vehicles" not in existing_tables:
        raise RuntimeError(
            "vehicles 테이블이 없음."
        )

    if "user_vehicles" not in existing_tables:
        raise RuntimeError(
            "user_vehicles 테이블이 없음."
        )

    vehicle_columns = get_table_columns(
        connection,
        "vehicles",
    )

    if "vehicle_model" not in vehicle_columns:
        connection.execute(
            """
            ALTER TABLE vehicles
            ADD COLUMN vehicle_model TEXT
            """
        )

    if "icon_key" not in vehicle_columns:
        connection.execute(
            """
            ALTER TABLE vehicles
            ADD COLUMN icon_key TEXT
            NOT NULL
            DEFAULT 'sedan'
            """
        )

    connection.execute(
        """
        UPDATE vehicles
        SET icon_key = 'sedan'
        WHERE icon_key IS NULL
           OR TRIM(icon_key) = ''
        """
    )


def migrate_event_collection_schema(
    connection: sqlite3.Connection,
) -> None:
    """
    기존 events 테이블에 자동 수집과
    병합 대기열 관리에 필요한 컬럼을 추가함.

    ALTER TABLE을 무조건 실행하면 서버를 다시
    시작할 때 duplicate column 오류가 발생함.

    따라서 현재 컬럼 목록을 먼저 확인하고,
    없는 컬럼만 추가함.
    """

    event_columns = get_table_columns(
        connection,
        "events",
    )

    if (
        "collection_started_at"
        not in event_columns
    ):
        connection.execute(
            """
            ALTER TABLE events
            ADD COLUMN collection_started_at TEXT
            """
        )

    if (
        "collection_deadline_at"
        not in event_columns
    ):
        connection.execute(
            """
            ALTER TABLE events
            ADD COLUMN collection_deadline_at TEXT
            """
        )

    if "ready_at" not in event_columns:
        connection.execute(
            """
            ALTER TABLE events
            ADD COLUMN ready_at TEXT
            """
        )

    if "evidence_count" not in event_columns:
        connection.execute(
            """
            ALTER TABLE events
            ADD COLUMN evidence_count INTEGER
            NOT NULL DEFAULT 0
            """
        )

    # 기존 사고 데이터가 있다면 evidence 테이블을
    # 기준으로 실제 영상 개수를 다시 계산함.
    connection.execute(
        """
        UPDATE events
        SET evidence_count = (
            SELECT COUNT(
                DISTINCT evidence.vehicle_id
            )
            FROM evidence
            WHERE evidence.event_id
                  = events.event_id
              AND evidence.upload_status
                  = 'STORED'
        )
        """
    )

def migrate_legacy_event_identity_schema(
    connection: sqlite3.Connection,
) -> None:
    """
    이전 DB의 action_id 컬럼을 호환용으로 유지함.

    현재 업로드 계약은 event_id만 사용함. 기존 운영 DB에서
    NOT NULL·UNIQUE 제약이 걸린 컬럼을 즉시 제거하면 장애가
    발생할 수 있으므로 event_id의 미러 값만 저장함. API와
    화면에는 이 레거시 컬럼을 노출하지 않음.
    """

    event_columns = get_table_columns(
        connection,
        "events",
    )

    if "action_id" not in event_columns:
        connection.execute(
            """
            ALTER TABLE events
            ADD COLUMN action_id TEXT
            """
        )

    connection.execute(
        """
        UPDATE events
        SET action_id = event_id
        WHERE action_id IS NULL
           OR TRIM(action_id) = ''
        """
    )

    connection.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS
        idx_events_action_id_unique
        ON events(action_id)
        """
    )

    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS
        idx_events_action_time
        ON events(action_id, event_time_ns)
        """
    )


def migrate_vehicle_maintenance_schema(
    connection: sqlite3.Connection,
) -> None:
    """
    기존 차량에 운영 상태 컬럼을 추가함.

    기존 차량은 모두 정상 운행 중인 것으로 간주하여
    ACTIVE를 기본값으로 사용함. 차량을 삭제하지 않고
    INACTIVE 또는 RETIRED로 전환하면 과거 사고 기록과
    사용자 소유 관계를 그대로 보존할 수 있음.
    """

    vehicle_columns = get_table_columns(
        connection,
        "vehicles",
    )

    if "status" not in vehicle_columns:
        connection.execute(
            """
            ALTER TABLE vehicles
            ADD COLUMN status TEXT
            NOT NULL DEFAULT 'ACTIVE'
            """
        )

    connection.execute(
        """
        UPDATE vehicles
        SET status = 'ACTIVE'
        WHERE status IS NULL
           OR TRIM(status) = ''
        """
    )


def migrate_unix_epoch_units(
    connection: sqlite3.Connection,
) -> None:
    """
    과거에 ms/us Epoch를 ns로 오해해 1970년으로 표시된
    사건과 Evidence를 Unix Epoch 나노초로 보정함.

    2020~2100 범위로 명확히 판별되는 값만 수정함. 이미 ns인
    데이터와 판별 불가능한 부팅 상대시간은 건드리지 않음.
    보정된 사건이 이미 병합됐다면 Timestamp 자막도 다시 만들 수
    있도록 Merge Job revision을 하나 증가시켜 재병합을 요청함.
    """

    event_rows = connection.execute(
        """
        SELECT event_id, event_time_ns
        FROM events
        """
    ).fetchall()

    for event_row in event_rows:
        event_id = str(event_row["event_id"])
        raw_event_time = int(event_row["event_time_ns"])
        event_unit = timestamp_factor_to_ns(raw_event_time)

        if event_unit is None or event_unit[0] == 1:
            continue

        evidence_rows = connection.execute(
            """
            SELECT id, clip_start_time_ns, clip_end_time_ns
            FROM evidence
            WHERE event_id = ?
            """,
            (event_id,),
        ).fetchall()

        normalized_event_time = raw_event_time * event_unit[0]

        for evidence_row in evidence_rows:
            normalized = normalize_evidence_timestamps(
                event_time=raw_event_time,
                clip_start_time=int(evidence_row["clip_start_time_ns"]),
                clip_end_time=int(evidence_row["clip_end_time_ns"]),
            )

            connection.execute(
                """
                UPDATE evidence
                SET clip_start_time_ns = ?,
                    clip_end_time_ns = ?
                WHERE id = ?
                """,
                (
                    normalized.clip_start_time_ns,
                    normalized.clip_end_time_ns,
                    int(evidence_row["id"]),
                ),
            )

        connection.execute(
            """
            UPDATE events
            SET event_time_ns = ?,
                status = CASE
                    WHEN evidence_count >= 1 THEN 'READY'
                    ELSE status
                END,
                updated_at = CURRENT_TIMESTAMP
            WHERE event_id = ?
            """,
            (normalized_event_time, event_id),
        )

        merge_job = connection.execute(
            """
            SELECT event_id
            FROM merge_jobs
            WHERE event_id = ?
            """,
            (event_id,),
        ).fetchone()

        if merge_job is not None and evidence_rows:
            connection.execute(
                """
                UPDATE merge_jobs
                SET status = 'WAITING',
                    available_at = CURRENT_TIMESTAMP,
                    attempt_count = 0,
                    last_error = NULL,
                    started_at = NULL,
                    completed_at = NULL,
                    target_revision = target_revision + 1,
                    rerun_requested = 0,
                    updated_at = CURRENT_TIMESTAMP
                WHERE event_id = ?
                """,
                (event_id,),
            )


def migrate_merge_revision_schema(
    connection: sqlite3.Connection,
) -> None:
    """
    늦게 도착한 영상의 재병합을 관리할 컬럼을 추가함.

    target_revision:
        다음 Worker가 생성할 병합 결과 번호임.

    completed_revision:
        마지막으로 정상 완료된 결과 번호임.

    rerun_requested:
        병합 실행 중 새 영상이 도착하여 현재 작업이
        끝난 뒤 다시 병합해야 하는지를 표시함.
    """

    merge_job_columns = get_table_columns(
        connection,
        "merge_jobs",
    )

    if "target_revision" not in merge_job_columns:
        connection.execute(
            """
            ALTER TABLE merge_jobs
            ADD COLUMN target_revision INTEGER
            NOT NULL DEFAULT 1
            """
        )

    if "completed_revision" not in merge_job_columns:
        connection.execute(
            """
            ALTER TABLE merge_jobs
            ADD COLUMN completed_revision INTEGER
            NOT NULL DEFAULT 0
            """
        )

    if "rerun_requested" not in merge_job_columns:
        connection.execute(
            """
            ALTER TABLE merge_jobs
            ADD COLUMN rerun_requested INTEGER
            NOT NULL DEFAULT 0
            """
        )


def initialize_database() -> None:
    """
    서버 시작 시 전체 DB 구조를 준비함.

    실행 순서:
    1. 기본 사고·영상 테이블을 생성함.
    2. 사고 접근 코드 테이블을 생성함.
    3. 사용자·차량 테이블을 생성함.
    4. Pi 노드 인증 테이블을 생성함.
    5. 병합 작업 대기열을 생성함.
    6. 기존 차량 및 사고 테이블 컬럼을 보완함.

    기존 데이터는 삭제하지 않음.
    """

    schema_sql = SCHEMA_PATH.read_text(
        encoding="utf-8"
    )

    migration_sql_list = [
        migration_path.read_text(
            encoding="utf-8"
        )
        for migration_path
        in MIGRATION_PATHS
    ]

    connection = get_database_connection()

    try:
        connection.execute(
            "PRAGMA journal_mode = WAL"
        )

        connection.executescript(
            schema_sql
        )

        if "content_sha256" not in get_table_columns(connection, "evidence"):
            connection.execute(
                "ALTER TABLE evidence ADD COLUMN content_sha256 TEXT"
            )

        for migration_sql in (
            migration_sql_list
        ):
            connection.executescript(
                migration_sql
            )

        migrate_vehicle_registration_schema(
            connection
        )

        # 공동 소유자의 별명은 사용자별로 독립 저장함.
        if "display_name" not in get_table_columns(connection, "user_vehicles"):
            connection.execute(
                "ALTER TABLE user_vehicles ADD COLUMN display_name TEXT"
            )

        migrate_event_collection_schema(
            connection
        )

        migrate_legacy_event_identity_schema(
            connection
        )

        migrate_vehicle_maintenance_schema(
            connection
        )

        migrate_merge_revision_schema(
            connection
        )

        migrate_unix_epoch_units(
            connection
        )

        connection.commit()

    finally:
        connection.close()


def database_is_ready() -> bool:
    """
    DB 연결과 필수 테이블 존재 여부를 확인함.

    health API가 서버 준비 상태를 판단할 때
    사용하는 함수임.
    """

    required_tables = {
        "events",
        "evidence",
        "event_access",
        "access_sessions",
        "users",
        "user_sessions",
        "vehicles",
        "user_vehicles",
        "vehicle_nodes",
        "vehicle_node_credentials",
        "merge_jobs",
    }

    connection = None

    try:
        connection = (
            get_database_connection()
        )

        rows = connection.execute(
            """
            SELECT name
            FROM sqlite_master
            WHERE type = 'table'
            """
        ).fetchall()

        existing_tables = {
            str(row["name"])
            for row in rows
        }

        return required_tables.issubset(
            existing_tables
        )

    except (
        sqlite3.Error,
        RuntimeError,
    ):
        return False

    finally:
        if connection is not None:
            connection.close()
