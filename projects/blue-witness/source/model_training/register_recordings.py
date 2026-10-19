#!/usr/bin/env python3
"""MP4 폴더 또는 촬영 계획을 등록하고 작업자별 박스 결과를 합친다.

영상 내용으로 식별자를 만들어 중복 등록을 건너뛰며, 촬영 정보와 원본을 함께
보존한다. --review로 모델 없이 프레임 추출과 검수를 이어서 실행할 수 있다.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from dataset_contracts import (
    DatasetContractError,
    RECORDING_MANIFEST,
    atomic_write_json,
    atomic_write_jsonl,
    class_maps,
    load_settings,
    probe_video,
    read_json,
    read_jsonl,
    resolve_project_path,
    sha256_file,
    validate_recording_manifest,
    validate_video_profile,
)

DATASET_USAGES = {"development", "final_test"}


def _string_list(value: Any, context: str) -> list[str]:
    """YAML 항목이 비어 있지 않은 문자열 배열인지 검증해 반환한다."""
    if not isinstance(value, list) or any(not isinstance(item, str) or not item for item in value):
        raise DatasetContractError(f"{context}는 문자열 배열이어야 합니다.")
    return value


# -----------------------------------------------------------------------------
# 기능: 촬영 계획 하나를 읽고 등록 전에 구조와 중복 항목을 검증한다.
#
# 동작 방법:
#   1. 입력 경로 또는 외부 source에서 필요한 정보를 읽는다.
#   2. 읽은 값을 후속 단계가 사용하는 내부 계약 형태로 검증·변환한다.
#
# 사용 이유/연결: 동기화된 촬영 파일을 정식 입력으로 만들고 prepare_dataset.py에 전달한다.
#
# 입력:
#   plans_path (Path) - 이 함수의 plans_path 처리에 사용하는 값.
#   plan_name (str) - 이 함수의 plan_name 처리에 사용하는 값.
#
# 반환: dict[str, Any] 형식의 처리 결과를 반환하며 의미는 기능 설명과 같다.
# -----------------------------------------------------------------------------
def load_capture_plan(plans_path: Path, plan_name: str) -> dict[str, Any]:
    with plans_path.open("r", encoding="utf-8") as handle:
        document = yaml.safe_load(handle)
    if not isinstance(document, dict) or document.get("schema_version") != 1:
        raise DatasetContractError("capture_plans.yaml의 schema_version은 1이어야 합니다.")
    plans = document.get("plans")
    if not isinstance(plans, dict) or plan_name not in plans:
        available = sorted(plans) if isinstance(plans, dict) else []
        raise DatasetContractError(f"촬영 계획을 찾지 못했습니다: {plan_name}, 사용 가능={available}")
    plan = plans[plan_name]
    if not isinstance(plan, dict):
        raise DatasetContractError(f"plans.{plan_name}은 객체여야 합니다.")
    for key in ("source_directory", "encoding_profile", "dataset_usage"):
        if not isinstance(plan.get(key), str) or not plan[key]:
            raise DatasetContractError(f"plans.{plan_name}.{key} 문자열이 필요합니다.")
    if plan["dataset_usage"] not in DATASET_USAGES:
        raise DatasetContractError(
            f"dataset_usage는 development 또는 final_test여야 합니다: {plan['dataset_usage']}"
        )
    session_defaults = plan.get("session_defaults", {})
    if not isinstance(session_defaults, dict):
        raise DatasetContractError(f"plans.{plan_name}.session_defaults는 객체여야 합니다.")
    for session_id, tags in session_defaults.items():
        if not isinstance(session_id, str) or not session_id:
            raise DatasetContractError("session_defaults의 session_id가 올바르지 않습니다.")
        _string_list(tags, f"session_defaults.{session_id}")
    recordings = plan.get("recordings")
    if not isinstance(recordings, list) or not recordings:
        raise DatasetContractError(f"plans.{plan_name}.recordings가 비어 있습니다.")
    filenames: list[str] = []
    for index, item in enumerate(recordings):
        context = f"plans.{plan_name}.recordings[{index}]"
        if not isinstance(item, dict):
            raise DatasetContractError(f"{context}는 객체여야 합니다.")
        filename = item.get("file")
        session_id = item.get("session_id")
        if not isinstance(filename, str) or Path(filename).name != filename or not filename.endswith(".mp4"):
            raise DatasetContractError(f"{context}.file은 소문자 .mp4 파일명이어야 합니다.")
        if not isinstance(session_id, str) or not session_id:
            raise DatasetContractError(f"{context}.session_id 문자열이 필요합니다.")
        _string_list(item.get("condition_tags", []), f"{context}.condition_tags")
        _string_list(item.get("vehicle_instances", []), f"{context}.vehicle_instances")
        filenames.append(filename)
    duplicates = sorted(name for name in set(filenames) if filenames.count(name) > 1)
    if duplicates:
        raise DatasetContractError(f"촬영 계획에 중복 파일명이 있습니다: {duplicates}")
    return plan


# -----------------------------------------------------------------------------
# 기능: YAML 메타데이터와 파일 정보로 검증 가능한 recording manifest를 만든다.
#
# 동작 방법:
#   1. YAML 메타데이터와 파일 정보로 검증 가능한 recording manifest를 만든다
#   2. 입력 계약을 유지한 결과를 만들어 호출한 pipeline 단계에 반환한다.
#
# 사용 이유/연결: 동기화된 촬영 파일을 정식 입력으로 만들고 prepare_dataset.py에 전달한다.
#
# 입력:
#   source (Path) - 이 함수의 source 처리에 사용하는 값.
#   plan (dict[str, Any]) - 이 함수의 plan 처리에 사용하는 값.
#   item (dict[str, Any]) - 이 함수의 item 처리에 사용하는 값.
#
# 반환: dict[str, Any] 형식의 처리 결과를 반환하며 의미는 기능 설명과 같다.
# -----------------------------------------------------------------------------
def build_manifest(source: Path, plan: dict[str, Any], item: dict[str, Any]) -> dict[str, Any]:
    stem = source.stem
    session_id = item["session_id"]
    common_tags = plan.get("session_defaults", {}).get(session_id, [])
    condition_tags = list(dict.fromkeys(common_tags + item.get("condition_tags", [])))
    recorded_at = datetime.fromtimestamp(source.stat().st_mtime, timezone.utc)
    return {
        "schema_version": 2,
        "recording_id": item.get("recording_id", f"rec_{stem}"),
        "session_id": session_id,
        "source_group_id": item.get("source_group_id", f"source_{stem}"),
        "encoding_profile": plan["encoding_profile"],
        "dataset_usage": plan["dataset_usage"],
        "recorded_at_utc": item.get(
            "recorded_at_utc", recorded_at.isoformat().replace("+00:00", "Z")
        ),
        "condition_tags": condition_tags,
        "vehicle_instances_present": item.get("vehicle_instances", []),
        "derived_from": item.get("derived_from"),
        "video_file": "recording.mp4",
        "source_video_sha256": sha256_file(source),
    }


def _registered_hashes(recordings_dir: Path) -> set[str]:
    """기존 recording manifest의 영상 SHA를 모아 중복 등록을 방지한다."""
    hashes: set[str] = set()
    for manifest_path in recordings_dir.rglob(RECORDING_MANIFEST):
        manifest = read_json(manifest_path)
        digest = manifest.get("source_video_sha256")
        if isinstance(digest, str):
            hashes.add(digest)
    return hashes


def folder_capture_plan(source_dir: Path, session_id: str | None = None) -> dict[str, Any]:
    """폴더의 MP4를 파일 목록 작성 없이 등록한다. 차종은 박스 창에서 지정한다."""
    if not source_dir.is_dir():
        raise DatasetContractError(f"원본 영상 폴더가 없습니다: {source_dir}")
    videos = sorted(
        (p for p in source_dir.iterdir() if p.is_file() and p.suffix.lower() == ".mp4"),
        key=lambda p: p.name.casefold(),
    )
    if not videos:
        raise DatasetContractError(f"MP4 영상이 없습니다: {source_dir}")
    recordings = []
    for video in videos:
        digest = sha256_file(video)
        recordings.append({
            "file": video.name,
            "recording_id": "rec_" + digest[:24],
            "session_id": session_id or "clip_" + digest[:24],
            "source_group_id": "source_" + digest[:24],
            "condition_tags": [],
            "vehicle_instances": [],
        })
    return {
        "source_directory": str(source_dir),
        "encoding_profile": "primary_2_5mbps",
        "dataset_usage": "development",
        "recordings": recordings,
    }


# -----------------------------------------------------------------------------
# 기능: 계획의 파일 구성과 영상 계약을 모두 확인한 뒤 원본을 보존하여 등록한다.
#
# 동작 방법:
#   1. 선택한 capture plan과 대상 config의 class·instance 계약을 확인한다.
#   2. source 디렉터리의 MP4 이름을 plan의 expected_files와 정확히 대조한다.
#   3. 각 영상의 codec·해상도·FPS·bitrate와 SHA를 검사하고 manifest를 구성한다.
#   4. 이미 등록된 SHA는 중복 등록하지 않고, 신규 영상은 staging 디렉터리에 복사한다.
#   5. 영상 복사 후 SHA를 다시 확인하고 manifest와 함께 최종 recordings 위치로 옮긴다.
#   6. dry-run이면 같은 검증을 수행하되 실제 파일은 만들지 않는다.
#
# 사용 이유/연결: 동기화된 촬영 파일을 정식 입력으로 만들고 prepare_dataset.py에 전달한다.
#
# 입력:
#   config_path (Path) - 경로·class·threshold 등 실행 계약이 기록된 YAML config 경로.
#   plans_path (Path) - 재사용 가능한 촬영 metadata와 예상 파일 목록이 있는 YAML 경로.
#   plan_name (str) - capture_plans 안에서 적용할 계획의 이름.
#   source_override (Path | None) - plan 기본값 대신 사용할 촬영 MP4 디렉터리.
#   dry_run (bool) - true면 검증 결과만 계산하고 recordings 디렉터리는 변경하지 않는다.
#
# 반환: 등록·중복 건너뜀·검사한 전체 파일 수가 포함된 count dictionary를 반환한다.
# -----------------------------------------------------------------------------
def register_recordings(
    config_path: Path,
    plans_path: Path,
    plan_name: str | None,
    source_override: Path | None = None,
    dry_run: bool = False,
    session_id: str | None = None,
) -> dict[str, int]:
    settings = load_settings(config_path)
    if plan_name:
        plan = load_capture_plan(plans_path, plan_name)
    else:
        if source_override is None:
            raise DatasetContractError("영상 폴더를 --source-dir로 지정하세요.")
        plan = folder_capture_plan(source_override, session_id)
    source_dir = source_override or resolve_project_path(plans_path, plan["source_directory"])
    if not source_dir.is_dir():
        raise DatasetContractError(f"원본 영상 폴더가 없습니다: {source_dir}")
    expected = {item["file"] for item in plan["recordings"]}
    actual = {path.name for path in source_dir.iterdir() if path.is_file() and path.suffix.lower() == ".mp4"}
    missing = sorted(expected - actual)
    unexpected = sorted(actual - expected)
    if missing or unexpected:
        raise DatasetContractError(f"촬영 파일 구성이 다릅니다. 누락={missing}, 예상 밖={unexpected}")

    recordings_dir = resolve_project_path(config_path, settings["paths"]["recordings_dir"])
    recordings_dir.mkdir(parents=True, exist_ok=True)
    known_hashes = _registered_hashes(recordings_dir)
    counts = {"found": len(expected), "validated": 0, "saved": 0, "duplicate": 0}
    for item in plan["recordings"]:
        source = source_dir / item["file"]
        manifest = build_manifest(source, plan, item)
        validate_recording_manifest(manifest, settings, plans_path)
        metadata = probe_video(source)
        validate_video_profile(metadata, manifest["encoding_profile"], settings, source)
        counts["validated"] += 1
        digest = manifest["source_video_sha256"]
        if digest in known_hashes:
            counts["duplicate"] += 1
            continue
        destination = recordings_dir / manifest["session_id"] / manifest["recording_id"]
        if destination.exists():
            raise DatasetContractError(f"녹화 경로가 이미 있습니다: {destination}")
        if dry_run:
            continue
        with tempfile.TemporaryDirectory(prefix=".recording-register-", dir=recordings_dir) as name:
            staged = Path(name) / manifest["recording_id"]
            staged.mkdir()
            copied_video = staged / "recording.mp4"
            shutil.copy2(source, copied_video)
            if sha256_file(copied_video) != digest:
                raise DatasetContractError(f"복사 전후 SHA가 다릅니다: {source}")
            atomic_write_json(staged / RECORDING_MANIFEST, manifest)
            destination.parent.mkdir(parents=True, exist_ok=True)
            os.replace(staged, destination)
        known_hashes.add(digest)
        counts["saved"] += 1
        print(f"등록: {destination}")
    return counts


def merge_work(config_path: Path, source_dirs: list[Path]) -> dict[str, int]:
    """각 작업자의 사진과 박스를 병합한다. 충돌은 파일을 바꾸기 전에 알린다."""
    settings = load_settings(config_path)
    target = resolve_project_path(config_path, settings["paths"]["work_dir"])
    frames: dict[str, dict[str, Any]] = {}
    annotations: dict[str, dict[str, Any]] = {}
    recordings: dict[str, str] = {}
    recording_by_sha: dict[str, str] = {}
    frame_by_sha: dict[str, str] = {}
    images: dict[Path, tuple[Path, str]] = {}
    done = {"approved", "negative", "rejected"}
    counts = {"added": 0, "updated": 0, "duplicate": 0}
    classes, instances = class_maps(settings)

    # 기능: JSONL 레코드를 고유 식별자별로 색인한다.
    # 입력: JSONL 경로 path와 키 이름 key.
    # 출력: 식별자 → 레코드 dict; 중복 식별자는 DatasetContractError.
    # 구조·이유: 원본 행 수와 색인 크기를 비교해 dict 생성 중 중복 행이 덮이는 문제를 발견한다.
    def indexed(path: Path, key: str) -> dict[str, dict[str, Any]]:
        records = read_jsonl(path)
        result = {record[key]: record for record in records}
        if len(result) != len(records):
            raise DatasetContractError(f"중복 식별자가 있습니다: {path}")
        return result

    # 기능: 등록 이미지 경로가 frames 하위에 있는지 검사한다.
    # 입력: 작업 디렉터리 directory와 상대 경로 value.
    # 출력: 해석한 절대 Path; frames 밖이면 DatasetContractError.
    # 구조·이유: resolve 후 경로 포함 관계를 확인해 외부 파일 참조를 차단한다.
    def image_path(directory: Path, value: str) -> Path:
        path = (directory / value).resolve()
        if not path.is_relative_to(directory.resolve() / "frames"):
            raise DatasetContractError(f"사진 경로가 frames 폴더 밖입니다: {value}")
        return path

    # 기능: 두 주석 레코드의 검수 상태와 객체 목록을 비교한다.
    # 입력: 주석 dict a와 b.
    # 출력: status와 objects가 모두 같으면 True.
    # 구조·이유: 촬영 기록 병합 시 라벨의 실질적 변경 여부를 판별한다.
    def same_labels(a: dict[str, Any], b: dict[str, Any]) -> bool:
        return a["status"] == b["status"] and a["objects"] == b["objects"]

    for source in [target, *source_dirs]:
        existing = source == target
        if not existing and not all((source / name).is_file() for name in (
            "frame_manifest.jsonl", "annotations.jsonl", "recording_index.jsonl",
        )):
            raise DatasetContractError(f"받은 work 폴더를 지정하세요: {source}")
        batch_frames = indexed(source / "frame_manifest.jsonl", "frame_id")
        batch_annotations = indexed(source / "annotations.jsonl", "frame_id")
        if set(batch_frames) != set(batch_annotations):
            raise DatasetContractError(f"사진 목록과 박스 목록이 일치하지 않습니다: {source}")
        batch_recordings = indexed(source / "recording_index.jsonl", "recording_id")
        for frame in batch_frames.values():
            record = {"recording_id": frame["recording_id"], "source_video_sha256": frame["source_video_sha256"]}
            previous = batch_recordings.setdefault(record["recording_id"], record)
            if previous["source_video_sha256"] != record["source_video_sha256"]:
                raise DatasetContractError(f"원본 영상 정보 충돌: {record['recording_id']}")
        for record in batch_recordings.values():
            rid, digest = record["recording_id"], record["source_video_sha256"]
            if recordings.get(rid, digest) != digest or recording_by_sha.get(digest, rid) != rid:
                raise DatasetContractError(f"원본 영상 식별자 충돌: {rid}")
            recordings[rid] = digest
            recording_by_sha[digest] = rid

        for fid, frame in batch_frames.items():
            annotation = batch_annotations[fid]
            if annotation["status"] not in done | {"draft", "manual_review_required"}:
                raise DatasetContractError(f"박스 상태가 올바르지 않습니다: {fid}")
            if annotation["status"] == "negative" and annotation["objects"]:
                raise DatasetContractError(f"차량 없음 상태에 박스가 남아 있습니다: {fid}")
            if annotation["status"] == "approved":
                for obj in annotation["objects"]:
                    if obj.get("class_name") not in classes or instances.get(obj.get("vehicle_instance")) != obj.get("class_name"):
                        raise DatasetContractError(f"차종 설정이 일치하지 않습니다: {fid}")
            original = image_path(source, frame["image_path"])
            digest = frame["image_sha256"]
            if not original.is_file() or sha256_file(original) != digest:
                raise DatasetContractError(f"사진 누락 또는 내용 불일치: {original}")
            old_id = fid if fid in frames else frame_by_sha.get(digest)
            if old_id is not None:
                old_frame, old_annotation = frames[old_id], annotations[old_id]
                if old_id == fid:
                    ignored = {"source_video", "annotation_status"}
                    previous_metadata = {"vehicle_instances_present": [], **{k: v for k, v in old_frame.items() if k not in ignored}}
                    incoming_metadata = {"vehicle_instances_present": [], **{k: v for k, v in frame.items() if k not in ignored}}
                    if previous_metadata != incoming_metadata:
                        raise DatasetContractError(f"사진 정보 충돌: {fid}")
                if not same_labels(old_annotation, annotation):
                    if old_annotation["status"] not in done and annotation["status"] in done:
                        annotations[old_id] = dict(annotation, frame_id=old_id)
                        old_frame["annotation_status"] = annotation["status"]
                        counts["updated"] += int(not existing)
                    elif old_annotation["status"] not in done or annotation["status"] in done:
                        raise DatasetContractError(f"같은 사진의 박스가 다릅니다: {fid}. 기존 작업은 유지됩니다.")
                counts["duplicate"] += int(not existing)
                continue
            destination = image_path(target, frame["image_path"])
            if destination.exists() and sha256_file(destination) != digest:
                raise DatasetContractError(f"사진 파일 이름 충돌: {destination}")
            if destination in images and images[destination][1] != digest:
                raise DatasetContractError(f"사진 파일 이름 충돌: {destination}")
            frames[fid] = dict(frame, annotation_status=annotation["status"])
            annotations[fid] = annotation
            frame_by_sha[digest] = fid
            images[destination] = (original, digest)
            counts["added"] += int(not existing)

    # 전체 입력을 확인한 뒤 복사하고 기존 사진과 박스를 보존한다.
    target.mkdir(parents=True, exist_ok=True)
    for destination, (original, digest) in images.items():
        if destination.exists():
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=destination.parent, delete=False) as temp:
            staged = Path(temp.name)
        try:
            shutil.copyfile(original, staged)
            if sha256_file(staged) != digest:
                raise DatasetContractError(f"사진 복사 오류: {original}")
            os.replace(staged, destination)
        finally:
            staged.unlink(missing_ok=True)
    atomic_write_jsonl(target / "frame_manifest.jsonl", [frames[k] for k in sorted(frames)])
    atomic_write_jsonl(target / "annotations.jsonl", [annotations[k] for k in sorted(annotations)])
    atomic_write_jsonl(target / "recording_index.jsonl", [
        {"recording_id": k, "source_video_sha256": recordings[k]} for k in sorted(recordings)
    ])
    return counts


def parse_args() -> argparse.Namespace:
    """촬영 계획, 원본 디렉터리와 dry-run 여부를 CLI에서 읽는다."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("config.yaml"))
    parser.add_argument("--plans", type=Path, default=Path("capture_plans.yaml"))
    parser.add_argument("--plan", help="기존 YAML 촬영 계획 이름; 생략하면 폴더 자동 등록")
    parser.add_argument("--source-dir", type=Path, help="촬영한 MP4가 들어 있는 폴더")
    parser.add_argument("--merge-work", type=Path, nargs="+", help="다른 작업자에게 받은 work 폴더를 현재 결과에 병합")
    parser.add_argument("--session-id", help="폴더 자동 등록 시 사용할 촬영 묶음 이름")
    parser.add_argument("--review", action="store_true", help="등록 후 이미지를 추출하고 수동 박스 작업 창 열기")
    parser.add_argument("--dry-run", action="store_true")
    arguments = parser.parse_args()
    if arguments.merge_work and any((arguments.plan, arguments.source_dir, arguments.session_id, arguments.review, arguments.dry_run)):
        parser.error("--merge-work는 등록·검수 옵션과 따로 실행하세요.")
    if not arguments.merge_work and not arguments.plan and arguments.source_dir is None:
        parser.error("--source-dir 영상폴더 또는 --plan 촬영계획을 지정하세요.")
    if arguments.review and arguments.dry_run:
        parser.error("--review와 --dry-run은 함께 사용할 수 없습니다.")
    if arguments.session_id and arguments.plan:
        parser.error("--session-id는 --plan 없이 폴더를 자동 등록할 때 사용합니다.")
    return arguments


