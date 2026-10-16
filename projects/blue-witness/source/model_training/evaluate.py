#!/usr/bin/env python3
"""@file evaluate.py
@brief 학습 checkpoint의 COCO 성능과 배포 confidence threshold별 오류를 평가한다.

전체 흐름
1. 불변 데이터셋과 checkpoint 계약을 교차 확인한다.
2. JPEG 또는 추적된 원본 MP4 frame에 동일한 YOLOX 전처리와 inference를 수행한다.
3. COCO AP와 class별 지표를 계산한다.
4. confidence sweep으로 TP·FP·FN, class 혼동, 조건별 오류를 집계한다.
5. Validation에서는 추천 threshold를 만들고 Test에서는 이미 선택된 threshold만 검증한다.

Test 결과를 보며 threshold를 다시 고르는 데이터 누수를 막기 위해 Val과 Test의
역할을 분리한다. 결과는 후속 ONNX release manifest에 연결되는 JSON report다.
"""

from __future__ import annotations

import argparse
import os
import sys
from collections import Counter
from pathlib import Path
from typing import Any

os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")

import cv2
import numpy as np
import torch
from pycocotools.coco import COCO
from pycocotools.cocoeval import COCOeval

from dataset_contracts import DatasetContractError, atomic_write_json, load_settings, read_json
from yolox_runtime import (
    filter_and_nms,
    load_trained_checkpoint,
    preprocess_frame,
    resolve_dataset_dir,
    select_device,
    validate_yolox_installation,
)


# ============================================================================
# bbox matching과 입력 frame 로딩
# ============================================================================

def calculate_iou(left: list[float], right: list[float]) -> float:
    """두 xywh bbox의 교집합 대비 합집합 비율을 계산한다."""
    left_x2, left_y2 = left[0] + left[2], left[1] + left[3]
    right_x2, right_y2 = right[0] + right[2], right[1] + right[3]
    intersection = max(0.0, min(left_x2, right_x2) - max(left[0], right[0])) * max(
        0.0, min(left_y2, right_y2) - max(left[1], right[1])
    )
    union = left[2] * left[3] + right[2] * right[3] - intersection
    return intersection / union if union > 0 else 0.0


# -----------------------------------------------------------------------------
# 기능: score 순서로 class와 무관한 IoU matching을 수행해 confusion 원인을 보존한다.
#
# 동작 방법:
#   1. 모델 입력 또는 prediction tensor를 설정된 계약에 맞게 처리한다.
#   2. confidence와 좌표 정보를 검증 가능한 detection 목록으로 변환한다.
#
# 사용 이유/연결: 학습 checkpoint를 입력으로 받고 결과를 threshold 선택과 ONNX export에 전달한다.
#
# 입력:
#   ground_truth (list[dict[str, Any]]) - 이 함수의 ground_truth 처리에 사용하는 값.
#   detections (list[dict[str, Any]]) - 이 함수의 detections 처리에 사용하는 값.
#   iou_threshold (float) - 이 함수의 iou_threshold 처리에 사용하는 값.
#
# 반환: tuple[list[tuple[dict[str, Any], dict[str, Any]]], list[dict[str, Any]], list[dict[str,
#   Any]]] 형식의 처리 결과를 반환하며 의미는 기능 설명과 같다.
# -----------------------------------------------------------------------------
def match_detections(
    ground_truth: list[dict[str, Any]], detections: list[dict[str, Any]], iou_threshold: float
) -> tuple[list[tuple[dict[str, Any], dict[str, Any]]], list[dict[str, Any]], list[dict[str, Any]]]:
    remaining = set(range(len(ground_truth)))
    matches: list[tuple[dict[str, Any], dict[str, Any]]] = []
    unmatched_detections: list[dict[str, Any]] = []
    for detection in sorted(detections, key=lambda item: item["score"], reverse=True):
        candidates = [(calculate_iou(detection["bbox"], ground_truth[index]["bbox"]), index) for index in remaining]
        best_iou, best_index = max(candidates, default=(0.0, -1))
        if best_iou >= iou_threshold:
            matches.append((ground_truth[best_index], detection))
            remaining.remove(best_index)
        else:
            unmatched_detections.append(detection)
    return matches, [ground_truth[index] for index in sorted(remaining)], unmatched_detections


