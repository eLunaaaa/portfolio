from contextlib import ExitStack, closing
from pathlib import Path
from tempfile import TemporaryDirectory
import gc
import sqlite3
import unittest
from unittest.mock import patch

from app.db import database, portal_repository
from app.services import admin_cleanup_service as cleanup, storage


class AdminCleanupTestCase(unittest.TestCase):
    EVENT = "EVT_20261010_120000000_CAR02"
    OTHER_EVENT = "EVT_20261010_120001000_CAR02"
    EVENT_TIME = 1_791_601_200_000_000_000

    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(TemporaryDirectory()))
        db_dir = self.root / "storage/database"
        db_path = db_dir / "ace.db"
        self.stack.enter_context(patch.multiple(
            database, DATABASE_DIRECTORY=db_dir, DATABASE_PATH=db_path,
        ))
        self.stack.enter_context(patch.object(portal_repository, "DATABASE_PATH", db_path))
        self.stack.enter_context(patch.multiple(
            cleanup,
            BACKEND_ROOT=self.root,
            STORAGE_ROOT=self.root / "storage",
            ORIGINALS_ROOT=self.root / "storage/originals",
            MERGED_ROOT=self.root / "storage/merged",
            TRASH_ROOT=self.root / "storage/trash",
        ))
        self.stack.enter_context(patch.multiple(
            storage, PROJECT_ROOT=self.root,
            ORIGINALS_DIRECTORY=cleanup.ORIGINALS_ROOT,
        ))
        # 기존 SQLite context manager에서 남은 연결을 Windows 정리 전에 회수함.
        self.stack.callback(gc.collect)
        database.initialize_database()
        with closing(database.get_database_connection()) as con, con:
            for event_id in (self.EVENT, self.OTHER_EVENT):
                con.execute(
                    "INSERT INTO events(event_id,action_id,accident_vehicle_id,event_time_ns) "
                    "VALUES(?,?,?,?)",
                    (event_id, event_id, "CAR_02", self.EVENT_TIME),
                )

    def add_clip(self, vehicle="CAR_02", event=None, timestamp=EVENT_TIME):
        event = event or self.EVENT
        clip = storage.build_video_path(event, vehicle, timestamp)
        clip.parent.mkdir(parents=True, exist_ok=True)
        clip.write_bytes(f"{event}/{vehicle}/{timestamp}".encode())
        with closing(database.get_database_connection()) as con, con:
            con.execute(
                "INSERT INTO evidence(event_id,vehicle_id,clip_start_time_ns,"
                "clip_end_time_ns,file_path,upload_status) VALUES(?,?,?,?,?,'STORED')",
                (event, vehicle, self.EVENT_TIME - 8_000_000_000,
                 self.EVENT_TIME + 2_000_000_000, clip.relative_to(self.root).as_posix()),
            )
        return clip

    def add_merge(self):
        merged = cleanup.MERGED_ROOT / self.EVENT / "merged_extended_r1.mp4"
        merged.parent.mkdir(parents=True)
        merged.write_bytes(b"merged video")
        return merged

    def count(self, table, event=None):
        with closing(database.get_database_connection()) as con:
            return con.execute(
                f"SELECT COUNT(*) FROM {table} WHERE event_id=?",
                (event or self.EVENT,),
            ).fetchone()[0]

    def test_delete_moves_dated_legacy_and_multiple_day_folders_then_purges(self):
        clips = [
            self.add_clip(),
            self.add_clip("CAR_04"),
            self.add_clip("CAR_05", timestamp=None),
            self.add_clip("CAR_06", timestamp=self.EVENT_TIME + 86_400_000_000_000),
        ]
        # Event folder contents, including sidecars, travel together.
        sidecar = clips[0].with_suffix(".json")
        sidecar.write_bytes(b"metadata")
        original_files = clips + [sidecar, self.add_merge()]
        contents = {p: p.read_bytes() for p in original_files}
        unrelated = self.add_clip(event=self.OTHER_EVENT)
        unrelated_contents = unrelated.read_bytes()

        response = cleanup.delete_event(self.EVENT, self.EVENT)

        self.assertTrue(response.success)
        self.assertEqual(response.deleted_counts["evidence"], 4)
        self.assertEqual(self.count("events"), 0)
        self.assertEqual(self.count("evidence"), 0)
        trash = self.root / response.trash_location
        for path, payload in contents.items():
            self.assertFalse(path.exists())
            destination = trash / path.relative_to(cleanup.STORAGE_ROOT)
            self.assertEqual(destination.read_bytes(), payload)
        self.assertEqual(unrelated.read_bytes(), unrelated_contents)
        self.assertEqual(self.count("events", self.OTHER_EVENT), 1)

        cleanup.purge_trash("PURGE SERVER TRASH")
        self.assertFalse(trash.exists())
        self.assertEqual(unrelated.read_bytes(), unrelated_contents)

    def test_db_failure_restores_all_files_and_rolls_back_rows(self):
        paths = [self.add_clip(), self.add_clip("CAR_04", timestamp=None), self.add_merge()]
        contents = {p: p.read_bytes() for p in paths}
        with closing(database.get_database_connection()) as con, con:
            con.execute(
                "CREATE TRIGGER reject_event_delete BEFORE DELETE ON events "
                "BEGIN SELECT RAISE(ABORT, 'test deletion failure'); END"
            )

        with self.assertRaises(sqlite3.IntegrityError):
            cleanup.delete_event(self.EVENT, self.EVENT)

        self.assertEqual(self.count("events"), 1)
        self.assertEqual(self.count("evidence"), 2)
        for path, payload in contents.items():
            self.assertEqual(path.read_bytes(), payload)
        self.assertFalse(any(p.is_file() for p in cleanup.TRASH_ROOT.rglob("*")))

    def test_move_failure_restores_already_staged_originals(self):
        paths = [self.add_clip(), self.add_clip("CAR_04", timestamp=None), self.add_merge()]
        contents = {p: p.read_bytes() for p in paths}
        original_move = cleanup.move_path_to_trash

        def fail_merge(source, operation, category):
            if category == "merged":
                raise OSError("test move failure")
            return original_move(source, operation, category)

        with patch.object(cleanup, "move_path_to_trash", side_effect=fail_merge):
            with self.assertRaises(OSError):
                cleanup.delete_event(self.EVENT, self.EVENT)

        self.assertEqual(self.count("events"), 1)
        self.assertEqual(self.count("evidence"), 2)
        for path, payload in contents.items():
            self.assertEqual(path.read_bytes(), payload)

    def test_invalid_stored_paths_do_not_move_any_files_or_delete_records(self):
        clip = self.add_clip()
        legacy = self.add_clip("CAR_04", timestamp=None)
        unrelated = self.add_clip(event=self.OTHER_EVENT)
        outside = self.root / "outside.mp4"
        outside.write_bytes(b"keep outside")
        for bad_path in (
            "storage/originals/../../outside.mp4",
            unrelated.relative_to(self.root).as_posix(),
            f"storage/originals/{self.EVENT}/clip.mp4",
        ):
            with self.subTest(path=bad_path):
                with closing(database.get_database_connection()) as con, con:
                    con.execute(
                        "UPDATE evidence SET file_path=? WHERE event_id=? AND vehicle_id='CAR_02'",
                        (bad_path, self.EVENT),
                    )
                with self.assertRaises(ValueError):
                    cleanup.delete_event(self.EVENT, self.EVENT)
                self.assertEqual(self.count("events"), 1)
                self.assertEqual(self.count("evidence"), 2)
                for path in (clip, legacy, unrelated, outside):
                    self.assertTrue(path.exists())

    def test_missing_original_file_does_not_block_existing_delete_behavior(self):
        clip = self.add_clip()
        clip.unlink()
        merged = self.add_merge()
        self.assertTrue(cleanup.delete_event(self.EVENT, self.EVENT).success)
        self.assertEqual(self.count("events"), 0)
        self.assertFalse(merged.exists())

    def test_legacy_folder_without_evidence_row_is_still_moved(self):
        clip = storage.build_video_path(self.EVENT, "CAR_02")
        clip.parent.mkdir(parents=True)
        clip.write_bytes(b"legacy video")
        response = cleanup.delete_event(self.EVENT, self.EVENT)
        self.assertFalse(clip.exists())
        self.assertEqual(
            (self.root / response.trash_location / clip.relative_to(cleanup.STORAGE_ROOT)).read_bytes(),
            b"legacy video",
        )


if __name__ == "__main__":
    unittest.main()
