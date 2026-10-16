from dataclasses import dataclass
import sqlite3

from app.db.database import (
    get_database_connection,
)
from app.models.evidence import (
    EvidenceMetadata,
)


class EventMetadataConflictError(
    Exception
):
    """
    같은 event_id로 서로 다른 사고 정보가
    전달됐을 때 발생함.
    """


class DuplicateEvidenceError(
    Exception
):
    """
    같은 사고와 차량의 영상이
    중복 등록됐을 때 발생함.
    """


class EventCollectionClosedError(
    Exception
):
    """
    사고 상태가 손상되어 안전하게 영상을
    추가할 수 없을 때 발생함.

    정상적인 MERGING, COMPLETED, FAILED 상태는
    늦게 도착한 영상의 재병합 대상으로 처리함.
    """


@dataclass(frozen=True)
class EvidenceRegistrationResult:
    """
    영상 DB 등록 후 반환하는 사고 수집 상태임.
    """

    event_id: str
    vehicle_id: str

    event_status: str
    evidence_count: int

    collection_deadline_at: str
    merge_job_status: str


@dataclass(frozen=True)
class CollectionRefreshResult:
    """
    수집 마감 상태 갱신 결과임.
    """

    checked_count: int

    promoted_event_ids: tuple[str, ...]
    waiting_event_ids: tuple[str, ...]


