"""개수별 병합 배치가 빈 칸·겹침 없이 사고 차량을 첫 칸에 유지하는지 검증함."""
import unittest
import json
from pathlib import Path
import shutil
import subprocess
from tempfile import TemporaryDirectory
from unittest.mock import patch

from app.services.video_merger import build_tile_layout, MergeError
from app.services import video_merger


class CompactMergeLayoutTests(unittest.TestCase):
    def test_layouts_fill_canvas_without_overlap(self):
        for count in range(1, 21):
            with self.subTest(count=count):
                tiles = build_tile_layout(count, 640, 480)
                self.assertEqual(len(tiles), count)
                self.assertEqual((tiles[0]["x"], tiles[0]["y"]), (0, 0))
                width = max(t["x"] + t["width"] for t in tiles)
                height = max(t["y"] + t["height"] for t in tiles)
                self.assertEqual(sum(t["width"] * t["height"] for t in tiles), width * height)
                for i, tile in enumerate(tiles):
                    self.assertTrue(all(v % 2 == 0 for v in tile.values()))
                    self.assertAlmostEqual(tile["width"] / tile["height"], 4 / 3, delta=0.01)
                    for other in tiles[i + 1:]:
                        overlap_x = min(tile["x"] + tile["width"], other["x"] + other["width"]) - max(tile["x"], other["x"])
                        overlap_y = min(tile["y"] + tile["height"], other["y"] + other["height"]) - max(tile["y"], other["y"])
                        self.assertFalse(overlap_x > 0 and overlap_y > 0)

    def test_three_highlights_first_tile(self):
        tiles = build_tile_layout(3, 640, 480)
        self.assertEqual((tiles[0]["width"], tiles[0]["height"]), (1280, 960))
        self.assertEqual([t["x"] for t in tiles], [0, 1280, 1280])

    def test_three_witnesses_use_equal_horizontal_tiles(self):
        tiles = build_tile_layout(3, 640, 480, view="witness")
        self.assertEqual([(t["x"], t["y"]) for t in tiles], [(0, 0), (640, 0), (1280, 0)])
        self.assertEqual([(t["width"], t["height"]) for t in tiles], [(640, 480)] * 3)

    def test_five_has_two_then_three(self):
        tiles = build_tile_layout(5, 640, 480)
        self.assertEqual([t["y"] for t in tiles], [0, 0, 720, 720, 720])
        self.assertEqual([t["width"] for t in tiles], [960, 960, 640, 640, 640])

    def test_empty_rejected(self):
        with self.assertRaises(MergeError):
            build_tile_layout(0, 640, 480)

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg 필요")
    def test_real_encodes_one_to_six_and_missing_accident(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            original = root / "original.mp4"
            subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i",
                            "color=c=blue:s=128x96:r=10:d=0.3", "-c:v", "libx264",
                            "-pix_fmt", "yuv420p", str(original)], check=True)
            original_bytes = original.read_bytes()
            for count, missing in [(n, False) for n in range(1, 7)] + [(5, True)]:
                with self.subTest(count=count, missing=missing):
                    inputs = [dict(vehicle_id=f"CAR_{i:02d}", video_path=str(original),
                                   is_accident_vehicle=i == 0, is_placeholder=missing and i == 0,
                                   trim_start_seconds=0, visible_duration_seconds=0.3,
                                   pad_before_seconds=0, pad_after_seconds=0,
                                   visible_start_ns=1780000000000000000) for i in range(count)]
                    plan = dict(inputs=inputs, timeline_duration_seconds=0.3)
                    with patch.object(video_merger, "excluded_merge_vehicle_ids", return_value=set()), \
                            patch.object(video_merger, "build_merge_plan", return_value=plan), \
                            patch.object(video_merger, "MERGED_ROOT", root / "merged"):
                        result = video_merger.merge_event(f"TEST_{count}_{missing}", tile_width=320, fps=10)
                    info = json.loads(subprocess.check_output([
                        "ffprobe", "-v", "error", "-show_entries", "stream=width,height",
                        "-of", "json", result["output"]["path"],
                    ]))["streams"][0]
                    self.assertEqual(info["width"], result["output"]["width"])
                    self.assertEqual(info["height"], result["output"]["height"])
                    if count == 4:
                        witness_path = Path(result["views"]["witness"])
                        witness_plan = json.loads(witness_path.with_name(
                            witness_path.stem.replace("merged_", "merge_plan_", 1) + ".json"
                        ).read_text(encoding="utf-8"))
                        self.assertEqual([tile["y"] for tile in witness_plan["output"]["tiles"]], [0, 0, 0])
                        self.assertEqual((witness_plan["output"]["columns"], witness_plan["output"]["rows"]), (3, 1))
                        self.assertEqual((witness_plan["output"]["width"], witness_plan["output"]["height"]), (960, 240))
                    # 첫 타일 모서리는 노란색이며 다른 타일은 검은색이어야 함.
                    pixels = subprocess.check_output(["ffmpeg", "-v", "error", "-i", result["output"]["path"],
                                                      "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1"])
                    def pixel(x, y):
                        offset = (y * info["width"] + x) * 3
                        return pixels[offset:offset + 3]
                    red, green, blue = pixel(1, 1)
                    self.assertGreater(min(red, green), 200)
                    self.assertLess(blue, 60)
                    if count > 1:
                        tile = result["output"]["tiles"][1]
                        self.assertLess(max(pixel(tile["x"] + 1, tile["y"] + 1)), 60)
            self.assertEqual(original.read_bytes(), original_bytes)