def _read_source_frame(
    dataset_dir: Path, split: str, image: dict[str, Any], trace: dict[str, Any], input_source: str
) -> np.ndarray:
    """평가 설정에 따라 export JPEG 또는 추적된 원본 MP4 frame을 읽는다."""
    if input_source == "jpeg":
        frame = cv2.imread(str(dataset_dir / "images" / split / image["file_name"]))
    else:
        capture = cv2.VideoCapture(trace["source_video"])
        capture.set(cv2.CAP_PROP_POS_FRAMES, int(trace["frame_index"]))
        success, frame = capture.read()
        capture.release()
        if not success:
            frame = None
    if frame is None:
        raise DatasetContractError(f"평가 frame을 읽을 수 없습니다: {image['frame_id']}")
    return frame


# -----------------------------------------------------------------------------
# 기능: JPEG 또는 원본 MP4를 같은 frame_id 계약으로 추론해 COCO와 상세 결과를 함께 만든다.
#
# 동작 방법:
#   1. 모델 입력 또는 prediction tensor를 설정된 계약에 맞게 처리한다.
#   2. confidence와 좌표 정보를 검증 가능한 detection 목록으로 변환한다.
#
# 사용 이유/연결: 학습 checkpoint를 입력으로 받고 결과를 threshold 선택과 ONNX export에 전달한다.
#
# 입력:
#   model (torch.nn.Module) - 학습 또는 inference에 사용할 YOLOX model.
#   device (torch.device) - tensor 연산을 수행할 PyTorch device.
#   dataset_dir (Path) - 불변 COCO 데이터셋 버전의 루트 경로.
#   split (str) - 이 함수의 split 처리에 사용하는 값.
#   input_size (tuple[int, int]) - 이 함수의 input_size 처리에 사용하는 값.
#   minimum_confidence (float) - 이 함수의 minimum_confidence 처리에 사용하는 값.
#   nms_threshold (float) - 이 함수의 nms_threshold 처리에 사용하는 값.
#   input_source (str) - 이 함수의 input_source 처리에 사용하는 값.
#
# 반환: tuple[COCO, list[dict[str, Any]], dict[int, list[dict[str, Any]]], dict[int, dict[str,
#   Any]]] 형식의 처리 결과를 반환하며 의미는 기능 설명과 같다.
# -----------------------------------------------------------------------------
def collect_predictions(
    model: torch.nn.Module,
    device: torch.device,
    dataset_dir: Path,
    split: str,
    input_size: tuple[int, int],
    minimum_confidence: float,
    nms_threshold: float,
    input_source: str,
) -> tuple[COCO, list[dict[str, Any]], dict[int, list[dict[str, Any]]], dict[int, dict[str, Any]]]:
    annotation_path = dataset_dir / "annotations" / f"instances_{split}.json"
    coco = COCO(str(annotation_path))
    manifest = read_json(dataset_dir / "dataset_manifest.json")
    trace_by_frame = {item["frame_id"]: item for item in manifest["frame_trace"] if item["split"] == split}
    images = sorted(coco.dataset["images"], key=lambda item: item["id"])
    details: dict[int, list[dict[str, Any]]] = {}
    image_context: dict[int, dict[str, Any]] = {}
    coco_results: list[dict[str, Any]] = []
    model.eval()
    for image in images:
        trace = trace_by_frame[image["frame_id"]]
        frame = _read_source_frame(dataset_dir, split, image, trace, input_source)
        prepared, scale = preprocess_frame(frame, input_size)
        tensor = torch.from_numpy(prepared).unsqueeze(0).to(device)
        with torch.inference_mode():
            decoded = model(tensor).detach().cpu().numpy()
        detections = filter_and_nms(decoded, scale, minimum_confidence, nms_threshold)
        details[image["id"]] = detections
        image_context[image["id"]] = trace
        for detection in detections:
            coco_results.append(
                {
                    "image_id": image["id"],
                    "category_id": detection["class_id"],
                    "bbox": detection["bbox"],
                    "score": detection["score"],
                }
            )
    return coco, coco_results, details, image_context