def main() -> int:
    """녹화 정식 등록을 실행하고 계약 위반을 실패 코드로 반환한다."""
    arguments = parse_args()
    try:
        if arguments.merge_work:
            counts = merge_work(arguments.config.resolve(), [p.resolve() for p in arguments.merge_work])
            print(f"병합 완료: 새 사진 {counts['added']} / 완료 박스 반영 {counts['updated']} / 중복 {counts['duplicate']}")
            return 0
        counts = register_recordings(
            arguments.config.resolve(),
            arguments.plans.resolve(),
            arguments.plan,
            arguments.source_dir.resolve() if arguments.source_dir else None,
            arguments.dry_run,
            arguments.session_id,
        )
    except (DatasetContractError, OSError, RuntimeError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    mode = "검사" if arguments.dry_run else "등록"
    print(
        f"{mode} 완료: 발견 {counts['found']} / 검증 {counts['validated']} / "
        f"등록 {counts['saved']} / 중복 {counts['duplicate']}", flush=True,
    )
    if arguments.review:
        module_dir = Path(__file__).resolve().parent
        stages = [("prepare_dataset.py", "--manual"), ("review_annotations.py",)]
        for stage in stages:
            result = subprocess.run([
                sys.executable, str(module_dir / stage[0]),
                "--config", str(arguments.config.resolve()), *stage[1:],
            ])
            if result.returncode:
                return result.returncode
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
