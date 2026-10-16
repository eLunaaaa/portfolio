from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from app.db import database as database_module
from app.db.database import (
    get_database_connection,
    initialize_database,
)
from app.db.evidence_repository import (
    promote_due_collections,
    register_stored_evidence,
)
from app.models.evidence import (
    EvidenceMetadata,
)


class CollectionPipelineTestCase(
    unittest.TestCase
):
    """
    사고 영상 수집 단계의 상태 전환을 검증함.

    실제 storage/database/ace.db를 사용하지 않고
    테스트마다 임시 SQLite DB를 생성함.

    따라서 테스트 중 오류가 발생해도 실제 사고,
    사용자, 차량, 병합 작업 데이터에는 영향이 없음.
    """

    def setUp(self) -> None:
        """
        각 테스트가 사용할 독립적인 DB를 준비함.

        database.py의 DATABASE_PATH만 잠시 임시 경로로
        바꿈. repository가 호출하는 DB 연결 함수는
        이 값을 사용하므로 운영 DB에 접근하지 않음.
        """

        self.temporary_directory = (
            TemporaryDirectory()
        )

        self.original_database_path = (
            database_module.DATABASE_PATH
        )

        database_module.DATABASE_PATH = (
            Path(
                self.temporary_directory.name
            )
            / "collection-pipeline-test.db"
        )

        initialize_database()

    def tearDown(self) -> None:
        """
        다음 테스트가 원래 DB 경로를 사용하도록
        전역 경로를 반드시 복구함.
        """

        database_module.DATABASE_PATH = (
            self.original_database_path
        )

        self.temporary_directory.cleanup()

    @staticmethod
    def build_metadata(
        event_id: str,
        vehicle_id: str,
    ) -> EvidenceMetadata:
        """
        동일 사고에 참여한 차량별 메타데이터를 만듦.

        event_id, 사고 차량, 사고 시각은 동일하고
        촬영 차량 vehicle_id만 다르게 생성함.
        """

        return EvidenceMetadata(
            event_id=event_id,
            accident_vehicle_id="CAR_01",
            vehicle_id=vehicle_id,
            event_time_ns=(
                1_800_000_005_000_000_000
            ),
            clip_start_time_ns=(
                1_800_000_000_000_000_000
            ),
            clip_end_time_ns=(
                1_800_000_010_000_000_000
            ),
            time_sync_status="SYNCED",
            chrony_offset_ms=1.2,
        )

    def read_pipeline_state(
        self,
        event_id: str,
    ):
        """
        events와 merge_jobs를 함께 조회함.

        테스트 검증용 함수이며 데이터를 변경하지 않음.
        """

        connection = get_database_connection()

        try:
            return connection.execute(
                """
                SELECT
                    events.status
                        AS event_status,
                    events.evidence_count,
                    events.collection_deadline_at,
                    merge_jobs.status
                        AS job_status,
                    merge_jobs.available_at,
                    merge_jobs.attempt_count
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

    def test_collection_becomes_pending_after_deadline(
        self,
    ) -> None:
        """
        차량 영상 2개가 모인 사고는 마감 시각 이후
        READY/PENDING으로 전환되는지 검증함.

        추가 영상이 들어와도 최초 수집 마감 시각이
        연장되지 않는지도 함께 확인함.
        """

        event_id = "EVT_COLLECTION_TEST_001"

        first_received_at = (
            "2027-01-15T00:00:00.000+00:00"
        )

        first_deadline = (
            "2027-01-15T00:00:30.000+00:00"
        )

        first_result = register_stored_evidence(
            event_id=event_id,
            metadata=self.build_metadata(
                event_id,
                "CAR_02",
            ),
            file_path=(
                "storage/originals/test/car02.mp4"
            ),
            received_at=first_received_at,
            collection_deadline_at=first_deadline,
            minimum_evidence_count=2,
        )

        self.assertEqual(
            first_result.event_status,
            "COLLECTING",
        )
        self.assertEqual(
            first_result.merge_job_status,
            "WAITING",
        )
        self.assertEqual(
            first_result.evidence_count,
            1,
        )

        second_result = register_stored_evidence(
            event_id=event_id,
            metadata=self.build_metadata(
                event_id,
                "CAR_03",
            ),
            file_path=(
                "storage/originals/test/car03.mp4"
            ),
            received_at=(
                "2027-01-15T00:00:05.000+00:00"
            ),
            # 두 번째 영상 기준으로 계산한 마감 시각은
            # 더 늦지만 서버는 최초 마감 시각을 유지함.
            collection_deadline_at=(
                "2027-01-15T00:00:35.000+00:00"
            ),
            minimum_evidence_count=2,
        )

        self.assertEqual(
            second_result.event_status,
            "COLLECTING",
        )
        self.assertEqual(
            second_result.merge_job_status,
            "WAITING",
        )
        self.assertEqual(
            second_result.evidence_count,
            2,
        )
        self.assertEqual(
            second_result.collection_deadline_at,
            first_deadline,
        )

        before_deadline = promote_due_collections(
            current_time=(
                "2027-01-15T00:00:29.000+00:00"
            ),
            retry_at=(
                "2027-01-15T00:00:39.000+00:00"
            ),
            minimum_evidence_count=2,
        )

        self.assertEqual(
            before_deadline.checked_count,
            0,
        )

        at_deadline = promote_due_collections(
            current_time=first_deadline,
            retry_at=(
                "2027-01-15T00:00:40.000+00:00"
            ),
            minimum_evidence_count=2,
        )

        self.assertEqual(
            at_deadline.promoted_event_ids,
            (event_id,),
        )
        self.assertEqual(
            at_deadline.waiting_event_ids,
            (),
        )

        state = self.read_pipeline_state(
            event_id
        )

        self.assertEqual(
            state["event_status"],
            "READY",
        )
        self.assertEqual(
            state["job_status"],
            "PENDING",
        )
        self.assertEqual(
            state["evidence_count"],
            2,
        )
        self.assertEqual(
            state["attempt_count"],
            0,
        )

    def test_insufficient_evidence_is_rechecked(
        self,
    ) -> None:
        """
        마감 시각에 영상이 한 개뿐이면 병합하지 않고
        WAITING 상태로 다음 확인 시각을 예약하는지
        검증함.
        """

        event_id = "EVT_COLLECTION_TEST_002"

        deadline = (
            "2027-01-15T01:00:30.000+00:00"
        )

        retry_at = (
            "2027-01-15T01:00:40.000+00:00"
        )

        register_stored_evidence(
            event_id=event_id,
            metadata=self.build_metadata(
                event_id,
                "CAR_02",
            ),
            file_path=(
                "storage/originals/test/car02.mp4"
            ),
            received_at=(
                "2027-01-15T01:00:00.000+00:00"
            ),
            collection_deadline_at=deadline,
            minimum_evidence_count=2,
        )

        refresh_result = promote_due_collections(
            current_time=deadline,
            retry_at=retry_at,
            minimum_evidence_count=2,
        )

        self.assertEqual(
            refresh_result.promoted_event_ids,
            (),
        )
        self.assertEqual(
            refresh_result.waiting_event_ids,
            (event_id,),
        )

        state = self.read_pipeline_state(
            event_id
        )

        self.assertEqual(
            state["event_status"],
            "COLLECTING",
        )
        self.assertEqual(
            state["job_status"],
            "WAITING",
        )
        self.assertEqual(
            state["evidence_count"],
            1,
        )
        self.assertEqual(
            state["available_at"],
            retry_at,
        )

        before_retry = promote_due_collections(
            current_time=(
                "2027-01-15T01:00:35.000+00:00"
            ),
            retry_at=(
                "2027-01-15T01:00:45.000+00:00"
            ),
            minimum_evidence_count=2,
        )

        self.assertEqual(
            before_retry.checked_count,
            0,
        )


if __name__ == "__main__":
    unittest.main()