def register_stored_evidence(
    metadata: EvidenceMetadata,
    event_id: str,
    file_path: str,
    received_at: str,
    collection_deadline_at: str,
    minimum_evidence_count: int,
    content_sha256: str | None = None,
    repair: bool = False,
) -> EvidenceRegistrationResult:
    """
    영상 기록, 사고 등록, 병합 작업 생성을
    하나의 SQLite Transaction으로 처리함.

    evidence·event·merge_job의 변경을 함께 commit함.
    파일 게시 후 DB commit 전에 중단된 요청은 재시도 시
    기존 파일의 SHA를 확인하고 DB 등록을 이어감.
    """

    connection = (
        get_database_connection()
    )

    try:
        connection.execute(
            "BEGIN IMMEDIATE"
        )

        event_row = connection.execute(
            """
            SELECT
                accident_vehicle_id,
                event_time_ns,
                status,
                collection_deadline_at
            FROM events
            WHERE event_id = ?
            """,
            (event_id,),
        ).fetchone()

        if event_row is None:
            event_status = "COLLECTING"

            effective_deadline = (
                collection_deadline_at
            )

            connection.execute(
                """
                -- action_id는 기존 DB 호환용 event_id 미러임.
                INSERT INTO events (
                    event_id,
                    action_id,
                    accident_vehicle_id,
                    event_time_ns,
                    status,
                    collection_started_at,
                    collection_deadline_at,
                    evidence_count,
                    updated_at
                )
                VALUES (
                    ?, ?, ?, ?, 'COLLECTING',
                    ?, ?, 0, ?
                )
                """,
                (
                    event_id,
                    event_id,
                    metadata.accident_vehicle_id,
                    metadata.event_time_ns,
                    received_at,
                    effective_deadline,
                    received_at,
                ),
            )

        else:
            if (
                event_row[
                    "accident_vehicle_id"
                ]
                != metadata.accident_vehicle_id
                or abs(
                    int(event_row["event_time_ns"])
                    - metadata.event_time_ns
                )
                > 2_000_000_000
            ):
                raise EventMetadataConflictError(
                    "같은 event_id에 서로 다른 "
                    "사고 정보가 전달됨."
                )

            event_status = str(
                event_row["status"]
            )

            if event_status not in {
                "COLLECTING",
                "READY",
                "MERGING",
                "COMPLETED",
                "FAILED",
            }:
                raise EventCollectionClosedError(
                    "지원하지 않는 사고 상태이므로 "
                    "새 영상을 추가할 수 없음: "
                    f"{event_status}"
                )

            existing_deadline = (
                event_row[
                    "collection_deadline_at"
                ]
            )

            if existing_deadline is None:
                effective_deadline = (
                    collection_deadline_at
                )

                connection.execute(
                    """
                    UPDATE events
                    SET
                        collection_started_at
                            = COALESCE(
                                collection_started_at,
                                ?
                            ),
                        collection_deadline_at = ?,
                        updated_at = ?
                    WHERE event_id = ?
                    """,
                    (
                        received_at,
                        effective_deadline,
                        received_at,
                        event_id,
                    ),
                )

            else:
                # 추가 영상이 들어와도 최초 마감 시각은
                # 연장하지 않음.
                effective_deadline = str(
                    existing_deadline
                )

        existing_evidence = (
            connection.execute(
                """
                SELECT id, content_sha256, upload_status,
                       clip_start_time_ns, clip_end_time_ns, file_path
                FROM evidence
                WHERE event_id = ?
                  AND vehicle_id = ?
                """,
                (
                    event_id,
                    metadata.vehicle_id,
                ),
            ).fetchone()
        )

        if existing_evidence is not None and not repair:
            raise DuplicateEvidenceError(
                "해당 사고와 차량의 증거가 "
                "이미 DB에 존재함."
            )

        if repair:
            if (existing_evidence is None or not content_sha256
                or existing_evidence["content_sha256"] != content_sha256
                or existing_evidence["file_path"] != file_path
                or existing_evidence["clip_start_time_ns"] != metadata.clip_start_time_ns
                or existing_evidence["clip_end_time_ns"] != metadata.clip_end_time_ns):
                raise EventMetadataConflictError("원본 복원 식별 정보가 일치하지 않음.")
            if existing_evidence["upload_status"] == "STORED":
                raise DuplicateEvidenceError("이미 원본 복원이 완료됨.")
            connection.execute(
                "UPDATE evidence SET upload_status='STORED' WHERE id=?",
                (existing_evidence["id"],),
            )
        else:
            connection.execute(
                """
                INSERT INTO evidence (
                    event_id,
                    vehicle_id,
                    clip_start_time_ns,
                    clip_end_time_ns,
                    file_path,
                    upload_status,
                    time_sync_status,
                    chrony_offset_ms,
                    ai_confidence,
                    received_at,
                    content_sha256
                )
                VALUES (
                    ?, ?, ?, ?, ?,
                    'STORED', ?, ?, ?, ?, ?
                )
                """,
                (
                    event_id,
                    metadata.vehicle_id,
                    metadata.clip_start_time_ns,
                    metadata.clip_end_time_ns,
                    file_path,
                    metadata.time_sync_status,
                    metadata.chrony_offset_ms,
                    metadata.ai_confidence,
                    received_at,
                    content_sha256,
                ),
            )

        count_row = connection.execute(
            """
            SELECT COUNT(
                DISTINCT vehicle_id
            ) AS evidence_count
            FROM evidence
            WHERE event_id = ?
              AND upload_status = 'STORED'
            """,
            (event_id,),
        ).fetchone()

        evidence_count = int(
            count_row["evidence_count"]
        )

        connection.execute(
            """
            UPDATE events
            SET
                evidence_count = ?,
                updated_at = ?
            WHERE event_id = ?
            """,
            (
                evidence_count,
                received_at,
                event_id,
            ),
        )

        initial_job_status = (
            "PENDING"
            if event_status == "READY"
            else "WAITING"
        )

        initial_available_at = (
            received_at
            if initial_job_status == "PENDING"
            else effective_deadline
        )

        connection.execute(
            """
            INSERT OR IGNORE INTO merge_jobs (
                event_id,
                status,
                available_at,
                attempt_count,
                updated_at
            )
            VALUES (?, ?, ?, 0, ?)
            """,
            (
                event_id,
                initial_job_status,
                initial_available_at,
                received_at,
            ),
        )

        # 완료되었거나 최종 실패한 사고에 새 차량 영상이
        # 늦게 도착하면 기존 결과를 지우지 않고 다음
        # revision 병합 작업을 새로 예약함.
        if event_status in {
            "COMPLETED",
            "FAILED",
        }:
            event_status = "READY"

            connection.execute(
                """
                UPDATE events
                SET
                    status = 'READY',
                    ready_at = ?,
                    evidence_count = ?,
                    updated_at = ?
                WHERE event_id = ?
                """,
                (
                    received_at,
                    evidence_count,
                    received_at,
                    event_id,
                ),
            )

            reopened_job = connection.execute(
                """
                UPDATE merge_jobs
                SET
                    status = 'PENDING',
                    target_revision = CASE
                        WHEN completed_revision > 0
                        THEN completed_revision + 1
                        WHEN target_revision < 1
                        THEN 1
                        ELSE target_revision
                    END,
                    rerun_requested = 0,
                    attempt_count = 0,
                    available_at = ?,
                    started_at = NULL,
                    completed_at = NULL,
                    last_error = NULL,
                    updated_at = ?
                WHERE event_id = ?
                """,
                (
                    received_at,
                    received_at,
                    event_id,
                ),
            )

            if reopened_job.rowcount != 1:
                raise EventCollectionClosedError(
                    "완료된 사고의 병합 작업을 "
                    "다시 예약할 수 없음."
                )

        # Worker가 현재 병합 중이라면 실행 중인 FFmpeg를
        # 강제로 중단하지 않음. 현재 revision이 끝난 직후
        # 다음 revision을 실행하도록 재실행 표시만 남김.
        elif event_status == "MERGING":
            rerun_job = connection.execute(
                """
                UPDATE merge_jobs
                SET
                    rerun_requested = 1,
                    updated_at = ?
                WHERE event_id = ?
                  AND status = 'RUNNING'
                """,
                (
                    received_at,
                    event_id,
                ),
            )

            if rerun_job.rowcount != 1:
                raise EventCollectionClosedError(
                    "실행 중인 병합 작업을 찾을 수 없어 "
                    "재병합을 예약하지 못함."
                )

        # 마감 시각이 이미 지났고 영상이 최소
        # 개수 이상이면 바로 병합 대기 상태로 바꿈.
        if (
            event_status == "COLLECTING"
            and evidence_count
                >= minimum_evidence_count
            and effective_deadline
                <= received_at
        ):
            event_status = "READY"

            connection.execute(
                """
                UPDATE events
                SET
                    status = 'READY',
                    ready_at = ?,
                    evidence_count = ?,
                    updated_at = ?
                WHERE event_id = ?
                """,
                (
                    received_at,
                    evidence_count,
                    received_at,
                    event_id,
                ),
            )

            connection.execute(
                """
                UPDATE merge_jobs
                SET
                    status = 'PENDING',
                    available_at = ?,
                    last_error = NULL,
                    updated_at = ?
                WHERE event_id = ?
                """,
                (
                    received_at,
                    received_at,
                    event_id,
                ),
            )

        job_row = connection.execute(
            """
            SELECT status
            FROM merge_jobs
            WHERE event_id = ?
            """,
            (event_id,),
        ).fetchone()

        merge_job_status = str(
            job_row["status"]
        )

        connection.commit()

        return EvidenceRegistrationResult(
            event_id=event_id,
            vehicle_id=metadata.vehicle_id,
            event_status=event_status,
            evidence_count=evidence_count,
            collection_deadline_at=(
                effective_deadline
            ),
            merge_job_status=(
                merge_job_status
            ),
        )

    except sqlite3.IntegrityError as error:
        connection.rollback()

        if (
            "UNIQUE constraint failed"
            in str(error)
        ):
            raise DuplicateEvidenceError(
                "해당 사고와 차량의 증거가 "
                "이미 DB에 존재함."
            ) from error

        raise

    except Exception:
        connection.rollback()
        raise

    finally:
        connection.close()


