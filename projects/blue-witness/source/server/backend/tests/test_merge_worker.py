from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from app.db import database as database_module
from app.db.database import (
    get_database_connection,
    initialize_database,
)
from app.db.merge_job_repository import (
    recover_interrupted_merge_jobs,
)
from app.services import merge_worker
from app.services.video_merger import (
    MergeError,
)


class MergeWorkerTestCase(unittest.TestCase):
    """
    자동 병합 Worker의 상태 전환을 검증함.

    실제 운영 DB와 FFmpeg를 사용하지 않음.
    임시 SQLite DB와 가짜 병합 결과를 사용하여
    Worker 제어 로직만 빠르게 검사함.
    """

    def setUp(self) -> None:
        """
        테스트 전용 DB와 출력 디렉터리를 준비함.
        """

        self.temporary_directory = (
            TemporaryDirectory()
        )

        self.temporary_root = Path(
            self.temporary_directory.name
        )

        self.original_database_path = (
            database_module.DATABASE_PATH
        )

        database_module.DATABASE_PATH = (
            self.temporary_root
            / "merge-worker-test.db"
        )

        initialize_database()

    def tearDown(self) -> None:
        """
        운영 DB 경로를 복구하고 임시 파일을 제거함.
        """

        database_module.DATABASE_PATH = (
            self.original_database_path
        )

        self.temporary_directory.cleanup()

    def insert_merge_job(
        self,
        event_id: str,
        event_status: str,
        job_status: str,
        attempt_count: int = 0,
    ) -> None:
        """
        Worker 검증에 필요한 사고와 작업을 생성함.

        업로드 과정은 2단계 테스트에서 검증했으므로
        여기서는 Worker 시작 상태만 직접 준비함.
        """

        connection = get_database_connection()

        try:
            connection.execute(
                """
                INSERT INTO events (
                    event_id,
                    action_id,
                    accident_vehicle_id,
                    event_time_ns,
                    status,
                    evidence_count,
                    ready_at,
                    updated_at
                )
                VALUES (?, ?, 'CAR_01', ?, ?, 2, ?, ?)
                """,
                (
                    event_id,
                    event_id,
                    1_800_000_005_000_000_000,
                    event_status,
                    "2000-01-01T00:00:00.000+00:00",
                    "2000-01-01T00:00:00.000+00:00",
                ),
            )

            connection.execute(
                """
                INSERT INTO merge_jobs (
                    event_id,
                    status,
                    available_at,
                    attempt_count,
                    started_at,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    event_id,
                    job_status,
                    "2000-01-01T00:00:00.000+00:00",
                    attempt_count,
                    (
                        "2000-01-01T00:00:00.000+00:00"
                        if job_status == "RUNNING"
                        else None
                    ),
                    "2000-01-01T00:00:00.000+00:00",
                ),
            )

            connection.commit()

        finally:
            connection.close()

    def read_state(self, event_id: str):
        """
        사고와 병합 작업의 현재 상태를 함께 조회함.
        """

        connection = get_database_connection()

        try:
            return connection.execute(
                """
                SELECT
                    events.status AS event_status,
                    merge_jobs.status AS job_status,
                    merge_jobs.attempt_count,
                    merge_jobs.available_at,
                    merge_jobs.started_at,
                    merge_jobs.completed_at,
                    merge_jobs.last_error
                FROM events
                INNER JOIN merge_jobs
                    ON merge_jobs.event_id
                       = events.event_id
                WHERE events.event_id = ?
                """,
                (event_id,),
            ).fetchone()

        finally:
            connection.close()

    def create_fake_merged_video(self) -> Path:
        """
        validate_output_file()이 확인할 비어 있지 않은
        가짜 MP4 파일을 임시 디렉터리에 생성함.

        실제 영상 디코딩은 하지 않으며 Worker의
        성공 상태 기록만 검증하는 용도임.
        """

        output_path = (
            self.temporary_root
            / "merged-test.mp4"
        )

        output_path.write_bytes(
            b"merge worker test output"
        )

        return output_path

    def test_pending_job_is_completed(self) -> None:
        """
        실행 가능한 작업이 한 번만 선점되고
        성공 후 COMPLETED로 기록되는지 검증함.
        """

        event_id = "EVT_WORKER_SUCCESS_001"

        self.insert_merge_job(
            event_id=event_id,
            event_status="READY",
            job_status="PENDING",
        )

        output_path = (
            self.create_fake_merged_video()
        )

        fake_result = {
            "output": {
                "path": str(output_path),
            }
        }

        with patch.object(
            merge_worker,
            "merge_event",
            return_value=fake_result,
        ) as merge_mock:
            result = (
                merge_worker
                .run_merge_worker_cycle()
            )

        self.assertEqual(
            result.state,
            "COMPLETED",
        )
        self.assertEqual(
            result.event_id,
            event_id,
        )
        self.assertEqual(
            result.attempt_count,
            1,
        )
        merge_mock.assert_called_once()
        self.assertEqual(
            merge_mock.call_args.kwargs[
                "revision"
            ],
            1,
        )

        state = self.read_state(event_id)

        self.assertEqual(
            state["event_status"],
            "COMPLETED",
        )
        self.assertEqual(
            state["job_status"],
            "COMPLETED",
        )
        self.assertEqual(
            state["attempt_count"],
            1,
        )
        self.assertIsNotNone(
            state["completed_at"]
        )
        self.assertIsNone(
            state["last_error"]
        )

        # 완료된 작업은 다음 주기에 다시 실행하지 않음.
        second_result = (
            merge_worker
            .run_merge_worker_cycle()
        )

        self.assertEqual(
            second_result.state,
            "IDLE",
        )

    def test_failed_job_retries_then_stops(self) -> None:
        """
        병합 실패 시 재시도를 예약하고 최대 시도에
        도달하면 FAILED로 종료하는지 검증함.
        """

        event_id = "EVT_WORKER_FAILURE_001"

        self.insert_merge_job(
            event_id=event_id,
            event_status="READY",
            job_status="PENDING",
        )

        with (
            patch.object(
                merge_worker,
                "merge_event",
                side_effect=MergeError(
                    "시연용 FFmpeg 실패"
                ),
            ),
            patch.object(
                merge_worker,
                "MERGE_MAXIMUM_ATTEMPTS",
                2,
            ),
            # 즉시 두 번째 주기를 실행할 수 있도록
            # 재시도 가능 시각을 과거로 고정함.
            patch.object(
                merge_worker,
                "calculate_retry_time",
                return_value=(
                    "2000-01-01T00:00:00.000+00:00"
                ),
            ),
        ):
            first_result = (
                merge_worker
                .run_merge_worker_cycle()
            )

            first_state = self.read_state(
                event_id
            )

            self.assertEqual(
                first_result.state,
                "RETRY_SCHEDULED",
            )
            self.assertEqual(
                first_state["event_status"],
                "READY",
            )
            self.assertEqual(
                first_state["job_status"],
                "PENDING",
            )
            self.assertEqual(
                first_state["attempt_count"],
                1,
            )
            self.assertIn(
                "시연용 FFmpeg 실패",
                first_state["last_error"],
            )

            second_result = (
                merge_worker
                .run_merge_worker_cycle()
            )

        final_state = self.read_state(
            event_id
        )

        self.assertEqual(
            second_result.state,
            "FAILED",
        )
        self.assertEqual(
            final_state["event_status"],
            "FAILED",
        )
        self.assertEqual(
            final_state["job_status"],
            "FAILED",
        )
        self.assertEqual(
            final_state["attempt_count"],
            2,
        )
        self.assertIn(
            "시연용 FFmpeg 실패",
            final_state["last_error"],
        )

    def test_running_job_is_recovered_after_restart(
        self,
    ) -> None:
        """
        서버가 병합 중 종료되어 RUNNING으로 남은
        작업을 READY/PENDING으로 복구하는지 검증함.
        """

        event_id = "EVT_WORKER_RECOVERY_001"

        self.insert_merge_job(
            event_id=event_id,
            event_status="MERGING",
            job_status="RUNNING",
            attempt_count=1,
        )

        recovered = recover_interrupted_merge_jobs(
            "2027-01-15T02:00:00.000+00:00"
        )

        self.assertEqual(
            recovered,
            (event_id,),
        )

        state = self.read_state(event_id)

        self.assertEqual(
            state["event_status"],
            "READY",
        )
        self.assertEqual(
            state["job_status"],
            "PENDING",
        )
        self.assertEqual(
            state["attempt_count"],
            1,
        )
        self.assertIsNone(
            state["started_at"]
        )
        self.assertIn(
            "서버 재시작",
            state["last_error"],
        )


if __name__ == "__main__":
    unittest.main()
