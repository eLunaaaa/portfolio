import unittest

from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException

from app.api.evidence import (
    get_evidence_storage_status,
)
from app.db.upload_replay_repository import (
    ExistingEvidenceUpload,
)
from app.services.node_auth_service import (
    AuthenticatedNode,
)
from app.services.storage import (
    FileChecksumMismatchError,
    StoredFile,
)


TEST_SHA256 = "a" * 64


def build_existing_upload() -> ExistingEvidenceUpload:
    """
    저장 상태 API 테스트에 사용할
    SQLite 조회 결과 모형을 생성함.
    """

    return ExistingEvidenceUpload(
        event_id="EVT_20260820_153045123_CAR_01",
        vehicle_id="CAR_02",
        accident_vehicle_id="CAR_01",
        event_time_ns=1_000,
        clip_start_time_ns=900,
        clip_end_time_ns=1_100,
        file_path=(
            "storage/originals/"
            "EVT_20260820_153045123_CAR_01/CAR_02/clip.mp4"
        ),
        event_status="COLLECTING",
        evidence_count=1,
        collection_deadline_at=(
            "2026-08-14T12:00:00+00:00"
        ),
        merge_job_status="PENDING",
    )


def build_stored_file() -> StoredFile:
    """
    디스크 무결성 검사를 통과한
    MP4 정보 모형을 생성함.
    """

    relative_path = (
        "storage/originals/"
        "EVT_20260820_153045123_CAR_01/CAR_02/clip.mp4"
    )

    return StoredFile(
        absolute_path=Path(
            "/tmp/evidence-status-test.mp4"
        ),
        relative_path=relative_path,
        size_bytes=123_456,
        sha256=TEST_SHA256,
        reused_existing=True,
    )


class EvidenceStorageStatusTest(
    unittest.TestCase
):
    """
    ACK 유실 복구 API의 핵심 계약을 검사함.
    """

    def setUp(self) -> None:
        self.authenticated_node = (
            AuthenticatedNode(
                node_id="NODE_PI_02",
                vehicle_id="CAR_02",
            )
        )

    @patch(
        "app.api.evidence.inspect_stored_mp4"
    )
    @patch(
        "app.api.evidence."
        "find_existing_evidence_upload"
    )
    def test_returns_ack_for_matching_file(
        self,
        find_existing_mock,
        inspect_mock,
    ) -> None:
        """
        DB와 실제 파일·해시가 모두 맞으면
        Pi가 DONE 처리할 수 있는 ACK를 반환함.
        """

        find_existing_mock.return_value = (
            build_existing_upload()
        )
        inspect_mock.return_value = (
            build_stored_file()
        )

        result = get_evidence_storage_status(
            event_id="EVT_20260820_153045123_CAR_01",
            content_sha256=TEST_SHA256,
            authenticated_node=(
                self.authenticated_node
            ),
        )

        self.assertTrue(result.stored)
        self.assertTrue(result.storage_ack)
        self.assertEqual(
            result.vehicle_id,
            "CAR_02",
        )
        self.assertEqual(
            result.sha256,
            TEST_SHA256,
        )

    @patch(
        "app.api.evidence."
        "find_existing_evidence_upload"
    )
    def test_returns_404_when_not_stored(
        self,
        find_existing_mock,
    ) -> None:
        """
        STORED DB 기록이 없으면 Pi가 정상 업로드를
        시도할 수 있도록 404를 반환함.
        """

        find_existing_mock.return_value = None

        with self.assertRaises(
            HTTPException
        ) as raised:
            get_evidence_storage_status(
                event_id="EVT_20260820_153045123_CAR_01",
                content_sha256=TEST_SHA256,
                authenticated_node=(
                    self.authenticated_node
                ),
            )

        self.assertEqual(
            raised.exception.status_code,
            404,
        )

    @patch(
        "app.api.evidence.inspect_stored_mp4"
    )
    @patch(
        "app.api.evidence."
        "find_existing_evidence_upload"
    )
    def test_rejects_checksum_mismatch(
        self,
        find_existing_mock,
        inspect_mock,
    ) -> None:
        """
        서버 파일과 Pi 파일의 해시가 다르면
        잘못된 DONE 처리를 막기 위해 409를 반환함.
        """

        find_existing_mock.return_value = (
            build_existing_upload()
        )
        inspect_mock.side_effect = (
            FileChecksumMismatchError(
                "SHA-256 불일치"
            )
        )

        with self.assertRaises(
            HTTPException
        ) as raised:
            get_evidence_storage_status(
                event_id="EVT_20260820_153045123_CAR_01",
                content_sha256=TEST_SHA256,
                authenticated_node=(
                    self.authenticated_node
                ),
            )

        self.assertEqual(
            raised.exception.status_code,
            409,
        )

    def test_rejects_invalid_sha256(self) -> None:
        """
        잘못된 해시 형식은 DB·파일 조회 전에
        422로 거부함.
        """

        with self.assertRaises(
            HTTPException
        ) as raised:
            get_evidence_storage_status(
                event_id="EVT_20260820_153045123_CAR_01",
                content_sha256="not-a-sha256",
                authenticated_node=(
                    self.authenticated_node
                ),
            )

        self.assertEqual(
            raised.exception.status_code,
            422,
        )