# ============================================================================
# COCO 지표와 배포 threshold 평가
# ============================================================================

# -----------------------------------------------------------------------------
# 기능: COCO evaluator로 전체 및 class별 AP 지표를 계산한다.
#
# 동작 방법:
#   1. COCO evaluator로 전체 및 class별 AP 지표를 계산한다
#   2. 입력 계약을 유지한 결과를 만들어 호출한 pipeline 단계에 반환한다.
#
# 사용 이유/연결: 학습 checkpoint를 입력으로 받고 결과를 threshold 선택과 ONNX export에 전달한다.
#
# 입력:
#   coco (COCO) - 이 함수의 coco 처리에 사용하는 값.
#   results (list[dict[str, Any]]) - 이 함수의 results 처리에 사용하는 값.
#   class_names (list[str]) - 이 함수의 class_names 처리에 사용하는 값.
#
# 반환: dict[str, Any] 형식의 처리 결과를 반환하며 의미는 기능 설명과 같다.
# -----------------------------------------------------------------------------
def _coco_metrics(coco: COCO, results: list[dict[str, Any]], class_names: list[str]) -> dict[str, Any]:
    if not results:
        return {"map50": 0.0, "map50_95": 0.0, "per_class": {name: {"ap50": 0.0, "ap50_95": 0.0} for name in class_names}}
    detections = coco.loadRes(results)
    evaluator = COCOeval(coco, detections, "bbox")
    evaluator.evaluate()
    evaluator.accumulate()
    precision = evaluator.eval["precision"]
    per_class: dict[str, Any] = {}
    for class_index, class_name in enumerate(class_names):
        all_iou = precision[:, :, class_index, 0, -1]
        iou_50 = precision[0, :, class_index, 0, -1]
        per_class[class_name] = {
            "ap50": float(np.mean(iou_50[iou_50 > -1])) if np.any(iou_50 > -1) else 0.0,
            "ap50_95": float(np.mean(all_iou[all_iou > -1])) if np.any(all_iou > -1) else 0.0,
        }
    valid = precision[precision > -1]
    at_50 = precision[0][precision[0] > -1]
    return {
        "map50": float(np.mean(at_50)) if at_50.size else 0.0,
        "map50_95": float(np.mean(valid)) if valid.size else 0.0,
        "per_class": per_class,
    }