def mark_evidence_repair_pending(
    event_id: str, vehicle_id: str, content_sha256: str,
) -> None:
    """최초 검증 해시를 보존한 채 원본 복원 의도를 commit함."""
    connection = get_database_connection()
    try:
        connection.execute("BEGIN IMMEDIATE")
        changed = connection.execute(
            """
            UPDATE evidence SET upload_status='PENDING'
            WHERE event_id=? AND vehicle_id=? AND content_sha256=?
              AND upload_status IN ('STORED','PENDING')
            """,
            (event_id, vehicle_id, content_sha256),
        )
        if changed.rowcount != 1:
            raise EventMetadataConflictError("복원할 원본 해시가 일치하지 않음.")
        connection.execute(
            """
            UPDATE events SET evidence_count=(
                SELECT COUNT(*) FROM evidence
                WHERE event_id=? AND upload_status='STORED'
            ) WHERE event_id=?
            """,
            (event_id, event_id),
        )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def promote_due_collections(
    current_time: str,
    retry_at: str,
    minimum_evidence_count: int,
) -> CollectionRefreshResult:
    """
    수집 마감 시각이 지난 사고를 확인함.

    영상이 최소 개수 이상이면:
        events를 READY로 변경함.
        merge_jobs를 PENDING으로 변경함.

    영상이 부족하면:
        COLLECTING 상태를 유지함.
        retry_at 시각에 다시 확인하도록 함.
    """

    connection = (
        get_database_connection()
    )

    promoted_event_ids: list[str] = []
    waiting_event_ids: list[str] = []

    try:
        connection.execute(
            "BEGIN IMMEDIATE"
        )

        due_rows = connection.execute(
            """
            SELECT events.event_id
            FROM events
            INNER JOIN merge_jobs
                ON merge_jobs.event_id
                   = events.event_id
            WHERE events.status = 'COLLECTING'
              AND merge_jobs.status = 'WAITING'
              AND events.collection_deadline_at
                  IS NOT NULL
              AND events.collection_deadline_at
                  <= ?
              AND merge_jobs.available_at
                  <= ?
            ORDER BY
                events.collection_deadline_at ASC
            """,
            (
                current_time,
                current_time,
            ),
        ).fetchall()

        for due_row in due_rows:
            event_id = str(
                due_row["event_id"]
            )

            count_row = connection.execute(
                """
                SELECT COUNT(
                    DISTINCT vehicle_id
                ) AS evidence_count
                FROM evidence
                WHERE event_id = ?
                  AND upload_status = 'STORED'
                """,
                (event_id,),
            ).fetchone()

            evidence_count = int(
                count_row["evidence_count"]
            )

            if (
                evidence_count
                >= minimum_evidence_count
            ):
                connection.execute(
                    """
                    UPDATE events
                    SET
                        status = 'READY',
                        evidence_count = ?,
                        ready_at = ?,
                        updated_at = ?
                    WHERE event_id = ?
                    """,
                    (
                        evidence_count,
                        current_time,
                        current_time,
                        event_id,
                    ),
                )

                connection.execute(
                    """
                    UPDATE merge_jobs
                    SET
                        status = 'PENDING',
                        available_at = ?,
                        last_error = NULL,
                        updated_at = ?
                    WHERE event_id = ?
                    """,
                    (
                        current_time,
                        current_time,
                        event_id,
                    ),
                )

                promoted_event_ids.append(
                    event_id
                )

            else:
                connection.execute(
                    """
                    UPDATE events
                    SET
                        evidence_count = ?,
                        updated_at = ?
                    WHERE event_id = ?
                    """,
                    (
                        evidence_count,
                        current_time,
                        event_id,
                    ),
                )

                connection.execute(
                    """
                    UPDATE merge_jobs
                    SET
                        available_at = ?,
                        updated_at = ?
                    WHERE event_id = ?
                    """,
                    (
                        retry_at,
                        current_time,
                        event_id,
                    ),
                )

                waiting_event_ids.append(
                    event_id
                )

        connection.commit()

        return CollectionRefreshResult(
            checked_count=len(
                due_rows
            ),
            promoted_event_ids=tuple(
                promoted_event_ids
            ),
            waiting_event_ids=tuple(
                waiting_event_ids
            ),
        )

    except Exception:
        connection.rollback()
        raise

    finally:
        connection.close()
