#!/usr/bin/env python3
"""@file export_coco.py
@brief 수동 검수가 끝난 frame을 누수 없는 불변 COCO 데이터셋 버전으로 내보낸다.

session과 source_group의 연결 컴포넌트를 하나의 split 단위로 묶고 train/val/test에
배정한다. 승인 객체와 negative frame만 포함하고 rejected 및 final_test 자료는
학습 데이터에서 제외한다. 이미지·annotation·class 계약·원본 추적 정보·통계와
SHA를 새 dataset_vNNN에 기록하며 기존 버전은 절대 덮어쓰지 않는다.
"""

from __future__ import annotations

import argparse
import os
import random
import shutil
import statistics
import sys
import uuid
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from dataset_contracts import (
    DatasetContractError,
    atomic_write_json,
    class_maps,
    load_settings,
    next_dataset_version,
    normalize_bbox,
    read_jsonl,
    resolve_project_path,
    sha256_file,
    sha256_value,
    validate_dataset_version_name,
)


SPLITS = ("train", "val", "test")


# ============================================================================
# 원본 계열을 보존하는 split 컴포넌트 구성
# ============================================================================

class UnionFind:
    """세션과 소스 그룹의 연결 관계를 하나의 분할 컴포넌트로 묶는다."""

    def __init__(self) -> None:
        """각 session/source key가 처음에는 자기 자신을 부모로 갖도록 준비한다."""
        self.parent: dict[str, str] = {}

    def find(self, value: str) -> str:
        """경로 압축을 적용해 값이 속한 연결 컴포넌트의 대표 key를 반환한다."""
        self.parent.setdefault(value, value)
        if self.parent[value] != value:
            self.parent[value] = self.find(self.parent[value])
        return self.parent[value]

    def union(self, left: str, right: str) -> None:
        """두 key의 대표를 합쳐 같은 원본 계열이 서로 다른 split으로 새지 않게 한다."""
        left_root, right_root = self.find(left), self.find(right)
        if left_root != right_root:
            self.parent[right_root] = left_root


