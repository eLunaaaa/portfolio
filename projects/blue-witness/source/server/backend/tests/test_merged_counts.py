"""수신 건수가 아닌 현재 병합 버전의 실제 영상 수를 검증함."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from app.services.portal_service import merged_input_vehicle_ids


class MergedCountsTests(unittest.TestCase):
    def test_actual_inputs_exclude_placeholder_and_deduplicate(self):
        with TemporaryDirectory() as directory:
            video = Path(directory) / "merged_extended_r2.mp4"
            plan = video.with_name("merge_plan_extended_r2.json")
            plan.write_text(json.dumps({"inputs": [
                {"vehicle_id": "CAR_02", "is_placeholder": True, "video_path": None},
                {"vehicle_id": "CAR_04", "video_path": "a.mp4"},
                {"vehicle_id": "CAR_04", "video_path": "a.mp4"},
                {"vehicle_id": "CAR_05", "video_path": "b.mp4"},
            ]}), encoding="utf-8")
            self.assertEqual(merged_input_vehicle_ids(video), ["CAR_04", "CAR_05"])

    def test_no_video_is_zero_but_missing_or_broken_plan_is_unknown(self):
        self.assertEqual(merged_input_vehicle_ids(None), [])
        with TemporaryDirectory() as directory:
            video = Path(directory) / "merged_extended_r1.mp4"
            self.assertIsNone(merged_input_vehicle_ids(video))
            video.with_name("merge_plan_extended_r1.json").write_text("broken", encoding="utf-8")
            self.assertIsNone(merged_input_vehicle_ids(video))
