#!/usr/bin/env python3
"""@file sync_recordings.py
@brief Raspberry Pi의 MP4를 SSH/SCP로 내려받아 무결성을 확인하고 local staging에 동기화한다.

원격에서 완성된 MP4 목록과 SHA를 조회하고 capture plan에 해당하는 파일만 선택한다.
임시 경로로 복사한 뒤 원격·local SHA가 같을 때만 최종 이름으로 교체하며 Raspberry
Pi의 원본은 삭제하지 않는다. recording manifest가 있으면 보존하고, 없으면 사용자가
명시한 metadata로 완성한다. 정식 데이터셋 등록은 register_recordings.py가 담당한다.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from dataset_contracts import (
    DatasetContractError,
    RECORDING_MANIFEST,
    atomic_write_json,
    load_settings,
    resolve_project_path,
    sha256_file,
    validate_recording_manifest,
)


INITIAL_VEHICLES_FILENAME_PATTERN = re.compile(
    r"^session(?P<session>0[1-6])_video(?P<video>0[1-9]|1[0-4])\.mp4$"
)
INITIAL_VEHICLES_EXPECTED_FILENAMES = {
    f"session{session:02d}_video{video:02d}.mp4"
    for session in range(1, 7)
    for video in range(1, 15)
}
PALISADE_FILENAME_PATTERN = re.compile(
    r"^session(?P<session>0[7-9]|10)_video(?P<video>0[1-8])\.mp4$"
)
PALISADE_EXPECTED_FILENAMES = {
    f"session{session:02d}_video{video:02d}.mp4"
    for session in range(7, 11)
    for video in range(1, 9)
}
SSH_CONNECTION_OPTIONS = [
    "-o", "ControlMaster=auto",
    "-o", "ControlPersist=60",
    "-o", "ControlPath=/tmp/vehicle-model-sync-%C",
]


# -----------------------------------------------------------------------------
# 기능: 쉘 문자열 결합 없이 외부 명령을 실행하고 자격 증명을 저장하지 않는다.
#
# 동작 방법:
#   1. 쉘 문자열 결합 없이 외부 명령을 실행하고 자격 증명을 저장하지 않는다
#   2. 입력 계약을 유지한 결과를 만들어 호출한 pipeline 단계에 반환한다.
#
# 사용 이유/연결: Raspberry Pi 촬영과 local 정식 등록 사이의 안전한 전송 단계다.
#
# 입력:
#   command (list[str]) - 이 함수의 command 처리에 사용하는 값.
#
# 반환: str 형식의 처리 결과를 반환하며 의미는 기능 설명과 같다.
# -----------------------------------------------------------------------------
def run_checked(command: list[str]) -> str:
    try:
        result = subprocess.run(command, check=True, text=True, capture_output=True)
    except FileNotFoundError as error:
        raise DatasetContractError(f"필요한 명령이 없습니다: {command[0]}") from error
    except subprocess.CalledProcessError as error:
        detail = (error.stderr or error.stdout or str(error)).strip()
        raise DatasetContractError(f"외부 명령 실패 ({command[0]}): {detail}") from error
    return result.stdout


# -----------------------------------------------------------------------------
# 기능: 임시 파일과 0바이트 파일을 제외한 완성 MP4를 원격에서 찾는다.
#
# 동작 방법:
#   1. 입력 경로 또는 외부 source에서 필요한 정보를 읽는다.
#   2. 읽은 값을 후속 단계가 사용하는 내부 계약 형태로 검증·변환한다.
#
# 사용 이유/연결: Raspberry Pi 촬영과 local 정식 등록 사이의 안전한 전송 단계다.
#
# 입력:
#   target (str) - user@host 형식의 SSH 접속 대상.
#   remote_root (str) - 이 함수의 remote_root 처리에 사용하는 값.
#   recursive (bool) - 이 함수의 recursive 처리에 사용하는 값.
#
# 반환: list[str] 형식의 처리 결과를 반환하며 의미는 기능 설명과 같다.
# -----------------------------------------------------------------------------
def list_remote_videos(target: str, remote_root: str, *, recursive: bool = True) -> list[str]:
    depth_option = "" if recursive else "-maxdepth 1 "
    script = (
        f"find {shlex.quote(remote_root)} {depth_option}-type f -name '*.mp4' -size +0c "
        "! -name '.incoming-*' ! -name '*.tmp' -print"
    )
    return sorted(
        line
        for line in run_checked(["ssh", *SSH_CONNECTION_OPTIONS, target, script]).splitlines()
        if line
    )


def remote_sha256(target: str, remote_path: str) -> str:
    """원격 MP4의 SHA-256을 SSH로 계산해 전송 후 무결성 검증에 사용한다."""
    output = run_checked(
        ["ssh", *SSH_CONNECTION_OPTIONS, target, f"sha256sum -- {shlex.quote(remote_path)}"]
    )
    digest = output.strip().split(maxsplit=1)[0].lower()
    if len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
        raise DatasetContractError(f"원격 SHA-256 출력 오류: {output.strip()}")
    return digest


def remote_modified_at_utc(target: str, remote_path: str) -> str:
    """촬영 시각을 따로 기록하지 않은 batch에서 MP4 수정 시각을 UTC로 읽는다."""
    output = run_checked(
        [
            "ssh",
            *SSH_CONNECTION_OPTIONS,
            target,
            f"date -u -r {shlex.quote(remote_path)} '+%Y-%m-%dT%H:%M:%SZ'",
        ]
    ).strip()
    if not output:
        raise DatasetContractError(f"원격 MP4 수정 시각을 확인할 수 없습니다: {remote_path}")
    return output


def initial_vehicles_capture_metadata(remote_path: str, recorded_at_utc: str) -> dict[str, Any]:
    """합의한 6 session×14 영상 계획에서 ID와 등장 차량을 결정한다."""
    filename = Path(remote_path).name
    match = INITIAL_VEHICLES_FILENAME_PATTERN.fullmatch(filename)
    if match is None:
        raise DatasetContractError(f"initial_vehicles_14 촬영 계획에 맞지 않는 파일명: {filename}")
    session_number = int(match.group("session"))
    video_number = int(match.group("video"))
    if 1 <= video_number <= 6:
        instances = ["ioniq5_silver"]
    elif video_number == 7:
        instances = ["other_car_1"]
    elif video_number == 8:
        instances = ["other_car_2"]
    elif video_number == 9:
        instances = ["other_car_3"]
    elif video_number == 10:
        other_index = ((session_number - 1) % 3) + 1
        instances = ["ioniq5_silver", f"other_car_{other_index}"]
    elif video_number == 11:
        instances = ["other_car_1", "other_car_2", "other_car_3"]
    elif video_number == 12:
        edge_instances = {
            1: "ioniq5_silver",
            2: "other_car_1",
            3: "ioniq5_silver",
            4: "other_car_2",
            5: "ioniq5_silver",
            6: "other_car_3",
        }
        instances = [edge_instances[session_number]]
    else:
        instances = []
    stem = Path(filename).stem
    return {
        "recording_id": f"rec_{stem}",
        "session_id": f"session{session_number:02d}",
        "source_group_id": f"source_{stem}",
        "encoding_profile": "primary_2_5mbps",
        "recorded_at_utc": recorded_at_utc,
        "condition_tags": [],
        "vehicle_instances_present": instances,
        "derived_from": None,
    }


def palisade_capture_metadata(remote_path: str, recorded_at_utc: str) -> dict[str, Any]:
    """4 session×8 팰리세이드 촬영 계획에서 ID와 등장 차량을 결정한다."""
    filename = Path(remote_path).name
    match = PALISADE_FILENAME_PATTERN.fullmatch(filename)
    if match is None:
        raise DatasetContractError(f"palisade_8 촬영 계획에 맞지 않는 파일명: {filename}")
    session_number = int(match.group("session"))
    video_number = int(match.group("video"))
    instances = ["palisade_black"]
    if video_number == 8:
        mixed_vehicle_by_session = {
            7: "ioniq5_silver",
            8: "other_car_1",
            9: "other_car_2",
            10: "other_car_3",
        }
        instances.append(mixed_vehicle_by_session[session_number])
    stem = Path(filename).stem
    return {
        "recording_id": f"rec_{stem}",
        "session_id": f"session{session_number:02d}",
        "source_group_id": f"source_{stem}",
        "encoding_profile": "primary_2_5mbps",
        "recorded_at_utc": recorded_at_utc,
        "condition_tags": [],
        "vehicle_instances_present": instances,
        "derived_from": None,
    }


# -----------------------------------------------------------------------------
# 기능: 원격 폴더에서 선택한 촬영 계획의 파일만 골라 완전성을 검사한다.
#
#   같은 폴더에 이전 session 영상이 남아 있어도 현재 계획과 이름이 다른 파일은
#   건드리지 않는다. 계획 대상 파일은 누락이나 중복이 있으면 전송 전에 중단한다.
#
# 동작 방법:
#   1. 원격 전체 경로를 basename 기준으로 묶어 같은 이름의 중복을 찾는다.
#   2. capture plan의 expected filename만 선택하고 이전 촬영 파일은 무시한다.
#   3. 예상 파일이 하나라도 없거나 같은 이름이 두 경로에 있으면 전송 전에 중단한다.
#   4. 검증을 통과하면 계획 순서대로 정렬된 원격 경로 목록을 반환한다.
#
# 사용 이유/연결: Raspberry Pi 촬영과 local 정식 등록 사이의 안전한 전송 단계다.
#
# 입력:
#   remote_paths (list[str]) - 이 함수의 remote_paths 처리에 사용하는 값.
#   expected_filenames (set[str]) - 이 함수의 expected_filenames 처리에 사용하는 값.
#   plan_name (str) - 이 함수의 plan_name 처리에 사용하는 값.
#
# 반환: list[str] 형식의 처리 결과를 반환하며 의미는 기능 설명과 같다.
# -----------------------------------------------------------------------------
def select_capture_plan_files(
    remote_paths: list[str], expected_filenames: set[str], plan_name: str
) -> list[str]:
    selected_paths = [path for path in remote_paths if Path(path).name in expected_filenames]
    filenames = [Path(path).name for path in selected_paths]
    filename_set = set(filenames)
    missing = sorted(expected_filenames - filename_set)
    duplicates = sorted(name for name in filename_set if filenames.count(name) > 1)
    if missing or duplicates:
        raise DatasetContractError(
            f"{plan_name} 파일 구성이 다릅니다. "
            f"누락={missing[:5]}, 중복={duplicates[:5]}"
        )
    return sorted(selected_paths)


def download(target: str, remote_path: str, local_path: Path) -> None:
    """SCP로 원격 파일을 임시 local 경로에 복사하며 원격 원본은 보존한다."""
    run_checked(
        [
            "scp",
            *SSH_CONNECTION_OPTIONS,
            "-p",
            "--",
            f"{target}:{shlex.quote(remote_path)}",
            str(local_path),
        ]
    )


# -----------------------------------------------------------------------------
# 기능: 영상 옆 recording_manifest.json이 있으면 다운로드하고, 없으면 None을 반환한다.
#
# 동작 방법:
#   1. 저장할 내용과 대상 경로를 준비한다.
#   2. 중간 실패가 완성 파일처럼 보이지 않도록 안전하게 기록하고 오류를 호출자에게 전달한다.
#
# 사용 이유/연결: Raspberry Pi 촬영과 local 정식 등록 사이의 안전한 전송 단계다.
#
# 입력:
#   target (str) - user@host 형식의 SSH 접속 대상.
#   video_path (str) - 원본 또는 평가 대상 MP4 경로.
#   temporary_dir (Path) - 이 함수의 temporary_dir 처리에 사용하는 값.
#
# 반환: dict[str, Any] | None 형식의 처리 결과를 반환하며 의미는 기능 설명과 같다.
# -----------------------------------------------------------------------------
def download_manifest(target: str, video_path: str, temporary_dir: Path) -> dict[str, Any] | None:
    candidate = f"{Path(video_path).parent}/{RECORDING_MANIFEST}"
    exists = run_checked(
        [
            "ssh",
            *SSH_CONNECTION_OPTIONS,
            target,
            f"test -f {shlex.quote(candidate)} && printf yes || true",
        ]
    )
    if exists.strip() != "yes":
        return None
    destination = temporary_dir / RECORDING_MANIFEST
    download(target, candidate, destination)
    try:
        value = json.loads(destination.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise DatasetContractError(f"원격 manifest JSON 오류: {candidate}: {error}") from error
    if not isinstance(value, dict):
        raise DatasetContractError(f"원격 manifest는 객체여야 합니다: {candidate}")
    return value


def _explicit_or_manifest(
    manifest: dict[str, Any], key: str, explicit: Any, prompt: str
) -> Any:
    """명시한 CLI 값과 기존 manifest 값 중 사용할 metadata를 선택한다."""
    value = manifest.get(key) or explicit
    if value in {None, ""} and sys.stdin.isatty():
        value = input(prompt).strip()
    if value in {None, ""}:
        raise DatasetContractError(f"{key}이 필요합니다. --{key.replace('_', '-')}로 전달하세요.")
    return value


# -----------------------------------------------------------------------------
# 기능: 원격 metadata를 보존하면서 최신 녹화 계약의 빠진 필드를 명시 입력으로 채운다.
#
# 동작 방법:
#   1. 원격 metadata를 보존하면서 최신 녹화 계약의 빠진 필드를 명시 입력으로 채운다
#   2. 입력 계약을 유지한 결과를 만들어 호출한 pipeline 단계에 반환한다.
#
# 사용 이유/연결: Raspberry Pi 촬영과 local 정식 등록 사이의 안전한 전송 단계다.
#
# 입력:
#   source (dict[str, Any] | None) - 이 함수의 source 처리에 사용하는 값.
#   arguments (argparse.Namespace) - CLI 또는 외부 process에 전달할 인자 값.
#   remote_path (str) - Raspberry Pi에 존재하는 원격 파일 경로.
#   digest (str) - 이 함수의 digest 처리에 사용하는 값.
#
# 반환: dict[str, Any] 형식의 처리 결과를 반환하며 의미는 기능 설명과 같다.
# -----------------------------------------------------------------------------
def complete_manifest(
    source: dict[str, Any] | None,
    arguments: argparse.Namespace,
    remote_path: str,
    digest: str,
) -> dict[str, Any]:
    result = dict(source or {})
    result["recording_id"] = _explicit_or_manifest(result, "recording_id", arguments.recording_id, "recording_id: ")
    result["session_id"] = _explicit_or_manifest(result, "session_id", arguments.session_id, "session_id: ")
    result["source_group_id"] = _explicit_or_manifest(
        result, "source_group_id", arguments.source_group_id, "source_group_id: "
    )
    result["encoding_profile"] = _explicit_or_manifest(
        result, "encoding_profile", arguments.encoding_profile, "encoding_profile: "
    )
    result["recorded_at_utc"] = _explicit_or_manifest(
        result, "recorded_at_utc", arguments.recorded_at_utc, "촬영 시각(UTC ISO-8601): "
    )
    if "condition_tags" not in result:
        result["condition_tags"] = [value for value in arguments.condition_tags.split(",") if value]
    if "vehicle_instances_present" not in result:
        result["vehicle_instances_present"] = [
            value for value in arguments.vehicle_instances_present.split(",") if value
        ]
    result.setdefault("derived_from", arguments.derived_from)
    result.update(
        {
            "schema_version": 2,
            "video_file": "recording.mp4",
            "source_video_sha256": digest,
            "remote_source": remote_path,
        }
    )
    return result


def existing_hashes(recordings_dir: Path) -> set[str]:
    """이미 동기화된 recording의 SHA를 모아 중복 다운로드를 방지한다."""
    hashes: set[str] = set()
    for path in recordings_dir.rglob(RECORDING_MANIFEST):
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            digest = value.get("source_video_sha256")
            if digest:
                hashes.add(digest)
        except (OSError, json.JSONDecodeError):
            continue
    return hashes


# -----------------------------------------------------------------------------
# 기능: 원격 원본은 건드리지 않고 SHA 검증된 신규 녹화만 원자적으로 확정한다.
#
# 동작 방법:
#   1. SSH로 원격 MP4 목록을 조회하고 선택한 capture plan의 예상 파일만 고른다.
#   2. local recording SHA와 원격 SHA를 비교해 이미 받은 영상은 건너뛴다.
#   3. 신규 영상과 선택적 manifest를 임시 디렉터리에 SCP로 복사한다.
#   4. 복사본 SHA를 원격 값과 비교하고 config의 영상 profile을 ffprobe로 검증한다.
#   5. 기존 manifest 또는 명시 CLI metadata로 완성된 recording manifest를 만든다.
#   6. 검증 완료 파일만 incoming 디렉터리에 원자적으로 이동하고 임시 자료를 정리한다.
#
# 사용 이유/연결: Raspberry Pi 촬영과 local 정식 등록 사이의 안전한 전송 단계다.
#
# 입력:
#   arguments (argparse.Namespace) - CLI 또는 외부 process에 전달할 인자 값.
#
# 반환: 다운로드·중복 건너뜀·원격 전체 파일 수를 담은 count dictionary를 반환한다.
# -----------------------------------------------------------------------------
def sync(arguments: argparse.Namespace) -> dict[str, int]:
    config_path = arguments.config.resolve()
    settings = load_settings(config_path)
    recordings_dir = resolve_project_path(config_path, settings["paths"]["recordings_dir"])
    recordings_dir.mkdir(parents=True, exist_ok=True)
    hashes = existing_hashes(recordings_dir)
    remote_paths = list_remote_videos(
        arguments.target,
        arguments.remote_root,
        recursive=arguments.capture_plan is None,
    )
    if arguments.capture_plan is not None:
        required_instances = {
            "ioniq5_silver",
            "palisade_black",
            "other_car_1",
            "other_car_2",
            "other_car_3",
        }
        configured_instances = set().union(
            *(set(item["vehicle_instances"]) for item in settings["classes"])
        )
        if not required_instances.issubset(configured_instances):
            raise DatasetContractError(
                "batch 촬영 계획은 config.yaml의 최종 3-class 차량 계약이 필요합니다."
            )
        if arguments.capture_plan == "initial_vehicles_14":
            remote_paths = select_capture_plan_files(
                remote_paths, INITIAL_VEHICLES_EXPECTED_FILENAMES, arguments.capture_plan
            )
        elif arguments.capture_plan == "palisade_8":
            remote_paths = select_capture_plan_files(
                remote_paths, PALISADE_EXPECTED_FILENAMES, arguments.capture_plan
            )
    counts = {"found": len(remote_paths), "saved": 0, "duplicate": 0, "errors": 0}
    for remote_path in remote_paths:
        try:
            digest = remote_sha256(arguments.target, remote_path)
            if digest in hashes:
                counts["duplicate"] += 1
                continue
            with tempfile.TemporaryDirectory(prefix=".recording-sync-", dir=recordings_dir) as name:
                temporary_dir = Path(name)
                temporary_video = temporary_dir / ".incoming-recording.mp4"
                if arguments.capture_plan == "initial_vehicles_14":
                    remote_manifest = initial_vehicles_capture_metadata(
                        remote_path, remote_modified_at_utc(arguments.target, remote_path)
                    )
                elif arguments.capture_plan == "palisade_8":
                    remote_manifest = palisade_capture_metadata(
                        remote_path, remote_modified_at_utc(arguments.target, remote_path)
                    )
                else:
                    remote_manifest = download_manifest(arguments.target, remote_path, temporary_dir)
                download(arguments.target, remote_path, temporary_video)
                if sha256_file(temporary_video) != digest:
                    raise DatasetContractError(f"전송 전후 SHA가 다릅니다: {remote_path}")
                manifest = complete_manifest(remote_manifest, arguments, remote_path, digest)
                validate_recording_manifest(manifest, settings, temporary_dir / RECORDING_MANIFEST)
                destination = recordings_dir / manifest["session_id"] / manifest["recording_id"]
                if destination.exists():
                    raise DatasetContractError(f"녹화 경로가 이미 있습니다: {destination}")
                staged = temporary_dir / manifest["recording_id"]
                staged.mkdir()
                temporary_video.replace(staged / "recording.mp4")
                atomic_write_json(staged / RECORDING_MANIFEST, manifest)
                destination.parent.mkdir(parents=True, exist_ok=True)
                os.replace(staged, destination)
                hashes.add(digest)
                counts["saved"] += 1
                print(f"저장: {destination}")
        except DatasetContractError as error:
            counts["errors"] += 1
            print(f"ERROR: {remote_path}: {error}", file=sys.stderr)
    return counts


def parse_args() -> argparse.Namespace:
    """SSH 대상, 원격 경로, 촬영 계획과 metadata 인자를 CLI에서 읽는다."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("config.yaml"))
    parser.add_argument("--target", required=True, help="user@RaspberryPi_IP")
    parser.add_argument("--remote-root", required=True)
    parser.add_argument("--recording-id")
    parser.add_argument("--session-id")
    parser.add_argument("--source-group-id")
    parser.add_argument("--encoding-profile")
    parser.add_argument("--recorded-at-utc")
    parser.add_argument("--derived-from", default=None)
    parser.add_argument("--condition-tags", default="")
    parser.add_argument("--vehicle-instances-present", default="")
    parser.add_argument(
        "--capture-plan",
        choices=("initial_vehicles_14", "palisade_8"),
        help="정해진 파일명과 촬영표로 여러 MP4의 manifest를 자동 생성",
    )
    return parser.parse_args()


def main() -> int:
    """원격 녹화 동기화를 실행하고 누락·무결성 오류를 CLI 실패로 보고한다."""
    try:
        counts = sync(parse_args())
    except (DatasetContractError, KeyboardInterrupt) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2
    print(
        f"발견 {counts['found']} / 저장 {counts['saved']} / "
        f"중복 {counts['duplicate']} / 오류 {counts['errors']}"
    )
    return 1 if counts["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
