from dataclasses import dataclass
import sqlite3

from app.db.database import (
    get_database_connection,
)


@dataclass(frozen=True)
class ClaimedMergeJob:
    """
    Worker가 독점적으로 가져온 병합 작업임.

    attempt_count는 이번 시도를 포함한
    누적 실행 횟수임.
    """

    event_id: str
    attempt_count: int
    target_revision: int


def recover_interrupted_merge_jobs(
    current_time: str,
) -> tuple[str, ...]:
    """
    서버 비정상 종료로 RUNNING에 남은 작업을
    다시 PENDING 상태로 복구함.

    예:
        FFmpeg 실행 중 서버 전원 종료
        → DB에는 RUNNING이 남음
        → 서버 재시작
        → PENDING으로 되돌림
        → Worker가 다시 병합함

    이 함수는 서버가 Worker를 시작하기 전에
    한 번 호출함.
    """

    connection = (
        get_database_connection()
    )

    recovered_event_ids: list[str] = []

    try:
        connection.execute(
            "BEGIN IMMEDIATE"
        )

        rows = connection.execute(
            """
            SELECT event_id
            FROM merge_jobs
            WHERE status = 'RUNNING'
            ORDER BY started_at ASC
            """
        ).fetchall()

        for row in rows:
            event_id = str(
                row["event_id"]
            )

            connection.execute(
                """
                UPDATE merge_jobs
                SET
                    status = 'PENDING',
                    available_at = ?,
                    started_at = NULL,
                    last_error = (
                        '서버 재시작으로 중단된 병합 작업을 복구함.'
                    ),
                    updated_at = ?
                WHERE event_id = ?
                  AND status = 'RUNNING'
                """,
                (
                    current_time,
                    current_time,
                    event_id,
                ),
            )

            connection.execute(
                """
                UPDATE events
                SET
                    status = 'READY',
                    updated_at = ?
                WHERE event_id = ?
                  AND status = 'MERGING'
                """,
                (
                    current_time,
                    event_id,
                ),
            )

            recovered_event_ids.append(
                event_id
            )

        connection.commit()

        return tuple(
            recovered_event_ids
        )

    except Exception:
        connection.rollback()
        raise

    finally:
        connection.close()


def claim_next_pending_merge_job(
    current_time: str,
) -> ClaimedMergeJob | None:
    """
    실행 가능한 PENDING 작업 한 개를
    RUNNING으로 변경하고 가져옴.

    BEGIN IMMEDIATE Transaction 안에서
    조회와 상태 변경을 함께 수행함.

    따라서 Worker가 동시에 두 번 실행되어도
    같은 작업을 두 Worker가 동시에 가져갈
    가능성을 줄일 수 있음.
    """

    connection = (
        get_database_connection()
    )

    try:
        connection.execute(
            "BEGIN IMMEDIATE"
        )

        row = connection.execute(
            """
            SELECT
                merge_jobs.event_id,
                merge_jobs.attempt_count,
                merge_jobs.target_revision
            FROM merge_jobs
            INNER JOIN events
                ON events.event_id
                   = merge_jobs.event_id
            WHERE merge_jobs.status
                  = 'PENDING'
              AND merge_jobs.available_at
                  <= ?
              AND events.status
                  = 'READY'
            ORDER BY
                merge_jobs.available_at ASC,
                merge_jobs.created_at ASC
            LIMIT 1
            """,
            (current_time,),
        ).fetchone()

        if row is None:
            connection.commit()
            return None

        event_id = str(
            row["event_id"]
        )

        next_attempt_count = (
            int(row["attempt_count"])
            + 1
        )

        cursor = connection.execute(
            """
            UPDATE merge_jobs
            SET
                status = 'RUNNING',
                attempt_count = ?,
                started_at = ?,
                completed_at = NULL,
                last_error = NULL,
                updated_at = ?
            WHERE event_id = ?
              AND status = 'PENDING'
            """,
            (
                next_attempt_count,
                current_time,
                current_time,
                event_id,
            ),
        )

        if cursor.rowcount != 1:
            connection.rollback()
            return None

        connection.execute(
            """
            UPDATE events
            SET
                status = 'MERGING',
                updated_at = ?
            WHERE event_id = ?
              AND status = 'READY'
            """,
            (
                current_time,
                event_id,
            ),
        )

        connection.commit()

        return ClaimedMergeJob(
            event_id=event_id,
            attempt_count=(
                next_attempt_count
            ),
            target_revision=int(
                row["target_revision"]
            ),
        )

    except Exception:
        connection.rollback()
        raise

    finally:
        connection.close()