# -----------------------------------------------------------------------------
# 기능: 하나의 confidence threshold에서 TP·FP·FN과 혼동 유형을 집계한다.
#
# 동작 방법:
#   1. 하나의 confidence threshold에서 TP·FP·FN과 혼동 유형을 집계한다
#   2. 입력 계약을 유지한 결과를 만들어 호출한 pipeline 단계에 반환한다.
#
# 사용 이유/연결: 학습 checkpoint를 입력으로 받고 결과를 threshold 선택과 ONNX export에 전달한다.
#
# 입력:
#   coco (COCO) - 이 함수의 coco 처리에 사용하는 값.
#   predictions (dict[int, list[dict[str, Any]]]) - 이 함수의 predictions 처리에 사용하는 값.
#   threshold (float) - 이 함수의 threshold 처리에 사용하는 값.
#   iou_threshold (float) - 이 함수의 iou_threshold 처리에 사용하는 값.
#   class_names (list[str]) - 이 함수의 class_names 처리에 사용하는 값.
#   image_context (dict[int, dict[str, Any]]) - 이 함수의 image_context 처리에 사용하는 값.
#   priority_class_ids (set[int]) - 이 함수의 priority_class_ids 처리에 사용하는 값.
#
# 반환: dict[str, Any] 형식의 처리 결과를 반환하며 의미는 기능 설명과 같다.
# -----------------------------------------------------------------------------
def _threshold_metrics(
    coco: COCO,
    predictions: dict[int, list[dict[str, Any]]],
    threshold: float,
    iou_threshold: float,
    class_names: list[str],
    image_context: dict[int, dict[str, Any]],
    priority_class_ids: set[int],
) -> dict[str, Any]:
    class_count = len(class_names)
    confusion = [[0 for _ in range(class_count + 1)] for _ in range(class_count + 1)]
    true_positive = Counter()
    false_positive = Counter()
    false_negative = Counter()
    instance_errors = Counter()
    instance_ground_truth = Counter()
    instance_correct = Counter()
    instance_missed = Counter()
    condition_errors = Counter()
    encoding_profile_errors = Counter()
    bitrate_errors = Counter()
    for image_id in coco.getImgIds():
        context = image_context[image_id]
        image_error_count = 0
        ground_truth = [
            {"bbox": item["bbox"], "class_id": item["category_id"], "vehicle_instance": item.get("vehicle_instance")}
            for item in coco.loadAnns(coco.getAnnIds(imgIds=[image_id], iscrowd=False))
        ]
        instance_ground_truth.update(
            item["vehicle_instance"] for item in ground_truth if item.get("vehicle_instance")
        )
        detections = [item for item in predictions[image_id] if item["score"] >= threshold]
        matches, missed, extras = match_detections(ground_truth, detections, iou_threshold)
        for truth, detection in matches:
            confusion[truth["class_id"]][detection["class_id"]] += 1
            if truth["class_id"] == detection["class_id"]:
                true_positive[truth["class_id"]] += 1
                if truth.get("vehicle_instance"):
                    instance_correct[truth["vehicle_instance"]] += 1
            else:
                image_error_count += 1
                false_negative[truth["class_id"]] += 1
                false_positive[detection["class_id"]] += 1
                if truth.get("vehicle_instance"):
                    instance_errors[f"{truth['vehicle_instance']}->{class_names[detection['class_id']]}" ] += 1
        for truth in missed:
            image_error_count += 1
            confusion[truth["class_id"]][class_count] += 1
            false_negative[truth["class_id"]] += 1
            if truth.get("vehicle_instance"):
                instance_missed[truth["vehicle_instance"]] += 1
        for detection in extras:
            image_error_count += 1
            confusion[class_count][detection["class_id"]] += 1
            false_positive[detection["class_id"]] += 1
        if image_error_count:
            for condition in context.get("condition_tags", []):
                condition_errors[condition] += image_error_count
            encoding_profile_errors[context["encoding_profile"]] += image_error_count
            bitrate_errors[str(context["video_bitrate_bps"])] += image_error_count
    per_class = {}
    for class_id, name in enumerate(class_names):
        precision_denominator = true_positive[class_id] + false_positive[class_id]
        recall_denominator = true_positive[class_id] + false_negative[class_id]
        per_class[name] = {
            "precision": true_positive[class_id] / precision_denominator if precision_denominator else 0.0,
            "recall": true_positive[class_id] / recall_denominator if recall_denominator else 0.0,
            "true_positive": true_positive[class_id],
            "false_positive": false_positive[class_id],
            "false_negative": false_negative[class_id],
        }
    target_false_positives = sum(false_positive[class_id] for class_id in priority_class_ids)
    target_tp = sum(true_positive[class_id] for class_id in priority_class_ids)
    target_fn = sum(false_negative[class_id] for class_id in priority_class_ids)
    return {
        "threshold": round(threshold, 6),
        "per_class": per_class,
        "target_recall": target_tp / (target_tp + target_fn) if target_tp + target_fn else 0.0,
        "target_false_positive_count": target_false_positives,
        "confusion_matrix_labels": class_names + ["background"],
        "confusion_matrix": confusion,
        "vehicle_instance_misclassifications": dict(sorted(instance_errors.items())),
        "vehicle_instance_summary": {
            instance: {
                "ground_truth": instance_ground_truth[instance],
                "correct_class": instance_correct[instance],
                "missed": instance_missed[instance],
            }
            for instance in sorted(instance_ground_truth)
        },
        "error_count_by_condition": dict(sorted(condition_errors.items())),
        "error_count_by_encoding_profile": dict(sorted(encoding_profile_errors.items())),
        "error_count_by_video_bitrate_bps": dict(sorted(bitrate_errors.items())),
    }


