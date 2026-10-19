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


# 기능: 다운로드한 FFmpeg 압축 파일의 무결성을 검사한다.
# 입력: 압축 파일 Path.
# 출력: 일치 시 None; 고정 SHA-256과 다르면 RuntimeError.
# 구조·이유: 1MB 단위로 읽어 큰 파일을 메모리에 모두 올리지 않고 해시를 비교한다.
def verify_archive(path):
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    if digest.hexdigest() != SHA256:
        raise RuntimeError(f"FFmpeg archive SHA-256 mismatch: {path}")


# 기능: 해시를 검증한 FFmpeg 압축 파일을 준비한다.
# 입력: 캐시 디렉터리 cache_dir.
# 출력: 검증된 압축 파일 Path; 다운로드·검증 실패 시 예외.
# 구조·이유: 캐시도 매번 검증하며 새 파일은 임시 .part에 받아 검증 후 교체하고 실패 잔여물을 정리한다.
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


# 기능: 압축 해제에 사용할 7-Zip 실행 경로를 찾는다.
# 입력: PATH와 ProgramFiles 환경.
# 출력: 실행 파일 경로 문자열; 찾지 못하면 RuntimeError.
# 구조·이유: PATH를 우선 확인하고 표준 설치 경로를 검사해 Windows 실행 환경 차이를 처리한다.
def find_7zip():
    executable = shutil.which("7z")
    if executable:
        return executable
    fallback = Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "7-Zip" / "7z.exe"
    if fallback.is_file():
        return str(fallback)
    raise RuntimeError("7-Zip is required to extract FFmpeg.")


# 기능: Windows CI용 FFmpeg를 설치하고 다음 단계에 경로를 전달한다.
# 입력: 캐시 디렉터리와 압축 해제 설치 디렉터리.
# 출력: 검증된 bin 디렉터리 Path와 GitHub Actions 환경 파일 갱신.
# 구조·이유: 해시 검증 → 압축 해제 → 실행 파일 쌍·고정 버전 확인 후에만 GITHUB_PATH와 준비 완료 출력을 기록한다.
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


# 기능: 환경 변수로 Windows FFmpeg 설치를 시작한다.
# 입력: FFMPEG_CACHE_DIR·FFMPEG_INSTALL_DIR 환경 변수.
# 출력: 성공 0, 설정·파일·명령 실행 실패 1.
# 구조·이유: 경로를 절대 경로로 바꿔 setup에 전달하고 실패 이유는 표준 오류로 출력해 CI가 중단되게 한다.
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
