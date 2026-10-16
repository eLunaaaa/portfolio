#!/usr/bin/env python3
"""동일 입력에서 PyTorch와 ONNXRuntime raw·후처리 출력을 비교한다."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import cv2
import numpy as np
import onnxruntime
import torch

from dataset_contracts import DatasetContractError, atomic_write_json, load_settings
from yolox_runtime import (
    decode_raw_predictions,
    filter_and_nms,
    load_trained_checkpoint,
    preprocess_frame,
    validate_yolox_installation,
)

onnxruntime.disable_telemetry_events()


def compare(args: argparse.Namespace) -> dict[str, object]:
    """raw tensor 수치 오차와 NMS 이후 detection 차이를 별도로 기록한다."""
    config_path = args.config.resolve()
    settings = load_settings(config_path)
    source_dir, _ = validate_yolox_installation(config_path, settings)
    class_count = len(settings["classes"])
    model_variant = str(settings["yolox"].get("model_variant", "nano"))
    model, checkpoint = load_trained_checkpoint(
        source_dir, args.checkpoint.resolve(), class_count, model_variant
    )
    if checkpoint.get("classes") != [item["name"] for item in settings["classes"]]:
        raise DatasetContractError("checkpoint 클래스 순서가 config.yaml과 다릅니다.")
    if checkpoint.get("input_size") != [args.height, args.width]:
        raise DatasetContractError("checkpoint와 비교 입력 크기가 다릅니다.")
    frame = cv2.imread(str(args.image.resolve()))
    if frame is None:
        raise DatasetContractError(f"비교 이미지를 읽을 수 없습니다: {args.image}")
    prepared, scale = preprocess_frame(frame, (args.height, args.width))
    batch = prepared[np.newaxis]
    model.eval().cpu()
    model.head.decode_in_inference = False
    with torch.inference_mode():
        pytorch_raw = model(torch.from_numpy(batch)).numpy()
    session = onnxruntime.InferenceSession(str(args.onnx.resolve()), providers=["CPUExecutionProvider"])
    onnx_raw = session.run(None, {session.get_inputs()[0].name: batch})[0]
    if pytorch_raw.shape != onnx_raw.shape:
        raise DatasetContractError(f"PyTorch/ONNX shape 불일치: {pytorch_raw.shape}, {onnx_raw.shape}")
    absolute = np.abs(pytorch_raw - onnx_raw)
    pytorch_detections = filter_and_nms(
        decode_raw_predictions(pytorch_raw, (args.height, args.width)), scale,
        args.confidence_threshold, float(settings["evaluation"]["nms_iou_threshold"]),
    )
    onnx_detections = filter_and_nms(
        decode_raw_predictions(onnx_raw, (args.height, args.width)), scale,
        args.confidence_threshold, float(settings["evaluation"]["nms_iou_threshold"]),
    )
    absolute_tolerance = settings["export"].get("absolute_tolerance")
    relative_tolerance = settings["export"].get("relative_tolerance")
    tolerance_passed = None
    if absolute_tolerance is not None and relative_tolerance is not None:
        tolerance_passed = bool(np.allclose(pytorch_raw, onnx_raw, atol=float(absolute_tolerance), rtol=float(relative_tolerance)))
    paired_count = min(len(pytorch_detections), len(onnx_detections))
    class_matches = [
        pytorch_detections[index]["class_id"] == onnx_detections[index]["class_id"]
        for index in range(paired_count)
    ]
    confidence_errors = [
        abs(pytorch_detections[index]["score"] - onnx_detections[index]["score"])
        for index in range(paired_count)
    ]
    bbox_errors = [
        max(
            abs(left - right)
            for left, right in zip(
                pytorch_detections[index]["bbox"], onnx_detections[index]["bbox"]
            )
        )
        for index in range(paired_count)
    ]
    return {
        "raw_shape": list(pytorch_raw.shape),
        "raw_max_absolute_error": float(absolute.max(initial=0.0)),
        "raw_mean_absolute_error": float(absolute.mean()),
        "configured_absolute_tolerance": absolute_tolerance,
        "configured_relative_tolerance": relative_tolerance,
        "tolerance_passed": tolerance_passed,
        "pytorch_detection_count": len(pytorch_detections),
        "onnx_detection_count": len(onnx_detections),
        "paired_class_ids_all_equal": all(class_matches),
        "paired_max_confidence_error": max(confidence_errors, default=0.0),
        "paired_max_bbox_coordinate_error": max(bbox_errors, default=0.0),
        "pytorch_detections": pytorch_detections,
        "onnx_detections": onnx_detections,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("config.yaml"))
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--onnx", type=Path, required=True)
    parser.add_argument("--image", type=Path, required=True)
    parser.add_argument("--width", type=int, required=True)
    parser.add_argument("--height", type=int, required=True)
    parser.add_argument("--confidence-threshold", type=float, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        report = compare(args)
        atomic_write_json(args.output.resolve(), report)
    except (DatasetContractError, OSError, RuntimeError, ValueError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print(f"출력 비교 완료: {args.output.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
