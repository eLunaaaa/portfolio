"""Exercise FFmpeg cache/setup failures without Windows or network access."""

import hashlib
import importlib.util
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock


SPEC = importlib.util.spec_from_file_location(
    "setup_windows_ffmpeg", Path(__file__).with_name("setup-windows-ffmpeg.py")
)
SETUP = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SETUP)


class SetupWindowsFfmpegTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.cache = self.root / "cache"
        self.install = self.root / "install"
        self.path_output = self.root / "github-path"
        self.step_output = self.root / "github-output"
        self.payload = b"verified test archive"
        self.commands = []
        self.missing = None
        self.failing = None
        self.wrong_version = None
        self.download_payload = self.payload
        self.download_failure = None
        self.addCleanup(mock.patch.stopall)
        mock.patch.object(SETUP, "SHA256", hashlib.sha256(self.payload).hexdigest()).start()
        mock.patch.object(SETUP.shutil, "which", side_effect=lambda name: name).start()
        mock.patch.object(SETUP.subprocess, "run", side_effect=self.run_command).start()
        mock.patch.dict(os.environ, {
            "GITHUB_PATH": str(self.path_output),
            "GITHUB_OUTPUT": str(self.step_output),
            "FFMPEG_CACHE_DIR": str(self.cache),
            "FFMPEG_INSTALL_DIR": str(self.install),
        }).start()

    def run_command(self, args, **kwargs):
        self.commands.append(args)
        if args[0] in ("curl.exe", "curl"):
            Path(args[args.index("--output") + 1]).write_bytes(self.download_payload)
            if self.download_failure:
                raise self.download_failure
        elif args[0] == "7z":
            bin_dir = self.install / "ffmpeg-release" / "bin"
            bin_dir.mkdir(parents=True)
            for name in ("ffmpeg", "ffprobe"):
                if name != self.missing:
                    (bin_dir / f"{name}.exe").touch()
        else:
            name = Path(args[0]).stem
            if name == self.failing:
                raise subprocess.CalledProcessError(1, args)
            version = "0.0.0" if name == self.wrong_version else SETUP.VERSION
            return subprocess.CompletedProcess(args, 0, f"{name} version {version}-essentials_build\n", "")
        return subprocess.CompletedProcess(args, 0)

    def cache_archive(self, payload=None):
        self.cache.mkdir()
        archive = self.cache / SETUP.ARCHIVE_NAME
        archive.write_bytes(self.payload if payload is None else payload)
        return archive

    def assert_not_exported(self):
        self.assertFalse(self.path_output.exists())
        self.assertFalse(self.step_output.exists())

    def test_cold_cache_downloads_checks_and_exports_both_tools(self):
        bin_dir = SETUP.setup(self.cache, self.install)
        self.assertEqual((self.cache / SETUP.ARCHIVE_NAME).read_bytes(), self.payload)
        self.assertEqual(list(self.cache.glob("*.part")), [])
        self.assertEqual(self.path_output.read_text().strip(), str(bin_dir))
        self.assertEqual(self.step_output.read_text(), "cache-ready=true\n")
        self.assertEqual([Path(command[0]).stem for command in self.commands[-2:]],
                         ["ffmpeg", "ffprobe"])
        self.assertIn(SETUP.URL, self.commands[0])

    def test_cache_hit_verifies_and_never_downloads(self):
        self.cache_archive()
        SETUP.setup(self.cache, self.install)
        self.assertEqual(self.commands[0][0], "7z")
        self.assertFalse(any(command[0].startswith("curl") for command in self.commands))

    def test_corrupt_cache_never_extracts_or_executes(self):
        self.cache_archive(b"corrupt archive")
        with self.assertRaisesRegex(RuntimeError, "SHA-256 mismatch"):
            SETUP.setup(self.cache, self.install)
        self.assertEqual(self.commands, [])
        self.assert_not_exported()

    def test_corrupt_download_never_publishes_or_extracts(self):
        self.download_payload = b"corrupt download"
        with self.assertRaisesRegex(RuntimeError, "SHA-256 mismatch"):
            SETUP.setup(self.cache, self.install)
        self.assertEqual(len(self.commands), 1)
        self.assertEqual(list(self.cache.iterdir()), [])
        self.assert_not_exported()

    def test_interrupted_download_removes_partial_archive(self):
        self.download_failure = subprocess.TimeoutExpired("curl", 120)
        with self.assertRaises(subprocess.TimeoutExpired):
            SETUP.setup(self.cache, self.install)
        self.assertEqual(list(self.cache.iterdir()), [])
        self.assert_not_exported()

    def test_failed_download_removes_partial_archive(self):
        self.download_failure = subprocess.CalledProcessError(22, "curl")
        self.assertEqual(SETUP.main(), 1)
        self.assertEqual(list(self.cache.iterdir()), [])
        self.assert_not_exported()

    def test_missing_ffprobe_cannot_use_an_existing_install(self):
        stale = self.install / "stale" / "bin"
        stale.mkdir(parents=True)
        for name in ("ffmpeg.exe", "ffprobe.exe"):
            (stale / name).touch()
        self.missing = "ffprobe"
        with self.assertRaisesRegex(RuntimeError, "ffmpeg.exe / ffprobe.exe pair"):
            SETUP.setup(self.cache, self.install)
        self.assertFalse(stale.exists())
        self.assert_not_exported()

    def test_ffmpeg_failure_does_not_export_or_mark_cache_ready(self):
        self.failing = "ffmpeg"
        self.assertEqual(SETUP.main(), 1)
        self.assert_not_exported()

    def test_ffprobe_failure_does_not_export_or_mark_cache_ready(self):
        self.failing = "ffprobe"
        self.assertEqual(SETUP.main(), 1)
        self.assert_not_exported()

    def test_wrong_version_does_not_export_or_mark_cache_ready(self):
        self.wrong_version = "ffprobe"
        self.assertEqual(SETUP.main(), 1)
        self.assert_not_exported()

    def test_outputs_are_optional_outside_actions(self):
        os.environ.pop("GITHUB_PATH")
        os.environ.pop("GITHUB_OUTPUT")
        self.assertEqual(SETUP.main(), 0)
        self.assert_not_exported()


if __name__ == "__main__":
    unittest.main()
