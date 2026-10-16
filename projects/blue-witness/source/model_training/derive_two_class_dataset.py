#!/usr/bin/env python3
"""@file derive_two_class_dataset.py
@brief 검증된 3-class COCO 데이터셋을 같은 split을 유지한 2-class 데이터셋으로 파생한다.

원본 이미지와 누수 방지 split은 그대로 복사하고 palisade_black과 other_vehicle의
category만 other_vehicle로 합친다. 새 classes·annotation·summary·manifest SHA를
다시 계산하며 원본 불변 버전은 수정하지 않는다. 2-class Nano/Tiny 비교 학습에서
동일한 frame 구성을 사용하기 위한 변환 단계다.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
import uuid
from collections import Counter
from pathlib import Path
from typing import Any

from dataset_contracts import (
    DatasetContractError,
    atomic_write_json,
    load_settings,
    read_json,
    resolve_project_path,
    sha256_file,
    sha256_value,
    validate_dataset_version_name,
)

SPLITS = ("train", "val", "test")
SOURCE_CLASSES = {0: "ioniq5_silver", 1: "palisade_black", 2: "other_vehicle"}
TARGET_CLASS_IDS = {"ioniq5_silver": 0, "palisade_black": 1, "other_vehicle": 1}


# -----------------------------------------------------------------------------
# 기능: 이미지와 split을 유지하고 팰리세이드·기타 차량 라벨만 하나로 합친다.
#
# 동작 방법:
#   1. 이미지와 split을 유지하고 팰리세이드·기타 차량 라벨만 하나로 합친다
#   2. 중간 산출물의 계약을 검사하고 완료 결과만 다음 pipeline 단계에 전달한다.
#
# 사용 이유/연결: 3-class export 결과를 입력으로 받아 2-class 학습 단계에 전달한다.
#
# 입력:
#   source_dataset (Path) - 이 함수의 source_dataset 처리에 사용하는 값.
#   target_config (Path) - 이 함수의 target_config 처리에 사용하는 값.
#   target_version (str) - 이 함수의 target_version 처리에 사용하는 값.
#
# 반환: Path 형식의 처리 결과를 반환하며 의미는 기능 설명과 같다.
# -----------------------------------------------------------------------------
def derive_dataset(
    source_dataset: Path,
    target_config: Path,
    target_version: str,
) -> Path:
    settings = load_settings(target_config)
    expected_classes = ["ioniq5_silver", "other_vehicle"]
    actual_classes = [item["name"] for item in settings["classes"]]
    if actual_classes != expected_classes:
        raise DatasetContractError(
            f"2-class config 순서는 {expected_classes}여야 합니다: {actual_classes}"
        )
    validate_dataset_version_name(target_version)
    source_manifest = read_json(source_dataset / "dataset_manifest.json")
    source_classes = read_json(source_dataset / "classes.json")
    if {item["id"]: item["name"] for item in source_classes} != SOURCE_CLASSES:
        raise DatasetContractError("원본 데이터셋이 예상한 3-class 계약과 다릅니다.")

    target_root = resolve_project_path(target_config, settings["paths"]["datasets_dir"])
    destination = target_root / target_version
    if destination.exists():
        raise DatasetContractError(f"기존 데이터셋은 덮어쓸 수 없습니다: {destination}")
    target_root.mkdir(parents=True, exist_ok=True)
    staging = target_root / f".{target_version}.staging-{uuid.uuid4().hex}"
    staging.mkdir()
    file_hashes: dict[str, str] = {}
    class_counts: Counter[str] = Counter()
    instance_counts: Counter[str] = Counter()
    split_counts: Counter[str] = Counter()
    object_count = 0
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

        for split in SPLITS:
            source_images = source_dataset / "images" / split
            target_images = staging / "images" / split
            shutil.copytree(source_images, target_images)
            source_coco = read_json(source_dataset / "annotations" / f"instances_{split}.json")
            source_coco["info"]["version"] = target_version
            source_coco["info"]["description"] = "Two-class model vehicle detection dataset"
            source_coco["categories"] = [
                {"id": item["id"], "name": item["name"], "supercategory": "vehicle"}
                for item in settings["classes"]
            ]
            for annotation in source_coco["annotations"]:
                source_name = SOURCE_CLASSES.get(annotation["category_id"])
                if source_name is None:
                    raise DatasetContractError(
                        f"알 수 없는 원본 category_id={annotation['category_id']}"
                    )
                annotation["category_id"] = TARGET_CLASS_IDS[source_name]
                class_counts[expected_classes[annotation["category_id"]]] += 1
                instance_counts[annotation["vehicle_instance"]] += 1
                object_count += 1
            annotations_path = staging / "annotations" / f"instances_{split}.json"
            atomic_write_json(annotations_path, source_coco)
            file_hashes[annotations_path.relative_to(staging).as_posix()] = sha256_file(
                annotations_path
            )
            split_counts[split] = len(source_coco["images"])
            for image_path in target_images.iterdir():
                relative = image_path.relative_to(staging).as_posix()
                file_hashes[relative] = sha256_file(image_path)

        frame_trace = source_manifest["frame_trace"]
        component_assignment = source_manifest["component_assignment"]
        dataset_sha = sha256_value(
            {
                "version": target_version,
                "files": file_hashes,
                "frame_trace": frame_trace,
                "component_assignment": component_assignment,
                "classes": classes_payload,
            }
        )
        manifest = {
            **source_manifest,
            "dataset_version": target_version,
            "dataset_sha256": dataset_sha,
            "files": file_hashes,
            "derived_from_dataset": source_manifest["dataset_version"],
            "derived_from_dataset_sha256": source_manifest["dataset_sha256"],
        }
        atomic_write_json(staging / "dataset_manifest.json", manifest)
        source_summary = read_json(source_dataset / "dataset_summary.json")
        summary: dict[str, Any] = {
            **source_summary,
            "dataset_version": target_version,
            "dataset_sha256": dataset_sha,
            "object_count": object_count,
            "counts_by_split": dict(split_counts),
            "counts_by_class": dict(sorted(class_counts.items())),
            "counts_by_vehicle_instance": dict(sorted(instance_counts.items())),
            "derived_from_dataset": source_manifest["dataset_version"],
        }
        # 3-class 기준으로 만들어진 세부 클래스 표는 오해를 막기 위해 제거한다.
        summary.pop("counts_by_split_and_class", None)
        atomic_write_json(staging / "dataset_summary.json", summary)
        os.replace(staging, destination)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return destination


def parse_args() -> argparse.Namespace:
    """원본 데이터셋과 파생할 2-class 버전을 CLI에서 읽는다."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dataset", type=Path, required=True)
    parser.add_argument("--target-config", type=Path, default=Path("config_2class_tiny.yaml"))
    parser.add_argument("--target-version", default="dataset_v001")
    return parser.parse_args()


def main() -> int:
    """2-class 파생 작업을 실행하고 사용자가 확인할 결과 경로를 출력한다."""
    arguments = parse_args()
    try:
        destination = derive_dataset(
            arguments.source_dataset.resolve(),
            arguments.target_config.resolve(),
            arguments.target_version,
        )
    except (DatasetContractError, OSError, KeyError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print(f"2-class 데이터셋 생성 완료: {destination}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
