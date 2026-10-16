"""원본 보존·시연 차량 제외·보기별 파일 선택과 접근 권한을 검증함."""
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import json
import shutil
import subprocess
from dataclasses import replace
import unittest
from unittest.mock import patch

from fastapi import HTTPException
from app.api import portal
from app.api.dependencies import get_current_user, get_event_access_session
from app.db.merge_repository import EvidenceRecord
from app.main import create_app
from app.services import video_merger, portal_service
from app.services.portal_access_service import EventAccessDeniedError
from test_communication_logs import request


class VideoViewsTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg 필요")
    def test_real_mode_files_exclude_car03_and_share_timeline(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            original = root / "original.mp4"
            subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i",
                            "color=c=blue:s=128x96:r=10:d=0.3", "-c:v", "libx264",
                            "-pix_fmt", "yuv420p", str(original)], check=True)
            original_bytes = original.read_bytes()
            base = 1_780_000_000_000_000_000
            records = [EvidenceRecord(i, "EVT_TEST", "CAR_03", name, base + 200_000_000,
                                      base, base + 300_000_000, "SYNCED", 0, str(original))
                       for i, name in enumerate(["CAR_02", "CAR_03", "CAR_04"])]
            for accident in ("CAR_03", "CAR_02"):
                with patch.object(video_merger, "list_latest_evidence", return_value=[
                    replace(r, accident_vehicle_id=accident) for r in records
                ]), patch.object(video_merger, "excluded_merge_vehicle_ids", return_value={"CAR_03"}), \
                        patch.object(video_merger, "MERGED_ROOT", root / "merged"):
                    result = video_merger.merge_event(f"EVT_{accident}", tile_width=320, fps=10)
                self.assertNotIn("CAR_03", [i["vehicle_id"] for i in result["inputs"]])
                self.assertEqual(set(result["views"]), {"all", "witness"} if accident == "CAR_03"
                                 else {"all", "witness", "own"})
                for view, path in result["views"].items():
                    duration = video_merger.probe_video_duration(Path(path))
                    self.assertAlmostEqual(duration, 0.3, places=2)
                    manifest = Path(path).with_name(Path(path).stem.replace("merged_", "merge_plan_", 1) + ".json")
                    data = json.loads(manifest.read_text())
                    self.assertEqual(data["timeline_start_ns"], result["timeline_start_ns"])
                    self.assertNotIn("CAR_03", [i["vehicle_id"] for i in data["inputs"]])
                    if view == "own":
                        self.assertEqual([i["vehicle_id"] for i in data["inputs"]], [accident])
                    if view == "witness":
                        self.assertFalse(any(i["is_accident_vehicle"] for i in data["inputs"]))
            self.assertEqual(original.read_bytes(), original_bytes)

    def test_exclusion_preserves_original_and_does_not_probe_excluded_file(self):
        with TemporaryDirectory() as directory:
            original = Path(directory) / "clip.mp4"
            original.write_bytes(b"ORIGINAL")
            records = [EvidenceRecord(i, "EVT_TEST", "CAR_03", name, 18_000_000_000,
                                      10_000_000_000, 20_000_000_000, "SYNCED", 0, str(original))
                       for i, name in enumerate(["CAR_02", "CAR_03", "CAR_04"])]
            with patch.object(video_merger, "list_latest_evidence", return_value=records), \
                    patch.object(video_merger, "excluded_merge_vehicle_ids", return_value={"CAR_03"}), \
                    patch.object(video_merger, "probe_video_duration", return_value=10) as probe:
                plan = video_merger.build_merge_plan("EVT_TEST")
            self.assertEqual([i["vehicle_id"] for i in plan["inputs"]], ["CAR_02", "CAR_04"])
            self.assertFalse(any(i["is_placeholder"] for i in plan["inputs"]))
            self.assertEqual(probe.call_count, 2)
            self.assertEqual(original.read_bytes(), b"ORIGINAL")

    def test_variants_must_match_current_revision_and_manifest(self):
        with TemporaryDirectory() as directory:
            root = Path(directory) / "storage/merged/EVT_TEST"
            root.mkdir(parents=True)
            for name in ["merged_extended_r1.mp4", "merged_extended_r1_own.mp4",
                         "merged_extended_r2.mp4", "merged_extended_r2_witness.mp4"]:
                (root / name).write_bytes(b"VIDEO")
            (root / "merge_plan_extended_r2.json").write_text(json.dumps({"views": {"all": "x", "witness": "x"}}))
            with patch.object(portal_service, "BACKEND_ROOT", Path(directory)):
                self.assertEqual(portal_service.available_video_views("EVT_TEST"),
                                 {"all": True, "witness": True, "own": False})
                self.assertIsNone(portal_service.find_video_view("EVT_TEST", "../../secret")[0])
                self.assertEqual(portal_service.find_video_view("EVT_TEST", "witness")[0].name,
                                 "merged_extended_r2_witness.mp4")

    def test_all_views_require_owner_and_access_session(self):
        for view in ("all", "witness", "own"):
            with patch.object(portal, "require_owned_event", side_effect=HTTPException(404)):
                with self.assertRaises(HTTPException) as error:
                    portal.resolve_authorized_merged_video("EVT", SimpleNamespace(id=1), "token", view)
                self.assertEqual(error.exception.status_code, 404)
            with patch.object(portal, "require_owned_event", return_value=SimpleNamespace(event_id="EVT")), \
                    patch.object(portal, "require_event_access", side_effect=EventAccessDeniedError("expired")):
                with self.assertRaises(HTTPException) as error:
                    portal.resolve_authorized_merged_video("EVT", SimpleNamespace(id=1), None, view)
                self.assertEqual(error.exception.status_code, 403)


class VideoViewsApiTests(unittest.IsolatedAsyncioTestCase):
    async def test_routes_reject_anonymous_non_admin_and_invalid_view(self):
        app = create_app()
        for prefix in ("admin", "portal"):
            for suffix in ("video/modes", "video?view=own"):
                path, _, query = f"/api/v1/{prefix}/events/EVT_TEST/{suffix}".partition("?")
                self.assertEqual((await request(app, path, query=query.encode()))[0], 401)
        app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=1, role="USER")
        self.assertEqual((await request(app, "/api/v1/admin/events/EVT_TEST/video/modes"))[0], 403)
        app.dependency_overrides[get_event_access_session] = lambda: "token"
        self.assertEqual((await request(app, "/api/v1/portal/events/EVT_TEST/video", query=b"view=invalid"))[0], 422)

    async def test_missing_variant_is_not_returned_as_another_mode(self):
        app = create_app()
        app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=1, role="ADMIN")
        with patch("app.api.admin.find_video_view", return_value=(None, None)):
            self.assertEqual((await request(app, "/api/v1/admin/events/EVT_TEST/video", query=b"view=own"))[0], 409)
