import unittest

from app.models.evidence import EvidenceMetadata
from app.services.timestamp_normalization import (
    normalize_evidence_timestamps,
)


class TimestampNormalizationTestCase(unittest.TestCase):
    """업로더 Epoch 단위를 서버 표준 ns로 바꾸는 규칙을 검증함."""

    def test_millisecond_epoch_is_converted_to_nanoseconds(self) -> None:
        event_ms = 1_787_233_523_708

        metadata = EvidenceMetadata(
            event_id="EVT_20260820_153045123_CAR_01",
            accident_vehicle_id="CAR_01",
            vehicle_id="CAR_03",
            event_time_ns=event_ms,
            clip_start_time_ns=event_ms - 10_000,
            clip_end_time_ns=event_ms,
        )

        self.assertEqual(
            metadata.event_time_ns,
            1_787_233_523_708_000_000,
        )
        self.assertEqual(
            metadata.event_time_ns - metadata.clip_start_time_ns,
            10_000_000_000,
        )

    def test_millisecond_epoch_with_nanosecond_offsets_is_repaired(self) -> None:
        event_ms = 1_787_233_523_708

        normalized = normalize_evidence_timestamps(
            event_time=event_ms,
            clip_start_time=event_ms - 10_000_000_000,
            clip_end_time=event_ms,
        )

        self.assertEqual(
            normalized.strategy,
            "event-epoch-with-ns-offsets",
        )
        self.assertEqual(
            normalized.event_time_ns - normalized.clip_start_time_ns,
            10_000_000_000,
        )

    def test_nanosecond_epoch_remains_unchanged(self) -> None:
        event_ns = 1_787_233_523_708_000_000

        normalized = normalize_evidence_timestamps(
            event_time=event_ns,
            clip_start_time=event_ns - 10_000_000_000,
            clip_end_time=event_ns,
        )

        self.assertFalse(normalized.changed)
        self.assertEqual(normalized.event_time_ns, event_ns)


if __name__ == "__main__":
    unittest.main()
