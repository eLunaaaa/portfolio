from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from app.db import database as database_module
from app.db.database import initialize_database
from app.services.event_identity_service import (
    EventIdentityConflictError,
    normalize_event_id,
    normalize_accident_vehicle_id,
    resolve_or_create_event,
)
from app.services.storage import build_video_path


KST = timezone(timedelta(hours=9))


class EventIdentityTestCase(unittest.TestCase):
    """Pi가 보낸 event_id를 그대로 사용하는 규칙을 검증함."""

    def setUp(self) -> None:
        self.temporary_directory = TemporaryDirectory()
        self.original_database_path = database_module.DATABASE_PATH
        database_module.DATABASE_PATH = (
            Path(self.temporary_directory.name) / "event-identity.db"
        )
        initialize_database()

        self.event_time_ns = int(
            datetime(
                2026, 8, 20, 15, 30, 45,
                tzinfo=KST,
            ).timestamp()
            * 1_000_000_000
        )

    def tearDown(self) -> None:
        database_module.DATABASE_PATH = self.original_database_path
        self.temporary_directory.cleanup()

    def test_same_event_id_maps_to_same_event(self) -> None:
        event_id = "EVT_20260820_153045123_CAR_01"
        first = resolve_or_create_event(
            event_id=event_id,
            accident_vehicle_id="CAR_01",
            event_time_ns=self.event_time_ns,
        )
        second = resolve_or_create_event(
            event_id=event_id,
            accident_vehicle_id="CAR_01",
            event_time_ns=self.event_time_ns + 1_000_000_000,
        )

        self.assertEqual(
            first.event_id,
            event_id,
        )
        self.assertEqual(first.event_id, second.event_id)
        self.assertTrue(first.created)
        self.assertFalse(second.created)

    def test_large_time_difference_is_rejected(self) -> None:
        resolve_or_create_event(
            event_id="EVT_20260820_153045123_CAR_01",
            accident_vehicle_id="CAR_01",
            event_time_ns=self.event_time_ns,
        )

        with self.assertRaises(EventIdentityConflictError):
            resolve_or_create_event(
                event_id="EVT_20260820_153045123_CAR_01",
                accident_vehicle_id="CAR_01",
                event_time_ns=self.event_time_ns + 3_000_000_000,
            )

    def test_original_video_path_uses_kst_date(self) -> None:
        path = build_video_path(
            event_id="EVT_20260820_153045123_CAR_01",
            vehicle_id="CAR_03",
            event_time_ns=self.event_time_ns,
        )

        self.assertEqual(
            path.parts[-7:],
            (
                "originals",
                "2026",
                "08",
                "20",
                "EVT_20260820_153045123_CAR_01",
                "CAR_03",
                "clip.mp4",
            ),
        )

    def test_numeric_accident_vehicle_id_is_normalized(self) -> None:
        self.assertEqual(
            normalize_accident_vehicle_id("1"),
            "CAR_01",
        )
        self.assertEqual(
            normalize_accident_vehicle_id("15"),
            "CAR_15",
        )

    def test_invalid_event_id_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            normalize_event_id("65537")

    def test_event_id_without_vehicle_underscore_is_accepted(self) -> None:
        self.assertEqual(
            normalize_event_id("EVT_20260820_153045123_CAR01"),
            "EVT_20260820_153045123_CAR01",
        )

    def test_event_id_vehicle_mismatch_is_rejected(self) -> None:
        with self.assertRaises(EventIdentityConflictError):
            resolve_or_create_event(
                event_id="EVT_20260820_153045123_CAR02",
                accident_vehicle_id="CAR_01",
                event_time_ns=self.event_time_ns,
            )

    def test_event_id_time_mismatch_is_rejected(self) -> None:
        with self.assertRaises(EventIdentityConflictError):
            resolve_or_create_event(
                event_id="EVT_20260820_153055123_CAR01",
                accident_vehicle_id="CAR_01",
                event_time_ns=self.event_time_ns,
            )


if __name__ == "__main__":
    unittest.main()
