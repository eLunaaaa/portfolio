from contextlib import closing
import gc
import os
from pathlib import Path
import runpy
import sqlite3
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from app.db import access_repository, database, portal_repository
from app.services import access_service


BACKEND = Path(__file__).resolve().parents[1]
CLIENTS = (
    ("app/services/portal_access_service.py", "connect_database"),
    ("app/cli/reset_admin_password.py", "open_database"),
    ("app/cli/reset_user_password.py", "open_database"),
)


class DatabasePathCompatibilityTests(unittest.TestCase):
    def selected_path(self, root: Path, names: tuple[str, ...]) -> Path:
        # Execute the unchanged production selector against isolated files.
        selector = root / "app/db/database.py"
        selector.parent.mkdir(parents=True)
        selector.write_text(Path(database.__file__).read_text(encoding="utf-8"), encoding="utf-8")
        directory = root / "storage/database"
        directory.mkdir(parents=True)
        for name in names:
            with closing(sqlite3.connect(directory / name)) as connection:
                with connection:
                    connection.execute("CREATE TABLE preserved (value TEXT)")
                    connection.execute("INSERT INTO preserved VALUES (?)", (name,))
        return runpy.run_path(str(selector))["DATABASE_PATH"]

    def test_existing_database_selection_and_contents_are_preserved(self):
        layouts = (
            (("ace.db",), "ace.db"),
            (("evidence.db",), "evidence.db"),
            (("ace.db", "evidence.db"), "evidence.db"),
        )
        for names, expected in layouts:
            with self.subTest(names=names), TemporaryDirectory() as temporary:
                path = self.selected_path(Path(temporary), names)
                self.assertEqual(path.name, expected)
                before = {name: (path.parent / name).read_bytes() for name in names}
                with patch.object(database, "DATABASE_PATH", path):
                    for source, connector in CLIENTS:
                        module = runpy.run_path(str(BACKEND / source))
                        self.assertEqual(module["DATABASE_PATH"], path)
                        with closing(module[connector]()) as connection:
                            row = connection.execute("SELECT value FROM preserved").fetchone()
                            self.assertEqual(row["value"], expected)
                self.assertEqual({entry.name for entry in path.parent.iterdir()}, set(names))
                self.assertEqual(before, {name: (path.parent / name).read_bytes() for name in names})

    def test_access_code_and_portal_use_same_database_on_existing_and_new_install(self):
        for names in (("ace.db",), ()):
            with self.subTest(names=names), TemporaryDirectory() as temporary:
                path = self.selected_path(Path(temporary), names)
                with patch.object(database, "DATABASE_PATH", path), \
                     patch.object(database, "DATABASE_DIRECTORY", path.parent), \
                     patch.object(access_repository, "DATABASE_PATH", path), \
                     patch.object(portal_repository, "DATABASE_PATH", path):
                    database.initialize_database()
                    with closing(database.get_database_connection()) as connection:
                        with connection:
                            connection.execute(
                                "INSERT INTO events(event_id,action_id,accident_vehicle_id,event_time_ns) "
                                "VALUES(?,?,?,?)", ("PATH_TEST", "PATH_TEST", "CAR_02", 1_800_000_000_000_000_000),
                            )
                    portal = runpy.run_path(str(BACKEND / CLIENTS[0][0]))
                    code = access_service.issue_access_code("PATH_TEST", valid_hours=5 / 60)
                    session = access_service.authenticate_access_code("PATH_TEST", code.access_code)
                    self.assertEqual(access_service.authorize_session(session.session_token), "PATH_TEST")
                    self.assertEqual(portal["require_event_access"](session.session_token, "PATH_TEST").event_id, "PATH_TEST")
                    with self.assertRaises(portal["EventAccessDeniedError"]):
                        portal["require_event_access"](session.session_token, "OTHER_EVENT")
                gc.collect()

    def test_missing_database_still_fails_without_creating_a_file(self):
        with TemporaryDirectory() as temporary:
            path = self.selected_path(Path(temporary), ())
            with patch.object(database, "DATABASE_PATH", path):
                for source, connector in CLIENTS:
                    module = runpy.run_path(str(BACKEND / source))
                    with self.assertRaises(RuntimeError):
                        module[connector]()
            self.assertFalse(path.exists())

    def test_password_tools_still_support_direct_file_and_module_execution(self):
        env = os.environ.copy()
        dependency_paths = [entry for entry in sys.path if entry
                            and not Path(entry).resolve().is_relative_to(BACKEND)]
        env["PYTHONIOENCODING"] = "utf-8"
        with TemporaryDirectory() as temporary:
            for source, _ in CLIENTS[1:]:
                module_name = source.removesuffix(".py").replace("/", ".")
                for mode in ("file", "module"):
                    with self.subTest(source=source, mode=mode):
                        env["PYTHONPATH"] = os.pathsep.join(
                            ([str(BACKEND)] if mode == "module" else []) + dependency_paths)
                        command = [sys.executable, "-B"] + (
                            [str(BACKEND / source)] if mode == "file" else ["-m", module_name])
                        # Empty username exits before any DB or password change.
                        result = subprocess.run(command, cwd=temporary, env=env, input="\n",
                                                text=True, encoding="utf-8", capture_output=True, timeout=10)
                        self.assertEqual(result.returncode, 1, result.stderr)
                        self.assertIn("아이디가 비어 있음", result.stdout)
                        self.assertNotIn("Traceback", result.stderr)


if __name__ == "__main__":
    unittest.main()