def _components(frames: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """session과 source_group 연결 관계를 split 배정 최소 단위로 묶는다."""
    union_find = UnionFind()
    for frame in frames:
        union_find.union(f"session:{frame['session_id']}", f"source:{frame['source_group_id']}")
    components: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for frame in frames:
        root = union_find.find(f"session:{frame['session_id']}")
        components[root].append(frame)
    return dict(components)


# -----------------------------------------------------------------------------
# 기능: 누수를 막으면서 클래스·인스턴스·인코딩 계약을 만족하도록 컴포넌트를 배치한다.
#
# 동작 방법:
#   1. 누수를 막으면서 클래스·인스턴스·인코딩 계약을 만족하도록 컴포넌트를 배치한다
#   2. 입력 계약을 유지한 결과를 만들어 호출한 pipeline 단계에 반환한다.
#
# 사용 이유/연결: 수동 검수 결과를 입력으로 받아 validate_dataset.py와 train.py에 전달한다.
#
# 입력:
#   components (dict[str, list[dict[str, Any]]]) - 이 함수의 components 처리에 사용하는 값.
#   annotations (dict[str, dict[str, Any]]) - 이 함수의 annotations 처리에 사용하는 값.
#   settings (dict[str, Any]) - 검증을 통과한 프로젝트 설정 객체.
#
# 반환: dict[str, str] 형식의 처리 결과를 반환하며 의미는 기능 설명과 같다.
# -----------------------------------------------------------------------------
def _assign_splits(
    components: dict[str, list[dict[str, Any]]],
    annotations: dict[str, dict[str, Any]],
    settings: dict[str, Any],
) -> dict[str, str]:
    if len(components) < len(SPLITS):
        raise DatasetContractError(
            f"누수 없이 3개 split을 만들려면 독립 컴포넌트가 최소 3개 필요합니다. 현재 {len(components)}개"
        )
    export = settings["dataset_export"]
    ratios = {name: float(export[f"{name}_ratio"]) for name in SPLITS}
    required_profiles = {
        name
        for name, profile in settings["encoding_profiles"].items()
        if bool(profile.get("required_in_every_split", False))
    }
    required_classes = {item["name"] for item in settings["classes"]}
    required_instances = {
        instance
        for item in settings["classes"]
        for instance in item["vehicle_instances"]
    }
    component_class_counts: dict[str, Counter[str]] = {}
    component_instance_counts: dict[str, Counter[str]] = {}
    for key, component_frames in components.items():
        class_counts: Counter[str] = Counter()
        instance_counts: Counter[str] = Counter()
        for frame in component_frames:
            for item in annotations[frame["frame_id"]]["objects"]:
                class_counts[item["class_name"]] += 1
                instance_counts[item["vehicle_instance"]] += 1
        component_class_counts[key] = class_counts
        component_instance_counts[key] = instance_counts

    available_classes = set().union(*(set(values) for values in component_class_counts.values()))
    available_instances = set().union(*(set(values) for values in component_instance_counts.values()))
    if available_classes != required_classes or available_instances != required_instances:
        raise DatasetContractError(
            "전체 데이터에 config의 클래스/vehicle_instance가 모두 존재하지 않습니다. "
            f"누락 클래스={sorted(required_classes - available_classes)}, "
            f"누락 인스턴스={sorted(required_instances - available_instances)}"
        )
    total = sum(len(values) for values in components.values())
    total_class_counts = sum(component_class_counts.values(), Counter())
    total_instance_counts = sum(component_instance_counts.values(), Counter())
    best: tuple[float, dict[str, str]] | None = None
    keys = sorted(components)
    rng = random.Random(int(export["random_seed"]))
    attempts = int(export["split_search_attempts"])
    for _ in range(attempts):
        rng.shuffle(keys)
        counts = Counter({name: 0 for name in SPLITS})
        profiles: dict[str, set[str]] = {name: set() for name in SPLITS}
        class_counts_by_split = {name: Counter() for name in SPLITS}
        instance_counts_by_split = {name: Counter() for name in SPLITS}
        assignment: dict[str, str] = {}
        for key in keys:
            split = min(
                SPLITS,
                key=lambda name: counts[name] / max(ratios[name] * total, 1.0) + rng.random() * 0.02,
            )
            assignment[key] = split
            counts[split] += len(components[key])
            profiles[split].update(frame["encoding_profile"] for frame in components[key])
            class_counts_by_split[split].update(component_class_counts[key])
            instance_counts_by_split[split].update(component_instance_counts[key])
        if any(counts[name] == 0 for name in SPLITS):
            continue
        if any(not required_profiles.issubset(profiles[name]) for name in SPLITS):
            continue
        if any(not required_classes.issubset(class_counts_by_split[name]) for name in SPLITS):
            continue
        if any(not required_instances.issubset(instance_counts_by_split[name]) for name in SPLITS):
            continue
        score = sum((counts[name] / total - ratios[name]) ** 2 for name in SPLITS)
        # 프레임 비율뿐 아니라 각 클래스와 실제 차량 인스턴스의 비율도 함께 맞춘다.
        for name in SPLITS:
            score += sum(
                (class_counts_by_split[name][class_name] / total_class_counts[class_name] - ratios[name]) ** 2
                for class_name in required_classes
            )
            score += 0.25 * sum(
                (
                    instance_counts_by_split[name][instance] / total_instance_counts[instance]
                    - ratios[name]
                ) ** 2
                for instance in required_instances
            )
        if best is None or score < best[0]:
            best = (score, dict(assignment))
    if best is None:
        required = ", ".join(sorted(required_profiles)) or "없음"
        raise DatasetContractError(
            "분할 조건을 만족하지 못했습니다. 각 클래스와 vehicle_instance가 독립된 "
            "session/source 컴포넌트 최소 3개에 존재하는지 확인하고, 각 split의 "
            f"필수 프로파일({required}) 수를 확인해 주세요."
        )
    return best[1]


# ============================================================================
# 불변 COCO 데이터셋 생성과 통계 기록
# ============================================================================

def _bbox_statistics(width_ratios: list[float], configured_bins: Any) -> dict[str, Any]:
    """bbox 너비 비율의 범위·중앙값과 선택적 구간별 분포를 요약한다."""
    if not width_ratios:
        return {"count": 0, "min": None, "median": None, "max": None, "bins": None}
    result: dict[str, Any] = {
        "count": len(width_ratios),
        "min": round(min(width_ratios), 6),
        "median": round(statistics.median(width_ratios), 6),
        "max": round(max(width_ratios), 6),
        "bins": None,
    }
    if configured_bins is not None:
        if not isinstance(configured_bins, list) or any(float(value) <= 0 for value in configured_bins):
            raise DatasetContractError("summary.bbox_width_ratio_bins는 양수 배열 또는 null이어야 합니다.")
        boundaries = sorted(float(value) for value in configured_bins)
        counts = [0] * (len(boundaries) + 1)
        for ratio in width_ratios:
            index = next((i for i, boundary in enumerate(boundaries) if ratio < boundary), len(boundaries))
            counts[index] += 1
        result["bins"] = {"boundaries": boundaries, "counts": counts}
    return result


# -----------------------------------------------------------------------------
# 기능: 검수 상태를 검증하고 새 dataset_vNNN 디렉터리를 한 번만 생성한다.
#
# 동작 방법:
#   1. frame manifest와 annotation의 frame_id 집합 및 모든 검수 상태를 확인한다.
#   2. final_test와 rejected frame을 제외하고 development 자료만 export 대상으로 고른다.
#   3. session/source_group 연결 컴포넌트를 만들어 같은 원본 계열의 split 누수를 막는다.
#   4. class·instance·encoding profile이 모두 포함되도록 train/val/test를 배정한다.
#   5. JPEG와 COCO annotation, classes, 추적 manifest와 통계를 staging에 생성한다.
#   6. 파일별 SHA와 dataset SHA를 계산한 뒤 staging을 새 불변 버전으로 교체한다.
#
# 사용 이유/연결: 수동 검수 결과를 입력으로 받아 validate_dataset.py와 train.py에 전달한다.
#
# 입력:
#   config_path (Path) - 경로·class·threshold 등 실행 계약이 기록된 YAML config 경로.
#   requested_version (str | None) - 생성할 dataset_vNNN 이름. None이면 다음 번호를 자동 선택한다.
#
# 반환: 검증 가능한 COCO 파일이 모두 생성된 새 데이터셋 루트 Path를 반환한다.
# -----------------------------------------------------------------------------
def export_dataset(config_path: Path, requested_version: str | None) -> Path:
    settings = load_settings(config_path)
    work_dir = resolve_project_path(config_path, settings["paths"]["work_dir"])
    datasets_dir = resolve_project_path(config_path, settings["paths"]["datasets_dir"])
    frames = read_jsonl(work_dir / "frame_manifest.jsonl")
    annotations = {item["frame_id"]: item for item in read_jsonl(work_dir / "annotations.jsonl")}
    if not frames or set(annotations) != {frame["frame_id"] for frame in frames}:
        raise DatasetContractError("frame manifest와 annotations가 없거나 서로 일치하지 않습니다.")
    # final_test는 임계값을 정한 뒤 마지막 평가에만 사용하며 학습 split에는 넣지 않는다.
    development_frames = [
        frame for frame in frames if frame.get("dataset_usage", "development") == "development"
    ]
    unresolved = [
        frame["frame_id"]
        for frame in development_frames
        if annotations[frame["frame_id"]]["status"] not in {"approved", "negative", "rejected"}
    ]
    if unresolved:
        raise DatasetContractError(
            f"검수가 끝나지 않은 프레임이 {len(unresolved)}개입니다. 예: {unresolved[:3]}"
        )
    selected = [
        frame
        for frame in development_frames
        if annotations[frame["frame_id"]]["status"] in {"approved", "negative"}
    ]
    if not bool(settings["dataset_export"].get("include_negatives", True)):
        selected = [frame for frame in selected if annotations[frame["frame_id"]]["status"] == "approved"]
    if not selected:
        raise DatasetContractError("내보낼 approved/negative 프레임이 없습니다.")
    duplicate_images = [sha for sha, count in Counter(frame["image_sha256"] for frame in selected).items() if count > 1]
    if duplicate_images:
        raise DatasetContractError(f"동일 이미지 SHA가 중복되었습니다: {duplicate_images[:3]}")

    class_to_id, instance_to_class = class_maps(settings)
    for frame in selected:
        annotation = annotations[frame["frame_id"]]
        for item in annotation["objects"]:
            class_name = item.get("class_name")
            instance = item.get("vehicle_instance")
            if class_name not in class_to_id or instance_to_class.get(instance) != class_name:
                raise DatasetContractError(f"클래스/vehicle_instance 불일치: {frame['frame_id']}")
            if normalize_bbox(item.get("bbox", []), frame["width"], frame["height"]) is None:
                raise DatasetContractError(f"유효하지 않은 bbox: {frame['frame_id']}")

    components = _components(selected)
    component_assignment = _assign_splits(components, annotations, settings)
    frame_to_split: dict[str, str] = {}
    for component, component_frames in components.items():
        for frame in component_frames:
            frame_to_split[frame["frame_id"]] = component_assignment[component]

    datasets_dir.mkdir(parents=True, exist_ok=True)
    version = requested_version or next_dataset_version(datasets_dir)
    validate_dataset_version_name(version)
    destination = datasets_dir / version
    if destination.exists():
        raise DatasetContractError(f"기존 데이터셋은 덮어쓸 수 없습니다: {destination}")
    staging = datasets_dir / f".{version}.staging-{uuid.uuid4().hex}"
    staging.mkdir()
    file_hashes: dict[str, str] = {}
    frame_trace: list[dict[str, Any]] = []
    try:
        classes_payload = [
            {
                "id": item["id"],
                "name": item["name"],
                "vehicle_instances": item["vehicle_instances"],
            }
            for item in settings["classes"]
        ]
        atomic_write_json(staging / "classes.json", classes_payload)
        file_hashes["classes.json"] = sha256_file(staging / "classes.json")
        image_id = 1
        annotation_id = 1
        split_counts: Counter[str] = Counter()
        class_counts: Counter[str] = Counter()
        instance_counts: Counter[str] = Counter()
        session_counts: Counter[str] = Counter()
        group_counts: Counter[str] = Counter()
        condition_counts: Counter[str] = Counter()
        bitrate_counts: Counter[str] = Counter()
        actual_bitrate_counts: Counter[str] = Counter()
        status_counts: Counter[str] = Counter()
        split_class_counts: dict[str, Counter[str]] = {name: Counter() for name in SPLITS}
        split_instance_counts: dict[str, Counter[str]] = {name: Counter() for name in SPLITS}
        split_profile_counts: dict[str, Counter[str]] = {name: Counter() for name in SPLITS}
        split_status_counts: dict[str, Counter[str]] = {name: Counter() for name in SPLITS}
        bbox_width_ratios: list[float] = []

        for split in SPLITS:
            images_dir = staging / "images" / split
            images_dir.mkdir(parents=True)
            coco: dict[str, Any] = {
                "info": {"description": "Model vehicle detection dataset", "version": version},
                "licenses": [],
                "categories": [
                    {"id": item["id"], "name": item["name"], "supercategory": "vehicle"}
                    for item in settings["classes"]
                ],
                "images": [],
                "annotations": [],
            }
            for frame in sorted(selected, key=lambda item: item["frame_id"]):
                if frame_to_split[frame["frame_id"]] != split:
                    continue
                source = work_dir / frame["image_path"]
                file_name = f"{frame['frame_id']}.jpg"
                target = images_dir / file_name
                shutil.copy2(source, target)
                relative_target = target.relative_to(staging).as_posix()
                copied_sha = sha256_file(target)
                if copied_sha != frame["image_sha256"]:
                    raise DatasetContractError(f"복사 중 이미지 SHA 불일치: {frame['frame_id']}")
                file_hashes[relative_target] = copied_sha
                coco["images"].append(
                    {
                        "id": image_id,
                        "file_name": file_name,
                        "width": frame["width"],
                        "height": frame["height"],
                        "frame_id": frame["frame_id"],
                        "session_id": frame["session_id"],
                        "source_group_id": frame["source_group_id"],
                    }
                )
                annotation = annotations[frame["frame_id"]]
                for item in annotation["objects"]:
                    bbox = normalize_bbox(item["bbox"], frame["width"], frame["height"])
                    assert bbox is not None
                    coco["annotations"].append(
                        {
                            "id": annotation_id,
                            "image_id": image_id,
                            "category_id": class_to_id[item["class_name"]],
                            "bbox": bbox,
                            "area": round(bbox[2] * bbox[3], 3),
                            "iscrowd": 0,
                            "vehicle_instance": item["vehicle_instance"],
                        }
                    )
                    annotation_id += 1
                    class_counts[item["class_name"]] += 1
                    instance_counts[item["vehicle_instance"]] += 1
                    split_class_counts[split][item["class_name"]] += 1
                    split_instance_counts[split][item["vehicle_instance"]] += 1
                    bbox_width_ratios.append(bbox[2] / frame["width"])
                frame_trace.append(
                    {
                        "frame_id": frame["frame_id"],
                        "split": split,
                        "dataset_image": relative_target,
                        "image_sha256": copied_sha,
                        "session_id": frame["session_id"],
                        "source_group_id": frame["source_group_id"],
                        "condition_tags": frame["condition_tags"],
                        "recording_id": frame["recording_id"],
                        "source_video_sha256": frame["source_video_sha256"],
                        "source_video": frame["source_video"],
                        "derived_from": frame["derived_from"],
                        "encoding_profile": frame["encoding_profile"],
                        "dataset_usage": frame.get("dataset_usage", "development"),
                        "frame_index": frame["frame_index"],
                        "timestamp_seconds": frame["timestamp_seconds"],
                        "video_bitrate_bps": frame["video_bitrate_bps"],
                        "annotation_status": annotation["status"],
                    }
                )
                image_id += 1
                split_counts[split] += 1
                session_counts[frame["session_id"]] += 1
                group_counts[frame["source_group_id"]] += 1
                condition_counts.update(frame["condition_tags"])
                bitrate_counts[frame["encoding_profile"]] += 1
                actual_bitrate_counts[str(frame["video_bitrate_bps"])] += 1
                status_counts[annotation["status"]] += 1
                split_profile_counts[split][frame["encoding_profile"]] += 1
                split_status_counts[split][annotation["status"]] += 1
            annotations_path = staging / "annotations" / f"instances_{split}.json"
            atomic_write_json(annotations_path, coco)
            relative_annotations = annotations_path.relative_to(staging).as_posix()
            file_hashes[relative_annotations] = sha256_file(annotations_path)

        split_source_groups: dict[str, list[str]] = {name: [] for name in SPLITS}
        split_sessions: dict[str, list[str]] = {name: [] for name in SPLITS}
        for split in SPLITS:
            split_source_groups[split] = sorted(
                {item["source_group_id"] for item in frame_trace if item["split"] == split}
            )
            split_sessions[split] = sorted(
                {item["session_id"] for item in frame_trace if item["split"] == split}
            )
        hash_payload = {
            "version": version,
            "files": file_hashes,
            "frame_trace": frame_trace,
            "component_assignment": component_assignment,
            "classes": classes_payload,
        }
        dataset_sha = sha256_value(hash_payload)
        manifest = {
            "schema_version": 1,
            "dataset_version": version,
            "dataset_sha256": dataset_sha,
            "dataset_sha256_scope": "classes, COCO JSON, images, frame_trace, component_assignment",
            "files": file_hashes,
            "component_assignment": component_assignment,
            "split_source_groups": split_source_groups,
            "split_sessions": split_sessions,
            "frame_trace": frame_trace,
        }
        atomic_write_json(staging / "dataset_manifest.json", manifest)
        summary = {
            "dataset_version": version,
            "dataset_sha256": dataset_sha,
            "frame_count": len(frame_trace),
            "object_count": sum(class_counts.values()),
            "counts_by_split": dict(sorted(split_counts.items())),
            "counts_by_class": dict(sorted(class_counts.items())),
            "counts_by_vehicle_instance": dict(sorted(instance_counts.items())),
            "counts_by_session": dict(sorted(session_counts.items())),
            "counts_by_source_group": dict(sorted(group_counts.items())),
            "counts_by_condition": dict(sorted(condition_counts.items())),
            "counts_by_encoding_profile": dict(sorted(bitrate_counts.items())),
            "counts_by_actual_video_bitrate_bps": dict(sorted(actual_bitrate_counts.items())),
            "counts_by_annotation_status": dict(sorted(status_counts.items())),
            "counts_by_split_and_class": {
                split: dict(sorted(split_class_counts[split].items())) for split in SPLITS
            },
            "counts_by_split_and_vehicle_instance": {
                split: dict(sorted(split_instance_counts[split].items())) for split in SPLITS
            },
            "counts_by_split_and_encoding_profile": {
                split: dict(sorted(split_profile_counts[split].items())) for split in SPLITS
            },
            "counts_by_split_and_annotation_status": {
                split: dict(sorted(split_status_counts[split].items())) for split in SPLITS
            },
            "bbox_width_ratio_statistics": _bbox_statistics(
                bbox_width_ratios, settings.get("summary", {}).get("bbox_width_ratio_bins")
            ),
            "unique_image_sha256_count": len({item["image_sha256"] for item in frame_trace}),
            "unique_source_video_sha256_count": len(
                {item["source_video_sha256"] for item in frame_trace}
            ),
        }
        atomic_write_json(staging / "dataset_summary.json", summary)
        os.replace(staging, destination)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return destination


def parse_args() -> argparse.Namespace:
    """내보낼 config와 선택적 dataset_vNNN 이름을 CLI에서 읽는다."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("config.yaml"))
    parser.add_argument("--dataset-version", default=None)
    return parser.parse_args()


def main() -> int:
    """COCO 데이터셋 export를 실행하고 생성된 불변 버전 경로를 출력한다."""
    args = parse_args()
    try:
        destination = export_dataset(args.config.resolve(), args.dataset_version)
    except (DatasetContractError, OSError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print(f"완료: {destination}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
