#!/usr/bin/env python3
"""Raspberry Pi의 완성 MP4와 manifest를 SSH로 읽어 로컬 녹화 저장소에 추가한다."""

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


def run_checked(command: list[str]) -> str:
    """쉘 문자열 결합 없이 외부 명령을 실행하고 자격 증명을 저장하지 않는다."""
    try:
        result = subprocess.run(command, check=True, text=True, capture_output=True)
    except FileNotFoundError as error:
        raise DatasetContractError(f"필요한 명령이 없습니다: {command[0]}") from error
    except subprocess.CalledProcessError as error:
        detail = (error.stderr or error.stdout or str(error)).strip()
        raise DatasetContractError(f"외부 명령 실패 ({command[0]}): {detail}") from error
    return result.stdout


def list_remote_videos(target: str, remote_root: str, *, recursive: bool = True) -> list[str]:
    """임시 파일과 0바이트 파일을 제외한 완성 MP4를 원격에서 찾는다."""
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
    """합의한 6 session×14 영상 계획에서 ID와 등장 차량을 결정한다.

    condition tag는 기억나지 않는 정보를 추측하지 않도록 비워 둔다. 촬영 시각도
    실제 별도 기록이 없으므로 원격 MP4의 수정 시각을 추적용 대체값으로 사용한다.
    """
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
    """4 session×8 팰리세이드 촬영 계획에서 ID와 등장 차량을 결정한다.

    1~7번은 팰리세이드 단독이며 8번은 session마다 지정한 기존 차량과 함께
    등장한다. condition tag는 촬영 당시 기록이 없으므로 임의로 만들지 않는다.
    """
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


def select_capture_plan_files(
    remote_paths: list[str], expected_filenames: set[str], plan_name: str
) -> list[str]:
    """원격 폴더에서 선택한 촬영 계획의 파일만 골라 완전성을 검사한다.

    같은 폴더에 이전 session 영상이 남아 있어도 현재 계획과 이름이 다른 파일은
    건드리지 않는다. 계획 대상 파일은 누락이나 중복이 있으면 전송 전에 중단한다.
    """
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


def download_manifest(target: str, video_path: str, temporary_dir: Path) -> dict[str, Any] | None:
    """영상 옆 recording_manifest.json이 있으면 다운로드하고, 없으면 None을 반환한다."""
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
    value = manifest.get(key) or explicit
    if value in {None, ""} and sys.stdin.isatty():
        value = input(prompt).strip()
    if value in {None, ""}:
        raise DatasetContractError(f"{key}이 필요합니다. --{key.replace('_', '-')}로 전달하세요.")
    return value


def complete_manifest(
    source: dict[str, Any] | None,
    arguments: argparse.Namespace,
    remote_path: str,
    digest: str,
) -> dict[str, Any]:
    """원격 metadata를 보존하면서 최신 녹화 계약의 빠진 필드를 명시 입력으로 채운다."""
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


def sync(arguments: argparse.Namespace) -> dict[str, int]:
    """원격 원본은 건드리지 않고 SHA 검증된 신규 녹화만 원자적으로 확정한다."""
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
