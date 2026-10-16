"""Regression checks for the server CI media-tool preflight (no downloads)."""
import contextlib
import importlib.util
import io
from pathlib import Path
import subprocess
import unittest
from unittest.mock import call, patch


SPEC = importlib.util.spec_from_file_location(
    "check_server", Path(__file__).with_name("check-server.py"),
)
CHECK_SERVER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CHECK_SERVER)


class MediaToolPreflightTests(unittest.TestCase):
    def test_both_tools_are_resolved_and_executed_with_a_timeout(self):
        paths = {name: f"/cached/bin/{name}" for name in ("ffmpeg", "ffprobe")}
        output = io.StringIO()
        with patch.object(CHECK_SERVER.shutil, "which", side_effect=paths.get) as which, \
                patch.object(CHECK_SERVER.subprocess, "run", side_effect=[
                    subprocess.CompletedProcess([], 0, "ffmpeg version pinned\nconfiguration: long\n", ""),
                    subprocess.CompletedProcess([], 0, "ffprobe version pinned\nconfiguration: long\n", ""),
                ]) as run, contextlib.redirect_stdout(output):
            CHECK_SERVER.verify_media_tools()
        self.assertEqual(which.call_args_list, [call("ffmpeg"), call("ffprobe")])
        self.assertEqual(run.call_args_list, [
            call([paths[name], "-version"], check=True, capture_output=True, text=True, timeout=10)
            for name in paths
        ])
        self.assertEqual(output.getvalue(), "ffmpeg version pinned\nffprobe version pinned\n")

    def test_either_missing_tool_stops_before_executing_anything(self):
        for missing in ("ffmpeg", "ffprobe"):
            with self.subTest(missing=missing), \
                    patch.object(CHECK_SERVER.shutil, "which", side_effect=lambda name: (
                        None if name == missing else f"/cached/bin/{name}"
                    )), patch.object(CHECK_SERVER.subprocess, "run") as run:
                with self.assertRaisesRegex(RuntimeError, f"missing from PATH: {missing}"):
                    CHECK_SERVER.verify_media_tools()
                run.assert_not_called()

    def test_both_missing_tools_are_reported(self):
        with patch.object(CHECK_SERVER.shutil, "which", return_value=None), \
                patch.object(CHECK_SERVER.subprocess, "run") as run:
            with self.assertRaisesRegex(RuntimeError, "missing from PATH: ffmpeg, ffprobe"):
                CHECK_SERVER.verify_media_tools()
            run.assert_not_called()

    def test_failing_executable_is_reported_for_either_tool(self):
        for failed in ("ffmpeg", "ffprobe"):
            with self.subTest(failed=failed):
                def run(command, **kwargs):
                    if command[0].endswith(failed):
                        raise subprocess.CalledProcessError(7, command)
                    return subprocess.CompletedProcess(command, 0, "version\n", "")

                with patch.object(CHECK_SERVER.shutil, "which", side_effect=lambda name: f"/bin/{name}"), \
                        patch.object(CHECK_SERVER.subprocess, "run", side_effect=run), \
                        contextlib.redirect_stdout(io.StringIO()):
                    with self.assertRaisesRegex(RuntimeError, f"{failed} -version failed with exit code 7"):
                        CHECK_SERVER.verify_media_tools()

    def test_timed_out_executable_is_reported(self):
        with patch.object(CHECK_SERVER.shutil, "which", side_effect=lambda name: f"/bin/{name}"), \
                patch.object(CHECK_SERVER.subprocess, "run", side_effect=subprocess.TimeoutExpired("ffmpeg", 10)):
            with self.assertRaisesRegex(RuntimeError, "ffmpeg -version timed out after 10 seconds"):
                CHECK_SERVER.verify_media_tools()

    def test_unlaunchable_executable_is_reported(self):
        with patch.object(CHECK_SERVER.shutil, "which", side_effect=lambda name: f"/bin/{name}"), \
                patch.object(CHECK_SERVER.subprocess, "run", side_effect=OSError("invalid executable")):
            with self.assertRaisesRegex(RuntimeError, "Cannot execute ffmpeg: invalid executable"):
                CHECK_SERVER.verify_media_tools()


class ServerModeTests(unittest.TestCase):
    def test_quick_and_integration_preflight_before_contracts(self):
        for mode in ("quick", "integration"):
            events = []
            with self.subTest(mode=mode), \
                    patch.object(CHECK_SERVER.sys, "argv", ["check-server.py", mode]), \
                    patch.object(CHECK_SERVER, "verify_media_tools", side_effect=lambda: events.append("tools")), \
                    patch.object(CHECK_SERVER, "contracts", side_effect=lambda: events.append("contracts")), \
                    patch.object(CHECK_SERVER, "health", side_effect=lambda tls: events.append(f"health:{tls}")), \
                    patch.object(CHECK_SERVER, "merge_fixture", side_effect=lambda: events.append("merge")):
                CHECK_SERVER.main()
            expected = ["tools", "contracts", "health:False", "health:True"]
            if mode == "integration":
                expected.append("merge")
            self.assertEqual(events, expected)

    def test_preflight_failure_prevents_contracts_and_health(self):
        with patch.object(CHECK_SERVER.sys, "argv", ["check-server.py", "quick"]), \
                patch.object(CHECK_SERVER, "verify_media_tools", side_effect=RuntimeError("missing tools")), \
                patch.object(CHECK_SERVER, "contracts") as contracts, \
                patch.object(CHECK_SERVER, "health") as health:
            with self.assertRaisesRegex(RuntimeError, "missing tools"):
                CHECK_SERVER.main()
            contracts.assert_not_called()
            health.assert_not_called()

    def test_serve_bypasses_media_preflight(self):
        with patch.object(CHECK_SERVER.sys, "argv", [
            "check-server.py", "serve", "--database", "fixture.db", "--port", "8000",
        ]), patch.object(CHECK_SERVER, "serve") as serve, \
                patch.object(CHECK_SERVER, "verify_media_tools") as verify, \
                patch.object(CHECK_SERVER, "contracts") as contracts:
            CHECK_SERVER.main()
            serve.assert_called_once()
            verify.assert_not_called()
            contracts.assert_not_called()


if __name__ == "__main__":
    unittest.main()
