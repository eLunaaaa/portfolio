#!/usr/bin/env python3
"""YAML 촬영 계획에 따라 로컬 MP4를 정식 녹화 저장소에 등록한다."""

from __future__ import annotations

import argparse
import os
import shutil
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
    load_settings,
    probe_video,
    read_json,
    resolve_project_path,
    sha256_file,
    validate_recording_manifest,
    validate_video_profile,
)

DATASET_USAGES = {"development", "final_test"}


def _string_list(value: Any, context: str) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(item, str) or not item for item in value):
        raise DatasetContractError(f"{context}는 문자열 배열이어야 합니다.")
    return value


def load_capture_plan(plans_path: Path, plan_name: str) -> dict[str, Any]:
    """촬영 계획 하나를 읽고 등록 전에 구조와 중복 항목을 검증한다."""
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


def build_manifest(source: Path, plan: dict[str, Any], item: dict[str, Any]) -> dict[str, Any]:
    """YAML 메타데이터와 파일 정보로 검증 가능한 recording manifest를 만든다."""
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
    hashes: set[str] = set()
    for manifest_path in recordings_dir.rglob(RECORDING_MANIFEST):
        manifest = read_json(manifest_path)
        digest = manifest.get("source_video_sha256")
        if isinstance(digest, str):
            hashes.add(digest)
    return hashes


def register_recordings(
    config_path: Path,
    plans_path: Path,
    plan_name: str,
    source_override: Path | None = None,
    dry_run: bool = False,
) -> dict[str, int]:
    """계획의 파일 구성과 영상 계약을 모두 확인한 뒤 원본을 보존하여 등록한다."""
    settings = load_settings(config_path)
    plan = load_capture_plan(plans_path, plan_name)
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


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("config.yaml"))
    parser.add_argument("--plans", type=Path, default=Path("capture_plans.yaml"))
    parser.add_argument("--plan", required=True)
    parser.add_argument("--source-dir", type=Path)
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def main() -> int:
    arguments = parse_args()
    try:
        counts = register_recordings(
            arguments.config.resolve(),
            arguments.plans.resolve(),
            arguments.plan,
            arguments.source_dir.resolve() if arguments.source_dir else None,
            arguments.dry_run,
        )
    except (DatasetContractError, OSError, RuntimeError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    mode = "검사" if arguments.dry_run else "등록"
    print(
        f"{mode} 완료: 발견 {counts['found']} / 검증 {counts['validated']} / "
        f"등록 {counts['saved']} / 중복 {counts['duplicate']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