def mark_merge_job_completed(
    event_id: str,
    completed_at: str,
    completed_revision: int,
) -> str:
    """
    병합 영상 생성이 성공한 작업을
    COMPLETED로 변경함.

    merge_jobs와 events를 같은 Transaction에서
    변경하여 두 상태가 달라지는 것을 방지함.
    """

    connection = (
        get_database_connection()
    )

    try:
        connection.execute(
            "BEGIN IMMEDIATE"
        )

        job_row = connection.execute(
            """
            SELECT rerun_requested
            FROM merge_jobs
            WHERE event_id = ?
              AND status = 'RUNNING'
            """,
            (event_id,),
        ).fetchone()

        if job_row is None:
            raise RuntimeError(
                "완료 처리할 RUNNING 병합 "
                f"작업이 없음: {event_id}"
            )

        rerun_requested = bool(
            job_row["rerun_requested"]
        )

        final_job_status = (
            "PENDING"
            if rerun_requested
            else "COMPLETED"
        )

        final_event_status = (
            "READY"
            if rerun_requested
            else "COMPLETED"
        )

        cursor = connection.execute(
            """
            UPDATE merge_jobs
            SET
                status = ?,
                target_revision = ?,
                completed_revision = ?,
                rerun_requested = 0,
                attempt_count = CASE
                    WHEN ? = 1 THEN 0
                    ELSE attempt_count
                END,
                started_at = NULL,
                available_at = ?,
                completed_at = ?,
                last_error = NULL,
                updated_at = ?
            WHERE event_id = ?
              AND status = 'RUNNING'
            """,
            (
                final_job_status,
                (
                    completed_revision + 1
                    if rerun_requested
                    else completed_revision
                ),
                completed_revision,
                1 if rerun_requested else 0,
                completed_at,
                completed_at,
                completed_at,
                event_id,
            ),
        )

        if cursor.rowcount != 1:
            raise RuntimeError(
                "완료 처리할 RUNNING 병합 "
                f"작업이 없음: {event_id}"
            )

        connection.execute(
            """
            UPDATE events
            SET
                status = ?,
                updated_at = ?
            WHERE event_id = ?
            """,
            (
                final_event_status,
                completed_at,
                event_id,
            ),
        )

        connection.commit()

        return final_job_status

    except Exception:
        connection.rollback()
        raise

    finally:
        connection.close()


def mark_merge_job_failed(
    event_id: str,
    failed_at: str,
    retry_at: str,
    maximum_attempts: int,
    error_message: str,
) -> str:
    """
    병합 실패 상태를 기록함.

    최대 시도 횟수 미만:
        merge_jobs → PENDING
        events → READY

    최대 시도 횟수 이상:
        merge_jobs → FAILED
        events → FAILED

    반환값:
        최종 merge_jobs 상태를 반환함.
    """

    connection = (
        get_database_connection()
    )

    # 너무 긴 FFmpeg 오류로 DB가 불필요하게
    # 커지지 않도록 마지막 4000자만 보관함.
    normalized_error = (
        error_message.strip()
        or "알 수 없는 병합 오류"
    )[-4000:]

    try:
        connection.execute(
            "BEGIN IMMEDIATE"
        )

        row = connection.execute(
            """
            SELECT attempt_count, rerun_requested
            FROM merge_jobs
            WHERE event_id = ?
            """,
            (event_id,),
        ).fetchone()

        if row is None:
            raise RuntimeError(
                "실패 처리할 병합 작업이 없음: "
                f"{event_id}"
            )

        attempt_count = int(
            row["attempt_count"]
        )

        if (
            attempt_count < maximum_attempts
            or row["rerun_requested"]
        ):
            final_job_status = "PENDING"
            final_event_status = "READY"

            connection.execute(
                """
                UPDATE merge_jobs
                SET
                    status = 'PENDING',
                    attempt_count = CASE WHEN rerun_requested=1
                        THEN 0 ELSE attempt_count END,
                    rerun_requested = 0,
                    available_at = ?,
                    started_at = NULL,
                    last_error = ?,
                    updated_at = ?
                WHERE event_id = ?
                """,
                (
                    retry_at,
                    normalized_error,
                    failed_at,
                    event_id,
                ),
            )

        else:
            final_job_status = "FAILED"
            final_event_status = "FAILED"

            connection.execute(
                """
                UPDATE merge_jobs
                SET
                    status = 'FAILED',
                    last_error = ?,
                    updated_at = ?
                WHERE event_id = ?
                """,
                (
                    normalized_error,
                    failed_at,
                    event_id,
                ),
            )

        connection.execute(
            """
            UPDATE events
            SET
                status = ?,
                updated_at = ?
            WHERE event_id = ?
            """,
            (
                final_event_status,
                failed_at,
                event_id,
            ),
        )

        connection.commit()

        return final_job_status

    except Exception:
        connection.rollback()
        raise

    finally:
        connection.close()
