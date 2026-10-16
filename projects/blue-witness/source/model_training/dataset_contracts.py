#!/usr/bin/env python3
"""데이터셋 파이프라인이 공유하는 계약, 입출력, 검증 도구."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

import cv2
import yaml


RECORDING_MANIFEST = "recording_manifest.json"
ANNOTATION_STATES = {
    "draft",
    "manual_review_required",
    "approved",
    "negative",
    "rejected",
}
DATASET_USAGES = {"development", "final_test"}
EXPORTABLE_STATES = {"approved", "negative"}
DATASET_VERSION_PATTERN = re.compile(r"^dataset_v(\d{3,})$")
IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


class DatasetContractError(RuntimeError):
    """입력 데이터나 설정이 명시된 계약을 위반했음을 나타낸다."""


@dataclass(frozen=True)
class VideoMetadata:
    """ffprobe에서 읽은 단일 비디오 스트림 정보."""

    codec: str
    width: int
    height: int
    fps: float
    bitrate_bps: int
    duration_seconds: float
    frame_count: int | None


def read_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def atomic_write_json(path: Path, value: Any) -> None:
    """같은 디렉터리의 임시 파일을 교체하여 JSON을 원자적으로 저장한다."""
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
        os.replace(temporary_name, path)
    except Exception:
        Path(temporary_name).unlink(missing_ok=True)
        raise


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    if not path.exists():
        return records
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as error:
                raise DatasetContractError(
                    f"JSONL 파싱 실패: {path}:{line_number}: {error}"
                ) from error
            if not isinstance(value, dict):
                raise DatasetContractError(f"JSONL 레코드는 객체여야 합니다: {path}:{line_number}")
            records.append(value)
    return records


def atomic_write_jsonl(path: Path, records: Iterable[dict[str, Any]]) -> None:
    """전체 JSONL을 임시 파일에 쓴 뒤 원자적으로 교체한다."""
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            for record in records:
                handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True))
                handle.write("\n")
        os.replace(temporary_name, path)
    except Exception:
        Path(temporary_name).unlink(missing_ok=True)
        raise


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_value(value: Any) -> str:
    payload = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _require_mapping(value: Any, context: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise DatasetContractError(f"{context}는 객체여야 합니다.")
    return value


def load_settings(config_path: Path) -> dict[str, Any]:
    """설정을 읽고 클래스·경로·분할 계약을 엄격히 검증한다."""
    with config_path.open("r", encoding="utf-8") as handle:
        raw = yaml.safe_load(handle)
    settings = _require_mapping(raw, "설정")
    if settings.get("schema_version") != 2:
        raise DatasetContractError("config.yaml의 schema_version은 2여야 합니다.")

    classes = settings.get("classes")
    if not isinstance(classes, list) or not classes:
        raise DatasetContractError("classes는 비어 있지 않은 배열이어야 합니다.")
    expected_ids = list(range(len(classes)))
    actual_ids = [item.get("id") for item in classes if isinstance(item, dict)]
    if actual_ids != expected_ids:
        raise DatasetContractError(f"클래스 ID는 0부터 연속이어야 합니다: {actual_ids}")
    names = [item.get("name") for item in classes]
    if len(names) != len(set(names)) or not all(isinstance(name, str) and name for name in names):
        raise DatasetContractError("클래스 이름은 비어 있지 않고 서로 달라야 합니다.")
    instances: list[str] = []
    for item in classes:
        values = item.get("vehicle_instances")
        if not isinstance(values, list) or not values or not all(isinstance(v, str) and v for v in values):
            raise DatasetContractError(f"{item['name']}의 vehicle_instances가 올바르지 않습니다.")
        instances.extend(values)
    if len(instances) != len(set(instances)):
        raise DatasetContractError("vehicle_instance 이름은 클래스 전체에서 고유해야 합니다.")

    paths = _require_mapping(settings.get("paths"), "paths")
    for name in ("recordings_dir", "work_dir", "datasets_dir"):
        if not isinstance(paths.get(name), str) or not paths[name]:
            raise DatasetContractError(f"paths.{name}이 필요합니다.")

    export = _require_mapping(settings.get("dataset_export"), "dataset_export")
    ratios = [float(export.get(f"{name}_ratio", -1)) for name in ("train", "val", "test")]
    if any(value <= 0 for value in ratios) or not math.isclose(sum(ratios), 1.0, abs_tol=1e-9):
        raise DatasetContractError("train/val/test 비율은 모두 양수이고 합계가 1이어야 합니다.")

    profiles = _require_mapping(settings.get("encoding_profiles"), "encoding_profiles")
    if not profiles:
        raise DatasetContractError("encoding_profiles가 비어 있습니다.")
    for name, profile_value in profiles.items():
        profile = _require_mapping(profile_value, f"encoding_profiles.{name}")
        for key in ("codec", "width", "height", "fps", "target_bitrate_bps"):
            if profile.get(key) is None:
                raise DatasetContractError(f"encoding_profiles.{name}.{key}이 필요합니다.")
        if int(profile["width"]) <= 0 or int(profile["height"]) <= 0 or float(profile["fps"]) <= 0:
            raise DatasetContractError(f"encoding_profiles.{name}의 크기와 FPS는 양수여야 합니다.")

    extraction = _require_mapping(settings.get("frame_extraction"), "frame_extraction")
    if int(extraction.get("frame_interval", 0)) <= 0:
        raise DatasetContractError("frame_extraction.frame_interval은 양수여야 합니다.")
    quality = int(extraction.get("jpeg_quality", 0))
    if not 1 <= quality <= 100:
        raise DatasetContractError("frame_extraction.jpeg_quality는 1~100이어야 합니다.")
    _require_mapping(settings.get("proposal_detector"), "proposal_detector")
    if int(export.get("split_search_attempts", 0)) <= 0:
        raise DatasetContractError("dataset_export.split_search_attempts는 양수여야 합니다.")
    _require_mapping(settings.get("summary"), "summary")
    return settings


def resolve_project_path(config_path: Path, value: str) -> Path:
    path = Path(value).expanduser()
    return path if path.is_absolute() else (config_path.parent / path).resolve()


def class_maps(settings: dict[str, Any]) -> tuple[dict[str, int], dict[str, str]]:
    """클래스 ID와 차량 인스턴스 소속을 빠르게 조회할 맵을 만든다."""
    class_to_id: dict[str, int] = {}
    instance_to_class: dict[str, str] = {}
    for item in settings["classes"]:
        class_to_id[item["name"]] = int(item["id"])
        for instance in item["vehicle_instances"]:
            instance_to_class[instance] = item["name"]
    return class_to_id, instance_to_class


def validate_recording_manifest(
    manifest: dict[str, Any], settings: dict[str, Any], manifest_path: Path
) -> None:
    """녹화 단위 추적성과 파생 영상 관계를 검증한다."""
    if manifest.get("schema_version") != 2:
        raise DatasetContractError(f"{manifest_path}: schema_version은 2여야 합니다.")
    required_strings = (
        "recording_id",
        "session_id",
        "source_group_id",
        "encoding_profile",
        "recorded_at_utc",
        "video_file",
    )
    for key in required_strings:
        if not isinstance(manifest.get(key), str) or not manifest[key].strip():
            raise DatasetContractError(f"{manifest_path}: {key} 문자열이 필요합니다.")
    for key in ("recording_id", "session_id", "source_group_id"):
        if IDENTIFIER_PATTERN.fullmatch(manifest[key]) is None:
            raise DatasetContractError(
                f"{manifest_path}: {key}에는 영문자·숫자·점·하이픈·언더스코어만 쓸 수 있습니다."
            )
    video_file = Path(manifest["video_file"])
    if video_file.is_absolute() or video_file.name != manifest["video_file"]:
        raise DatasetContractError(f"{manifest_path}: video_file은 같은 폴더의 파일명이어야 합니다.")
    try:
        recorded_at = datetime.fromisoformat(manifest["recorded_at_utc"].replace("Z", "+00:00"))
    except ValueError as error:
        raise DatasetContractError(f"{manifest_path}: recorded_at_utc가 ISO-8601이 아닙니다.") from error
    if recorded_at.tzinfo is None:
        raise DatasetContractError(f"{manifest_path}: recorded_at_utc에 UTC 오프셋이 필요합니다.")
    if manifest["encoding_profile"] not in settings["encoding_profiles"]:
        raise DatasetContractError(
            f"{manifest_path}: 알 수 없는 encoding_profile={manifest['encoding_profile']}"
        )
    dataset_usage = manifest.get("dataset_usage", "development")
    if dataset_usage not in DATASET_USAGES:
        raise DatasetContractError(
            f"{manifest_path}: dataset_usage는 development 또는 final_test여야 합니다."
        )
    derived_from = manifest.get("derived_from")
    if derived_from is not None and (not isinstance(derived_from, str) or not derived_from.strip()):
        raise DatasetContractError(f"{manifest_path}: derived_from은 null 또는 문자열이어야 합니다.")
    tags = manifest.get("condition_tags")
    if not isinstance(tags, list) or not all(isinstance(tag, str) and tag for tag in tags):
        raise DatasetContractError(f"{manifest_path}: condition_tags는 문자열 배열이어야 합니다.")
    present = manifest.get("vehicle_instances_present")
    _, instance_to_class = class_maps(settings)
    if not isinstance(present, list) or any(item not in instance_to_class for item in present):
        raise DatasetContractError(
            f"{manifest_path}: vehicle_instances_present에 알 수 없는 값이 있습니다."
        )


def discover_recordings(recordings_dir: Path, settings: dict[str, Any]) -> list[tuple[Path, dict[str, Any]]]:
    """하위 깊이와 무관하게 녹화 manifest를 찾아 비디오와 연결한다."""
    discovered: list[tuple[Path, dict[str, Any]]] = []
    recording_ids: set[str] = set()
    for manifest_path in sorted(recordings_dir.rglob(RECORDING_MANIFEST)):
        manifest = read_json(manifest_path)
        validate_recording_manifest(manifest, settings, manifest_path)
        recording_id = manifest["recording_id"]
        if recording_id in recording_ids:
            raise DatasetContractError(f"중복 recording_id: {recording_id}")
        recording_ids.add(recording_id)
        video_path = manifest_path.parent / manifest["video_file"]
        if not video_path.is_file():
            raise DatasetContractError(f"녹화 파일이 없습니다: {video_path}")
        discovered.append((video_path, manifest))
    if not discovered:
        raise DatasetContractError(f"{recordings_dir}에서 {RECORDING_MANIFEST}를 찾지 못했습니다.")
    manifests_by_id = {manifest["recording_id"]: manifest for _, manifest in discovered}
    for _, manifest in discovered:
        parent_id = manifest.get("derived_from")
        if parent_id is None:
            continue
        if parent_id == manifest["recording_id"] or parent_id not in manifests_by_id:
            raise DatasetContractError(
                f"{manifest['recording_id']}: derived_from={parent_id} 녹화를 찾을 수 없습니다."
            )
        parent = manifests_by_id[parent_id]
        if (
            parent["session_id"] != manifest["session_id"]
            or parent["source_group_id"] != manifest["source_group_id"]
        ):
            raise DatasetContractError(
                f"{manifest['recording_id']}: 파생 영상은 원본과 session_id/source_group_id가 같아야 합니다."
            )
    return discovered


def _run_ffprobe(arguments: list[str], video_path: Path) -> dict[str, Any]:
    command = ["ffprobe", "-v", "error", *arguments, "-of", "json", str(video_path)]
    try:
        completed = subprocess.run(command, check=True, capture_output=True, text=True)
    except FileNotFoundError as error:
        raise DatasetContractError("ffprobe가 없습니다. ffmpeg를 먼저 설치해 주세요.") from error
    except subprocess.CalledProcessError as error:
        message = error.stderr.strip() or error.stdout.strip()
        raise DatasetContractError(f"ffprobe 실패: {video_path}: {message}") from error
    try:
        return json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        raise DatasetContractError(f"ffprobe JSON 파싱 실패: {video_path}") from error


def _parse_fraction(value: str | None) -> float:
    if not value or value in {"0/0", "N/A"}:
        return 0.0
    numerator, denominator = value.split("/", maxsplit=1)
    return float(numerator) / float(denominator)


def probe_video(video_path: Path) -> VideoMetadata:
    """컨테이너가 아닌 첫 영상 스트림의 실제 코덱·크기·FPS·비트레이트를 읽는다."""
    payload = _run_ffprobe(
        [
            "-select_streams", "v:0",
            "-show_entries",
            "stream=codec_name,width,height,avg_frame_rate,nb_frames,bit_rate,duration:format=duration,bit_rate",
        ],
        video_path,
    )
    streams = payload.get("streams") or []
    if len(streams) != 1:
        raise DatasetContractError(f"첫 영상 스트림을 읽을 수 없습니다: {video_path}")
    stream = streams[0]
    format_data = payload.get("format") or {}
    bitrate = stream.get("bit_rate") or format_data.get("bit_rate") or 0
    duration = stream.get("duration") or format_data.get("duration") or 0
    frame_count = stream.get("nb_frames")
    return VideoMetadata(
        codec=str(stream.get("codec_name") or ""),
        width=int(stream.get("width") or 0),
        height=int(stream.get("height") or 0),
        fps=_parse_fraction(stream.get("avg_frame_rate")),
        bitrate_bps=int(float(bitrate)),
        duration_seconds=float(duration),
        frame_count=int(frame_count) if frame_count not in {None, "N/A"} else None,
    )


def probe_frame_pts(video_path: Path) -> list[float | None]:
    """프레임별 best-effort PTS를 디코딩 순서대로 읽는다."""
    payload = _run_ffprobe(
        [
            "-select_streams", "v:0",
            "-show_frames",
            "-show_entries", "frame=best_effort_timestamp_time",
        ],
        video_path,
    )
    timestamps: list[float | None] = []
    for frame in payload.get("frames") or []:
        raw = frame.get("best_effort_timestamp_time")
        timestamps.append(float(raw) if raw not in {None, "N/A"} else None)
    return timestamps


def validate_video_profile(
    metadata: VideoMetadata, profile_name: str, settings: dict[str, Any], video_path: Path
) -> None:
    """실제 영상 스트림이 선언된 프로파일의 배포 입력 계약과 맞는지 확인한다."""
    profile = settings["encoding_profiles"][profile_name]
    problems: list[str] = []
    if metadata.codec != str(profile["codec"]):
        problems.append(f"codec={metadata.codec}, expected={profile['codec']}")
    if metadata.width != int(profile["width"]) or metadata.height != int(profile["height"]):
        problems.append(
            f"size={metadata.width}x{metadata.height}, expected={profile['width']}x{profile['height']}"
        )
    if not math.isclose(metadata.fps, float(profile["fps"]), rel_tol=0.0, abs_tol=0.05):
        problems.append(f"fps={metadata.fps:.6g}, expected={profile['fps']}")
    if metadata.bitrate_bps <= 0:
        problems.append("bitrate를 ffprobe에서 읽지 못함")
    if problems:
        raise DatasetContractError(f"영상 프로파일 불일치: {video_path}: " + "; ".join(problems))


def normalize_bbox(bbox: Iterable[float], width: int, height: int) -> list[float] | None:
    """xywh 상자를 영상 경계로 제한하고 면적 없는 상자를 제거한다."""
    values = [float(value) for value in bbox]
    if len(values) != 4 or not all(math.isfinite(value) for value in values):
        return None
    x, y, box_width, box_height = values
    x1 = min(max(x, 0.0), float(width))
    y1 = min(max(y, 0.0), float(height))
    x2 = min(max(x + box_width, 0.0), float(width))
    y2 = min(max(y + box_height, 0.0), float(height))
    if x2 <= x1 or y2 <= y1:
        return None
    return [round(x1, 3), round(y1, 3), round(x2 - x1, 3), round(y2 - y1, 3)]


def write_jpeg(path: Path, frame: Any, quality: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    success, encoded = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, quality])
    if not success:
        raise DatasetContractError(f"JPEG 인코딩 실패: {path}")
    temporary_path = path.with_name(f".{path.name}.tmp")
    temporary_path.write_bytes(encoded.tobytes())
    os.replace(temporary_path, path)


def next_dataset_version(datasets_dir: Path) -> str:
    """기존 버전을 덮어쓰지 않는 다음 dataset_vNNN 이름을 계산한다."""
    latest = 0
    if datasets_dir.exists():
        for path in datasets_dir.iterdir():
            match = DATASET_VERSION_PATTERN.fullmatch(path.name)
            if match:
                latest = max(latest, int(match.group(1)))
    return f"dataset_v{latest + 1:03d}"


def validate_dataset_version_name(value: str) -> None:
    if DATASET_VERSION_PATTERN.fullmatch(value) is None:
        raise DatasetContractError("데이터셋 버전은 dataset_v001 형식이어야 합니다.")
