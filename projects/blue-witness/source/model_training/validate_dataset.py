#!/usr/bin/env python3
"""버전 COCO 데이터셋의 SHA, 누수, 클래스, 인코딩 계약을 검증한다."""

from __future__ import annotations

import argparse
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import cv2

from dataset_contracts import (
    DATASET_VERSION_PATTERN,
    DatasetContractError,
    class_maps,
    load_settings,
    read_json,
    resolve_project_path,
    sha256_file,
    sha256_value,
)


SPLITS = ("train", "val", "test")


def _latest_dataset(datasets_dir: Path) -> Path:
    candidates = [path for path in datasets_dir.iterdir() if path.is_dir() and DATASET_VERSION_PATTERN.fullmatch(path.name)]
    if not candidates:
        raise DatasetContractError(f"검증할 dataset_vNNN 디렉터리가 없습니다: {datasets_dir}")
    return max(candidates, key=lambda path: int(DATASET_VERSION_PATTERN.fullmatch(path.name).group(1)))


def validate_dataset(config_path: Path, requested_dir: Path | None) -> dict[str, Any]:
    """모든 오류를 수집한 뒤 하나라도 있으면 성공으로 보고하지 않는다."""
    settings = load_settings(config_path)
    datasets_dir = resolve_project_path(config_path, settings["paths"]["datasets_dir"])
    dataset_dir = requested_dir.resolve() if requested_dir else _latest_dataset(datasets_dir)
    manifest_path = dataset_dir / "dataset_manifest.json"
    summary_path = dataset_dir / "dataset_summary.json"
    classes_path = dataset_dir / "classes.json"
    for path in (manifest_path, summary_path, classes_path):
        if not path.is_file():
            raise DatasetContractError(f"필수 파일이 없습니다: {path}")
    manifest = read_json(manifest_path)
    summary = read_json(summary_path)
    classes = read_json(classes_path)
    errors: list[str] = []
    if manifest.get("dataset_version") != dataset_dir.name:
        errors.append("dataset_manifest의 버전과 디렉터리 이름이 다릅니다.")
    if summary.get("dataset_version") != dataset_dir.name:
        errors.append("dataset_summary의 버전과 디렉터리 이름이 다릅니다.")
    expected_classes = [
        {"id": item["id"], "name": item["name"], "vehicle_instances": item["vehicle_instances"]}
        for item in settings["classes"]
    ]
    if classes != expected_classes:
        errors.append("classes.json이 config.yaml의 클래스 계약과 다릅니다.")

    file_hashes = manifest.get("files")
    if not isinstance(file_hashes, dict):
        errors.append("dataset_manifest.files가 객체가 아닙니다.")
        file_hashes = {}
    for relative, expected_sha in file_hashes.items():
        path = dataset_dir / relative
        if not path.is_file():
            errors.append(f"manifest 파일 누락: {relative}")
        elif sha256_file(path) != expected_sha:
            errors.append(f"파일 SHA 불일치: {relative}")

    frame_trace = manifest.get("frame_trace")
    component_assignment = manifest.get("component_assignment")
    if not isinstance(frame_trace, list) or not isinstance(component_assignment, dict):
        errors.append("frame_trace 또는 component_assignment가 올바르지 않습니다.")
        frame_trace = []
        component_assignment = {}
    computed_dataset_sha = sha256_value(
        {
            "version": dataset_dir.name,
            "files": file_hashes,
            "frame_trace": frame_trace,
            "component_assignment": component_assignment,
            "classes": classes,
        }
    )
    if computed_dataset_sha != manifest.get("dataset_sha256"):
        errors.append("dataset SHA가 manifest 내용과 일치하지 않습니다.")
    if summary.get("dataset_sha256") != manifest.get("dataset_sha256"):
        errors.append("summary와 manifest의 dataset SHA가 다릅니다.")

    session_splits: dict[str, set[str]] = defaultdict(set)
    group_splits: dict[str, set[str]] = defaultdict(set)
    source_sha_splits: dict[str, set[str]] = defaultdict(set)
    profile_splits: dict[str, set[str]] = defaultdict(set)
    image_shas: list[str] = []
    trace_by_split_frame: dict[tuple[str, str], dict[str, Any]] = {}
    for item in frame_trace:
        split = item.get("split")
        if split not in SPLITS:
            errors.append(f"알 수 없는 split: {split}")
            continue
        session_splits[item["session_id"]].add(split)
        group_splits[item["source_group_id"]].add(split)
        source_sha_splits[item["source_video_sha256"]].add(split)
        profile_splits[item["encoding_profile"]].add(split)
        image_shas.append(item["image_sha256"])
        trace_by_split_frame[(split, item["frame_id"])] = item
    for label, mapping in (
        ("session_id", session_splits),
        ("source_group_id", group_splits),
        ("source_video_sha256", source_sha_splits),
    ):
        leaking = {key: sorted(values) for key, values in mapping.items() if len(values) > 1}
        if leaking:
            errors.append(f"{label} split 누수: {leaking}")
    if len(image_shas) != len(set(image_shas)):
        errors.append("동일 이미지 SHA가 데이터셋 안에서 중복되었습니다.")
    for profile_name, profile in settings["encoding_profiles"].items():
        if profile.get("required_in_every_split") and profile_splits[profile_name] != set(SPLITS):
            errors.append(
                f"필수 프로파일 {profile_name}이 모든 split에 없습니다: "
                f"{sorted(profile_splits[profile_name])}"
            )

    _, instance_to_class = class_maps(settings)
    coco_frame_count = 0
    coco_object_count = 0
    classes_by_split: dict[str, set[str]] = {name: set() for name in SPLITS}
    instances_by_split: dict[str, set[str]] = {name: set() for name in SPLITS}
    for split in SPLITS:
        coco_path = dataset_dir / "annotations" / f"instances_{split}.json"
        if not coco_path.is_file():
            errors.append(f"COCO 파일 누락: {coco_path.relative_to(dataset_dir)}")
            continue
        coco = read_json(coco_path)
        images = coco.get("images") or []
        annotations = coco.get("annotations") or []
        image_by_id = {item["id"]: item for item in images}
        coco_frame_count += len(images)
        coco_object_count += len(annotations)
        category_by_id = {item["id"]: item["name"] for item in coco.get("categories") or []}
        for image in images:
            image_path = dataset_dir / "images" / split / image["file_name"]
            if not image_path.is_file():
                errors.append(f"COCO 이미지 누락: {image_path.relative_to(dataset_dir)}")
                continue
            decoded = cv2.imread(str(image_path))
            if decoded is None or decoded.shape[1] != image["width"] or decoded.shape[0] != image["height"]:
                errors.append(f"COCO 이미지 크기/디코딩 오류: {image_path.relative_to(dataset_dir)}")
            if (split, image.get("frame_id")) not in trace_by_split_frame:
                errors.append(f"frame_trace에 없는 COCO 이미지: {split}/{image.get('frame_id')}")
        for annotation in annotations:
            image = image_by_id.get(annotation.get("image_id"))
            class_name = category_by_id.get(annotation.get("category_id"))
            if image is None or class_name is None:
                errors.append(f"COCO 참조 오류: annotation id={annotation.get('id')}")
                continue
            if instance_to_class.get(annotation.get("vehicle_instance")) != class_name:
                errors.append(f"COCO 클래스/인스턴스 불일치: annotation id={annotation.get('id')}")
            else:
                classes_by_split[split].add(class_name)
                instances_by_split[split].add(annotation["vehicle_instance"])
            bbox = annotation.get("bbox") or []
            if len(bbox) != 4 or bbox[2] <= 0 or bbox[3] <= 0:
                errors.append(f"COCO bbox 오류: annotation id={annotation.get('id')}")
            elif bbox[0] < 0 or bbox[1] < 0 or bbox[0] + bbox[2] > image["width"] + 1e-6 or bbox[1] + bbox[3] > image["height"] + 1e-6:
                errors.append(f"COCO bbox 경계 초과: annotation id={annotation.get('id')}")

    required_classes = {item["name"] for item in settings["classes"]}
    required_instances = set(instance_to_class)
    for split in SPLITS:
        missing_classes = sorted(required_classes - classes_by_split[split])
        missing_instances = sorted(required_instances - instances_by_split[split])
        if missing_classes:
            errors.append(f"{split} split 클래스 누락: {missing_classes}")
        if missing_instances:
            errors.append(f"{split} split vehicle_instance 누락: {missing_instances}")

    if coco_frame_count != len(frame_trace):
        errors.append(f"COCO/trace 프레임 수 불일치: {coco_frame_count}/{len(frame_trace)}")
    if summary.get("frame_count") != len(frame_trace) or summary.get("object_count") != coco_object_count:
        errors.append("dataset_summary의 프레임 또는 객체 수가 실제와 다릅니다.")
    if errors:
        preview = "\n- ".join(errors[:30])
        suffix = "" if len(errors) <= 30 else f"\n... 외 {len(errors) - 30}개"
        raise DatasetContractError(f"데이터셋 검증 실패 {len(errors)}건:\n- {preview}{suffix}")
    return {
        "dataset_dir": str(dataset_dir),
        "dataset_sha256": manifest["dataset_sha256"],
        "frame_count": len(frame_trace),
        "object_count": coco_object_count,
        "counts_by_split": dict(Counter(item["split"] for item in frame_trace)),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("config.yaml"))
    parser.add_argument("--dataset-dir", type=Path, default=None)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        report = validate_dataset(args.config.resolve(), args.dataset_dir)
    except (DatasetContractError, OSError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print(
        f"검증 성공: {report['dataset_dir']} frames={report['frame_count']} "
        f"objects={report['object_count']} sha256={report['dataset_sha256']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
