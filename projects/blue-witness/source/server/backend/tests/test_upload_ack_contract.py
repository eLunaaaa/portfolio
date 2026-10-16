from pathlib import Path
import unittest

from pydantic import ValidationError

from app.api.evidence import build_existing_response
from app.db.upload_replay_repository import (
    ExistingEvidenceUpload,
)
from app.models.evidence import EvidenceUploadResponse
from app.services.storage import StoredFile


class UploadAckContractTestCase(unittest.TestCase):
    """
    Pi Queue가 DONE으로 전환할 때 사용하는
    영구 저장 ACK 계약을 검증함.

    운영 DB와 실제 영상 파일은 사용하지 않음.
    """

    @staticmethod
    def response_values() -> dict:
        return {
            "saved": True,
            "storage_ack": True,
            "event_id": "EVT_20260820_153045123_CAR_01",
            "vehicle_id": "CAR_02",
            "upload_status": "STORED",
            "file_path": "storage/test/clip.mp4",
            "file_size_bytes": 1024,
            "sha256": "a" * 64,
            "event_status": "COLLECTING",
            "evidence_count": 1,
            "collection_deadline_at": (
                "2027-01-15T00:00:30.000+00:00"
            ),
            "merge_job_status": "WAITING",
            "idempotent_replay": False,
        }

    def test_success_response_contains_true_storage_ack(self) -> None:
        """성공 JSON에 storage_ack=true가 포함되는지 확인함."""

        response = EvidenceUploadResponse(
            **self.response_values()
        )

        self.assertIs(response.storage_ack, True)
        self.assertIn(
            '"storage_ack":true',
            response.model_dump_json(),
        )
        self.assertEqual(
            response.event_id,
            "EVT_20260820_153045123_CAR_01",
        )

    def test_false_storage_ack_is_rejected(self) -> None:
        """성공 응답에 False ACK를 넣을 수 없음을 확인함."""

        values = self.response_values()
        values["storage_ack"] = False

        with self.assertRaises(ValidationError):
            EvidenceUploadResponse(**values)

    def test_idempotent_replay_also_returns_storage_ack(self) -> None:
        """
        응답 유실 후 동일 영상을 재전송한 경우에도
        기존 저장 결과 확인 후 ACK하는지 검증함.
        """

        existing = ExistingEvidenceUpload(
            event_id="EVT_ACK_001",
            vehicle_id="CAR_02",
            accident_vehicle_id="CAR_01",
            event_time_ns=1_800_000_005_000_000_000,
            clip_start_time_ns=1_800_000_000_000_000_000,
            clip_end_time_ns=1_800_000_010_000_000_000,
            file_path="storage/test/clip.mp4",
            event_status="COLLECTING",
            evidence_count=1,
            collection_deadline_at=(
                "2027-01-15T00:00:30.000+00:00"
            ),
            merge_job_status="WAITING",
        )

        stored_file = StoredFile(
            absolute_path=Path("/tmp/ace-test-clip.mp4"),
            relative_path="storage/test/clip.mp4",
            size_bytes=1024,
            sha256="a" * 64,
            reused_existing=True,
        )

        response = build_existing_response(
            existing=existing,
            stored_file=stored_file,
        )

        self.assertIs(response.storage_ack, True)
        self.assertIs(response.idempotent_replay, True)


if __name__ == "__main__":
    unittest.main()
