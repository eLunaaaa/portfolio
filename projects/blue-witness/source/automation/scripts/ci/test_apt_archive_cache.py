#!/usr/bin/env python3
"""Real APT regression using local fixtures, isolated state, and no downloads."""

import hashlib
import os
from pathlib import Path
import pwd
import shutil
import subprocess
import tempfile
import unittest


@unittest.skipUnless(shutil.which("apt-get") and shutil.which("dpkg-deb"),
                     "requires apt-get and dpkg-deb")
class AptArchiveCacheTests(unittest.TestCase):
    def test_offline_install_uses_the_directory_containing_cached_archives(self):
        with tempfile.TemporaryDirectory(prefix="apt-cache-test-") as temporary:
            root = Path(temporary)
            archives = root / "downloaded"
            empty_archives = root / "empty-archives"
            lists = root / "lists"
            parts = root / "empty-parts"
            for path in (archives, empty_archives, lists, parts,
                         root / "state", root / "cache", root / "log"):
                path.mkdir()
            (lists / "partial").mkdir()
            status = root / "status"
            status.touch()
            sources = root / "sources.list"
            # The matching remote index reproduces APT's source selection. Every
            # invocation uses --no-download, so this endpoint is never contacted.
            sources.write_text("deb [trusted=yes] http://127.0.0.1:9 ./\n")

            index = []
            debs = []
            fixtures = (("cache-test-dependency", ""),
                        ("cache-test-tool", "Depends: cache-test-dependency (= 1.0)\n"))
            for name, dependency in fixtures:
                package_root = root / name
                (package_root / "DEBIAN").mkdir(parents=True)
                control = (f"Package: {name}\nVersion: 1.0\nArchitecture: all\n"
                           "Maintainer: Test <test@example.invalid>\n"
                           + dependency + "Description: isolated APT cache fixture\n")
                (package_root / "DEBIAN/control").write_text(control)
                deb = archives / f"{name}_1.0_all.deb"
                subprocess.run(["dpkg-deb", "--build", str(package_root), str(deb)],
                               check=True, capture_output=True, text=True, timeout=15)
                debs.append(str(deb))
                content = deb.read_bytes()
                index.append(control + f"Filename: pool/{deb.name}\nSize: {len(content)}\n"
                             + f"SHA256: {hashlib.sha256(content).hexdigest()}\n\n")
            (lists / "127.0.0.1:9_._Packages").write_text("".join(index))

            sandbox_user = pwd.getpwuid(os.geteuid()).pw_name
            options = {
                "Dir::State": root / "state",
                "Dir::State::status": status,
                "Dir::State::lists": lists,
                "Dir::Cache": root / "cache",
                "Dir::Cache::pkgcache": root / "cache/pkgcache.bin",
                "Dir::Cache::srcpkgcache": root / "cache/srcpkgcache.bin",
                "Dir::Log": root / "log",
                "Dir::Etc::sourcelist": sources,
                "Dir::Etc::sourceparts": parts,
                "Dir::Etc::main": "/dev/null",
                "Dir::Etc::parts": parts,
                "Dir::Etc::preferences": "/dev/null",
                "Dir::Etc::preferencesparts": parts,
                "Debug::NoLocking": "1",
                "APT::Sandbox::User": sandbox_user,
            }
            common = ["apt-get"]
            for key, value in options.items():
                common += ["-o", f"{key}={value}"]
            # Download-only additionally ensures dpkg can never install anything.
            common += ["--yes", "--no-install-recommends", "--download-only", "--no-download"]
            env = {**os.environ, "LC_ALL": "C", "APT_CONFIG": "/dev/null"}

            results = []
            for directory in (empty_archives, archives):
                command = common + ["-o", f"Dir::Cache::archives={directory}", "install"] + debs
                results.append(subprocess.run(command, text=True, capture_output=True,
                                              timeout=15, env=env))

            wrong_directory, matching_directory = results
            self.assertEqual(wrong_directory.returncode, 100,
                             wrong_directory.stdout + wrong_directory.stderr)
            self.assertIn("Unable to fetch some archives", wrong_directory.stderr)
            self.assertEqual(matching_directory.returncode, 0,
                             matching_directory.stdout + matching_directory.stderr)
            self.assertEqual(status.read_text(), "")


if __name__ == "__main__":
    unittest.main()
