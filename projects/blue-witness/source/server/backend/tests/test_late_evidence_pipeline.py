from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from app.db import database as database_module
from app.db.database import (
    get_database_connection,
    initialize_database,
)
from app.db.evidence_repository import (
    register_stored_evidence,
)
from app.db.merge_job_repository import (
    mark_merge_job_completed,
)
from app.models.evidence import EvidenceMetadata
from app.services import portal_service


class LateEvidencePipelineTestCase(unittest.TestCase):
    """
    최초 병합 뒤 늦게 도착한 영상을 재병합하는
    revision 상태 전환을 검증함.

    실제 운영 DB, 원본 영상, FFmpeg는 사용하지 않음.
    """

    def setUp(self) -> None:
        self.temporary_directory = TemporaryDirectory()
        self.temporary_root = Path(
            self.temporary_directory.name
        )
        self.original_database_path = (
            database_module.DATABASE_PATH
        )
        database_module.DATABASE_PATH = (
            self.temporary_root / "late-evidence-test.db"
        )
        initialize_database()

    def tearDown(self) -> None:
        database_module.DATABASE_PATH = (
            self.original_database_path
        )
        self.temporary_directory.cleanup()

    @staticmethod
    def metadata(
        event_id: str,
        vehicle_id: str,
    ) -> EvidenceMetadata:
        return EvidenceMetadata(
            event_id=event_id,
            accident_vehicle_id="CAR_01",
            vehicle_id=vehicle_id,
            event_time_ns=1_800_000_005_000_000_000,
            clip_start_time_ns=1_800_000_000_000_000_000,
            clip_end_time_ns=1_800_000_010_000_000_000,
            time_sync_status="SYNCED",
            chrony_offset_ms=1.0,
        )

    def insert_existing_pipeline(
        self,
        event_id: str,
        event_status: str,
        job_status: str,
        target_revision: int,
        completed_revision: int,
    ) -> None:
        """
        늦은 영상 도착 직전의 기존 사고 상태를 만듦.
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
                    collection_started_at,
                    collection_deadline_at,
                    evidence_count,
                    updated_at
                )
                VALUES (?, ?, 'CAR_01', ?, ?, ?, ?, 2, ?)
                """,
                (
                    event_id,
                    event_id,
                    1_800_000_005_000_000_000,
                    event_status,
                    "2027-01-15T00:00:00.000+00:00",
                    "2027-01-15T00:00:30.000+00:00",
                    "2027-01-15T00:01:00.000+00:00",
                ),
            )

            for vehicle_id in ("CAR_02", "CAR_03"):
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
                        received_at
                    )
                    VALUES (?, ?, ?, ?, ?, 'STORED', 'SYNCED', ?)
                    """,
                    (
                        event_id,
                        vehicle_id,
                        1_800_000_000_000_000_000,
                        1_800_000_010_000_000_000,
                        f"storage/test/{vehicle_id}.mp4",
                        "2027-01-15T00:00:10.000+00:00",
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
                    completed_at,
                    target_revision,
                    completed_revision,
                    updated_at
                )
                VALUES (?, ?, ?, 1, ?, ?, ?, ?, ?)
                """,
                (
                    event_id,
                    job_status,
                    "2027-01-15T00:00:30.000+00:00",
                    (
                        "2027-01-15T00:00:31.000+00:00"
                        if job_status == "RUNNING"
                        else None
                    ),
                    (
                        "2027-01-15T00:01:00.000+00:00"
                        if job_status == "COMPLETED"
                        else None
                    ),
                    target_revision,
                    completed_revision,
                    "2027-01-15T00:01:00.000+00:00",
                ),
            )
            connection.commit()

        finally:
            connection.close()

    @staticmethod
    def read_state(event_id: str):
        connection = get_database_connection()

        try:
            return connection.execute(
                """
                SELECT
                    events.status AS event_status,
                    events.evidence_count,
                    merge_jobs.status AS job_status,
                    merge_jobs.target_revision,
                    merge_jobs.completed_revision,
                    merge_jobs.rerun_requested,
                    merge_jobs.attempt_count
                FROM events
                INNER JOIN merge_jobs
                    ON merge_jobs.event_id = events.event_id
                WHERE events.event_id = ?
                """,
                (event_id,),
            ).fetchone()

        finally:
            connection.close()

    def test_completed_event_is_reopened_for_next_revision(self) -> None:
        event_id = "EVT_LATE_COMPLETED_001"
        self.insert_existing_pipeline(
            event_id,
            "COMPLETED",
            "COMPLETED",
            target_revision=1,
            completed_revision=1,
        )

        result = register_stored_evidence(
            event_id=event_id,
            metadata=self.metadata(event_id, "CAR_04"),
            file_path="storage/test/CAR_04.mp4",
            received_at="2027-01-15T00:02:00.000+00:00",
            collection_deadline_at="2027-01-15T00:02:30.000+00:00",
            minimum_evidence_count=2,
        )

        state = self.read_state(event_id)
        self.assertEqual(result.event_status, "READY")
        self.assertEqual(result.merge_job_status, "PENDING")
        self.assertEqual(state["evidence_count"], 3)
        self.assertEqual(state["target_revision"], 2)
        self.assertEqual(state["completed_revision"], 1)
        self.assertEqual(state["attempt_count"], 0)

    def test_running_job_schedules_rerun_after_completion(self) -> None:
        event_id = "EVT_LATE_RUNNING_001"
        self.insert_existing_pipeline(
            event_id,
            "MERGING",
            "RUNNING",
            target_revision=1,
            completed_revision=0,
        )

        result = register_stored_evidence(
            event_id=event_id,
            metadata=self.metadata(event_id, "CAR_04"),
            file_path="storage/test/CAR_04.mp4",
            received_at="2027-01-15T00:00:40.000+00:00",
            collection_deadline_at="2027-01-15T00:01:10.000+00:00",
            minimum_evidence_count=2,
        )

        running_state = self.read_state(event_id)
        self.assertEqual(result.event_status, "MERGING")
        self.assertEqual(result.merge_job_status, "RUNNING")
        self.assertEqual(running_state["rerun_requested"], 1)

        final_status = mark_merge_job_completed(
            event_id=event_id,
            completed_at="2027-01-15T00:01:00.000+00:00",
            completed_revision=1,
        )

        rerun_state = self.read_state(event_id)
        self.assertEqual(final_status, "PENDING")
        self.assertEqual(rerun_state["event_status"], "READY")
        self.assertEqual(rerun_state["job_status"], "PENDING")
        self.assertEqual(rerun_state["target_revision"], 2)
        self.assertEqual(rerun_state["completed_revision"], 1)
        self.assertEqual(rerun_state["rerun_requested"], 0)
        self.assertEqual(rerun_state["attempt_count"], 0)

    def test_portal_selects_latest_revision_file(self) -> None:
        event_id = "EVT_LATE_PORTAL_001"
        merge_directory = (
            self.temporary_root
            / "storage"
            / "merged"
            / event_id
        )
        merge_directory.mkdir(parents=True)
        (merge_directory / "merged_extended_r1.mp4").write_bytes(b"r1")
        (merge_directory / "merged_extended_r3.mp4").write_bytes(b"r3")
        (merge_directory / "merged_extended.mp4").write_bytes(b"legacy")

        with patch.object(
            portal_service,
            "BACKEND_ROOT",
            self.temporary_root,
        ):
            video_path, merge_mode = (
                portal_service.find_merged_video(event_id)
            )

        self.assertEqual(
            video_path,
            merge_directory / "merged_extended_r3.mp4",
        )
        self.assertEqual(merge_mode, "extended")


if __name__ == "__main__":
    unittest.main()
