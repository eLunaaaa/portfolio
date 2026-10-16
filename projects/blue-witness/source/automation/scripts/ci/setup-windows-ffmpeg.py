#!/usr/bin/env python3
"""Install the pinned Windows FFmpeg archive used by server CI."""

import hashlib
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile


VERSION = "9.0.2"
ARCHIVE_NAME = f"ffmpeg-{VERSION}-essentials_build.7z"
URL = f"https://github.com/GyanD/codexffmpeg/releases/download/{VERSION}/{ARCHIVE_NAME}"
SHA256 = "4705843ccaaf54257c16ad90f3e952ece33c17df964ecf7bfdbb0f49c7171077"


def verify_archive(path):
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    if digest.hexdigest() != SHA256:
        raise RuntimeError(f"FFmpeg archive SHA-256 mismatch: {path}")


def get_archive(cache_dir):
    cache_dir.mkdir(parents=True, exist_ok=True)
    archive = cache_dir / ARCHIVE_NAME
    if archive.exists():
        verify_archive(archive)
        print(f"Using verified FFmpeg {VERSION} archive from cache.", flush=True)
        return archive

    curl = shutil.which("curl.exe") or shutil.which("curl")
    if not curl:
        raise RuntimeError("curl is required to download FFmpeg.")
    with tempfile.NamedTemporaryFile(dir=cache_dir, suffix=".part", delete=False) as handle:
        temporary = Path(handle.name)
    try:
        subprocess.run(
            [curl, "--fail", "--location", "--silent", "--show-error",
             "--connect-timeout", "15", "--max-time", "60",
             "--retry", "1", "--retry-max-time", "90",
             "--output", str(temporary), URL],
            check=True, timeout=120,
        )
        verify_archive(temporary)
        temporary.replace(archive)
    finally:
        temporary.unlink(missing_ok=True)
    print(f"Downloaded and verified FFmpeg {VERSION} archive.", flush=True)
    return archive


def find_7zip():
    executable = shutil.which("7z")
    if executable:
        return executable
    fallback = Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "7-Zip" / "7z.exe"
    if fallback.is_file():
        return str(fallback)
    raise RuntimeError("7-Zip is required to extract FFmpeg.")


def setup(cache_dir, install_dir):
    archive = get_archive(cache_dir)
    seven_zip = find_7zip()
    if install_dir.exists():
        shutil.rmtree(install_dir)
    install_dir.mkdir(parents=True)
    subprocess.run(
        [seven_zip, "x", str(archive), f"-o{install_dir}", "-y", "-bso0", "-bsp0"],
        check=True, timeout=30,
    )
    candidates = [path.parent for path in install_dir.rglob("ffmpeg.exe")
                  if (path.parent / "ffprobe.exe").is_file()]
    if len(candidates) != 1:
        raise RuntimeError("FFmpeg archive must contain one ffmpeg.exe / ffprobe.exe pair.")
    bin_dir = candidates[0].resolve()
    for name in ("ffmpeg", "ffprobe"):
        result = subprocess.run(
            [str(bin_dir / f"{name}.exe"), "-version"],
            check=True, timeout=10, capture_output=True, text=True,
        )
        if not re.match(rf"^{name} version {re.escape(VERSION)}(?:[-\s]|$)", result.stdout):
            raise RuntimeError(f"Unexpected {name} version: {result.stdout.splitlines()[:1]}")
        print(result.stdout.splitlines()[0], flush=True)

    if os.environ.get("GITHUB_PATH"):
        with open(os.environ["GITHUB_PATH"], "a", encoding="utf-8") as target:
            target.write(f"{bin_dir}\n")
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as target:
            target.write("cache-ready=true\n")
    return bin_dir


def main():
    try:
        setup(Path(os.environ["FFMPEG_CACHE_DIR"]).resolve(),
              Path(os.environ["FFMPEG_INSTALL_DIR"]).resolve())
    except (KeyError, OSError, RuntimeError, subprocess.SubprocessError) as error:
        print(f"FFmpeg setup failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