# -----------------------------------------------------------------------------
# 기능: Validation은 threshold를 탐색하고 Test는 설정된 threshold 없이 선택하지 않는다.
#
# 동작 방법:
#   1. config, YOLOX source, dataset SHA와 checkpoint의 class·입력 크기를 교차 검증한다.
#   2. MPS 우선 device에 모델을 올리고 전체 split의 prediction을 한 번 수집한다.
#   3. COCO mAP50/mAP50-95와 class별 AP를 계산한다.
#   4. Validation에서는 설정 범위의 confidence를 순회해 recall·FP·FN·혼동을 비교한다.
#   5. 우선 class의 최소 recall 조건을 만족하는 threshold 중 false positive가 적은 값을
#      추천한다. Test에서는 Validation에서 고른 명시 threshold만 평가한다.
#
# 사용 이유/연결: 학습 checkpoint를 입력으로 받고 결과를 threshold 선택과 ONNX export에 전달한다.
#
# 입력:
#   config_path (Path) - 경로·class·threshold 등 실행 계약이 기록된 YAML config 경로.
#   dataset_version (str) - 평가할 불변 dataset_vNNN 이름.
#   checkpoint_path (Path) - 학습 weight와 재현 metadata가 포함된 checkpoint 경로.
#   split (str) - val 또는 test. 두 split의 threshold 선택 정책이 서로 다르다.
#   input_size (tuple[int, int]) - 모델에 넣을 (height, width) 크기.
#   input_source (str) - export JPEG와 원본 MP4 중 실제 추론에 사용할 입력 종류.
#   confidence_threshold (float | None) - Test에서 사용할 확정값. Validation에서는 생략 가능하다.
#
# 반환: 모델·데이터 계약, COCO 지표, threshold sweep과 추천값이 포함된 report를 반환한다.
# -----------------------------------------------------------------------------
def evaluate_checkpoint(
    config_path: Path,
    dataset_version: str,
    checkpoint_path: Path,
    split: str,
    input_size: tuple[int, int],
    input_source: str,
    confidence_threshold: float | None = None,
) -> dict[str, Any]:
    if split not in {"val", "test"}:
        raise DatasetContractError("평가 split은 val 또는 test여야 합니다.")
    settings = load_settings(config_path)
    source_dir, _ = validate_yolox_installation(config_path, settings)
    dataset_dir = resolve_dataset_dir(config_path, settings, dataset_version)
    class_names = [item["name"] for item in settings["classes"]]
    model_variant = str(settings["yolox"].get("model_variant", "nano"))
    model, checkpoint = load_trained_checkpoint(
        source_dir, checkpoint_path, len(class_names), model_variant
    )
    dataset_manifest = read_json(dataset_dir / "dataset_manifest.json")
    if checkpoint.get("dataset_version") != dataset_version or checkpoint.get("dataset_sha256") != dataset_manifest["dataset_sha256"]:
        raise DatasetContractError("checkpoint가 선택한 dataset version/SHA에서 학습된 것이 아닙니다.")
    if checkpoint.get("classes") != class_names or checkpoint.get("input_size") != [input_size[0], input_size[1]]:
        raise DatasetContractError("checkpoint의 클래스 순서 또는 입력 크기가 평가 설정과 다릅니다.")
    device = select_device()
    model.to(device)
    evaluation = settings["evaluation"]
    priority_names = evaluation.get("priority_classes", class_names[:2])
    if not isinstance(priority_names, list) or not priority_names:
        raise DatasetContractError("evaluation.priority_classes는 클래스 이름 배열이어야 합니다.")
    unknown_priority = sorted(set(priority_names) - set(class_names))
    if unknown_priority:
        raise DatasetContractError(f"알 수 없는 우선 평가 클래스: {unknown_priority}")
    priority_class_ids = {class_names.index(name) for name in priority_names}
    start = float(evaluation["confidence_sweep_start"])
    if split == "test" and confidence_threshold is None:
        raise DatasetContractError("Test 평가는 Validation에서 선택한 --confidence-threshold가 필요합니다.")
    minimum_inference_confidence = float(evaluation["coco_confidence_floor"])
    coco, coco_results, predictions, contexts = collect_predictions(
        model,
        device,
        dataset_dir,
        split,
        input_size,
        minimum_inference_confidence,
        float(evaluation["nms_iou_threshold"]),
        input_source,
    )
    thresholds = (
        np.arange(start, float(evaluation["confidence_sweep_end"]) + 1e-9, float(evaluation["confidence_sweep_step"]))
        if split == "val" and confidence_threshold is None
        else np.asarray([float(confidence_threshold)])
    )
    sweep = [
        _threshold_metrics(
            coco, predictions, float(value), float(evaluation["matching_iou_threshold"]),
            class_names, contexts, priority_class_ids,
        )
        for value in thresholds
    ]
    recommendation = None
    minimum_recall = evaluation.get("minimum_target_recall")
    if split == "val" and minimum_recall is not None:
        candidates = [item for item in sweep if item["target_recall"] >= float(minimum_recall)]
        if candidates:
            recommendation = min(candidates, key=lambda item: (item["target_false_positive_count"], -item["target_recall"], -item["threshold"]))["threshold"]
    return {
        "dataset_version": dataset_version,
        "dataset_sha256": dataset_manifest["dataset_sha256"],
        "checkpoint": str(checkpoint_path.resolve()),
        "checkpoint_epoch": checkpoint.get("epoch"),
        "split": split,
        "input_source": input_source,
        "input_size": [input_size[1], input_size[0]],
        "device": str(device),
        "coco": _coco_metrics(coco, coco_results, class_names),
        "confidence_sweep": sweep,
        "recommended_confidence_threshold": recommendation,
        "recommendation_policy": "validation target recall constraint 후 target false positive 최소화" if minimum_recall is not None else "minimum_target_recall 미설정으로 추천하지 않음",
    }


def parse_args() -> argparse.Namespace:
    """평가할 checkpoint, split, 입력 원본과 threshold 조건을 CLI에서 읽는다."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("config.yaml"))
    parser.add_argument("--dataset-version", required=True)
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--split", choices=("val", "test"), required=True)
    parser.add_argument("--input-source", choices=("jpeg", "mp4"), default="jpeg")
    parser.add_argument("--width", type=int, required=True)
    parser.add_argument("--height", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--confidence-threshold", type=float)
    return parser.parse_args()


def main() -> int:
    """checkpoint 평가를 실행하고 생성된 JSON report 경로를 출력한다."""
    args = parse_args()
    try:
        report = evaluate_checkpoint(
            args.config.resolve(), args.dataset_version, args.checkpoint.resolve(), args.split,
            (args.height, args.width), args.input_source, args.confidence_threshold,
        )
        atomic_write_json(args.output.resolve(), report)
    except (DatasetContractError, OSError, RuntimeError, ValueError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print(f"평가 완료: {args.output.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
