#!/usr/bin/env python3
"""Exercise cache lifecycle without installing packages or accessing the network."""

import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


SCRIPT = Path(__file__).with_name("install-host-deps.sh")
REQUIRED = [
    "gcc-14-aarch64-linux-gnu",
    "g++-14-aarch64-linux-gnu",
    "cppcheck",
    "pkg-config",
    "libglib2.0-dev",
]
DEPENDENCY = "libcompiler-runtime"
MOCK = r'''#!/usr/bin/env python3
import json
import os
from pathlib import Path
import sys

tool = Path(sys.argv[0]).name
args = sys.argv[1:]
if tool == "sudo":
    os.execvp(args[0], args)

state_path = Path(os.environ["MOCK_STATE"])
state = json.loads(state_path.read_text())
if tool == "dpkg-query":
    if args[-1] in state["installed"]:
        print("install ok installed", end="")
        sys.exit(0)
    sys.exit(1)

if tool != "apt-get":
    raise SystemExit("Unexpected command: " + tool)
with Path(os.environ["MOCK_LOG"]).open("a") as log:
    log.write(json.dumps(args) + "\n")
if "update" in args:
    sys.exit(0)
if "--download-only" in args:
    if state.get("download_fail"):
        sys.exit(20)
    archives = Path(next(a.split("=", 1)[1] for a in args
                         if a.startswith("Dir::Cache::archives=")))
    packages = args[args.index("install") + 1:] + ["libcompiler-runtime"]
    for package in packages:
        if package == "libcompiler-runtime" and state.get("missing_dep"):
            continue
        (archives / (package + "_1.0_amd64.deb")).write_text(package)
    (archives / "partial").mkdir()
    (archives / "lock").touch()
    sys.exit(0)
if "--no-download" not in args:
    raise SystemExit("Installation allowed a network download")
local_files = [Path(p) for p in args[args.index("install") + 1:]]
archive_dir = next((a.split("=", 1)[1] for a in args
                    if a.startswith("Dir::Cache::archives=")), None)
if archive_dir is None or any(p.parent != Path(archive_dir) for p in local_files):
    raise SystemExit("APT archive directory does not contain the local packages")
packages = [p.read_text() for p in local_files]
if "libcompiler-runtime" not in packages:
    raise SystemExit("Missing transitive dependency; network access is disabled")
state["installed"] = sorted(set(state["installed"] + packages))
state_path.write_text(json.dumps(state))
'''


class HostDependencyCacheTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        for name in ("sudo", "apt-get", "dpkg-query"):
            executable = self.bin / name
            executable.write_text(MOCK)
            executable.chmod(0o755)
        self.cache = self.root / "host-debs"
        self.state = self.root / "state.json"
        self.log = self.root / "apt.jsonl"
        self.output = self.root / "output"
        self.set_state()

    def set_state(self, installed=(), **options):
        self.state.write_text(json.dumps({"installed": list(installed), **options}))

    def run_script(self, hit=False):
        env = {
            **os.environ,
            "PATH": str(self.bin) + os.pathsep + os.environ["PATH"],
            "HOST_DEB_CACHE_DIR": str(self.cache),
            "HOST_DEB_CACHE_HIT": str(hit).lower(),
            "GITHUB_OUTPUT": str(self.output),
            "MOCK_STATE": str(self.state),
            "MOCK_LOG": str(self.log),
        }
        return subprocess.run(["bash", str(SCRIPT)], env=env, text=True,
                              capture_output=True, timeout=15)

    def calls(self):
        return [json.loads(line) for line in self.log.read_text().splitlines()] \
            if self.log.exists() else []

    def make_cache(self):
        self.cache.mkdir()
        lines = []
        for package in REQUIRED + [DEPENDENCY]:
            path = self.cache / (package + "_1.0_amd64.deb")
            path.write_text(package)
            lines.append(hashlib.sha256(path.read_bytes()).hexdigest() + "  " + path.name)
        (self.cache / "SHA256SUMS").write_text("\n".join(lines) + "\n")

    def assert_not_ready(self):
        self.assertFalse(self.output.exists() and
                         "cache-ready=true" in self.output.read_text())

    def test_cold_cache_downloads_dependencies_and_saves_only_complete_archives(self):
        # An existing tool must not need another download on this runner image.
        self.set_state(installed=["pkg-config"])
        result = self.run_script()
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = self.calls()
        self.assertEqual(len(calls), 3)
        self.assertIn("update", calls[0])
        self.assertIn("APT::Update::Error-Mode=any", calls[0])
        self.assertIn("--download-only", calls[1])
        self.assertNotIn("pkg-config", calls[1])
        self.assertIn("--no-download", calls[2])
        self.assertIn("APT::Keep-Downloaded-Packages=true", calls[2])
        for call in calls:
            self.assertIn("Acquire::Retries=1", call)
            self.assertIn("Acquire::http::Timeout=15", call)
            self.assertIn("Acquire::https::Timeout=15", call)
        self.assertTrue((self.cache / (DEPENDENCY + "_1.0_amd64.deb")).exists())
        self.assertEqual(len(list(self.cache.glob("*.deb"))), len(REQUIRED))
        self.assertTrue((self.cache / "SHA256SUMS").exists())
        self.assertFalse((self.cache / "partial").exists())
        self.assertFalse((self.cache / "lock").exists())
        self.assertEqual(self.output.read_text(), "cache-ready=true\n")
        self.assertEqual(list(self.root.glob("host-debs.download.*")), [])

    def test_cache_hit_installs_offline_without_updating_indexes(self):
        self.make_cache()
        result = self.run_script(hit=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = self.calls()
        self.assertEqual(len(calls), 1)
        self.assertIn("--no-download", calls[0])
        self.assertIn("APT::Keep-Downloaded-Packages=true", calls[0])
        self.assertNotIn("update", calls[0])
        self.assertNotIn("--download-only", calls[0])
        self.assertEqual(self.output.read_text(), "cache-ready=true\n")

    def test_all_tools_preinstalled_needs_neither_apt_nor_a_cache(self):
        self.set_state(installed=REQUIRED)
        result = self.run_script()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.calls(), [])
        self.assert_not_ready()

    def test_failed_download_is_not_published(self):
        self.set_state(download_fail=True)
        result = self.run_script()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(len(self.calls()), 2)
        self.assertFalse((self.cache / "SHA256SUMS").exists())
        self.assertEqual(list(self.root.glob("host-debs.download.*")), [])
        self.assert_not_ready()

    def test_missing_transitive_dependency_fails_without_network_fallback(self):
        self.set_state(missing_dep=True)
        result = self.run_script()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("--no-download", self.calls()[-1])
        self.assertFalse((self.cache / "SHA256SUMS").exists())
        self.assert_not_ready()

    def test_checksum_mismatch_fails_before_apt(self):
        self.make_cache()
        next(self.cache.glob("*.deb")).write_text("corrupt archive")
        result = self.run_script(hit=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("checksum verification failed", result.stderr)
        self.assertEqual(self.calls(), [])
        self.assert_not_ready()

    def test_manifest_cannot_reference_files_outside_cache(self):
        self.make_cache()
        (self.cache / "SHA256SUMS").write_text("0" * 64 + "  ../outside.deb\n")
        result = self.run_script(hit=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("invalid checksum manifest", result.stderr)
        self.assertEqual(self.calls(), [])
        self.assert_not_ready()

    def test_unlisted_archive_fails_before_apt(self):
        self.make_cache()
        (self.cache / "extra_1.0_amd64.deb").write_text("extra")
        result = self.run_script(hit=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unlisted package", result.stderr)
        self.assertEqual(self.calls(), [])
        self.assert_not_ready()


if __name__ == "__main__":
    unittest.main()
