#!/usr/bin/env python3
"""학습된 YOLOX-Nano를 static FP32·NMS 미포함 ONNX와 manifest로 내보낸다."""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

import torch

from dataset_contracts import DatasetContractError, atomic_write_json, load_settings, read_json, sha256_file
from yolox_runtime import (
    expected_prediction_count,
    load_trained_checkpoint,
    resolve_dataset_dir,
    validate_yolox_installation,
)


def export_model(args: argparse.Namespace) -> Path:
    """실제 graph 출력 shape를 검증한 뒤 release에 필요한 계약 파일을 함께 기록한다."""
    config_path = args.config.resolve()
    settings = load_settings(config_path)
    source_dir, _ = validate_yolox_installation(config_path, settings)
    dataset_dir = resolve_dataset_dir(config_path, settings, args.dataset_version)
    class_names = [item["name"] for item in settings["classes"]]
    model_variant = str(settings["yolox"].get("model_variant", "nano"))
    model, checkpoint = load_trained_checkpoint(
        source_dir, args.checkpoint.resolve(), len(class_names), model_variant
    )
    dataset_manifest = read_json(dataset_dir / "dataset_manifest.json")
    if checkpoint.get("dataset_version") != args.dataset_version or checkpoint.get("dataset_sha256") != dataset_manifest["dataset_sha256"]:
        raise DatasetContractError("checkpoint와 export dataset version/SHA가 다릅니다.")
    if checkpoint.get("classes") != class_names:
        raise DatasetContractError("checkpoint 클래스 순서가 config.yaml과 다릅니다.")
    expected_size = checkpoint.get("input_size")
    if expected_size != [args.height, args.width]:
        raise DatasetContractError(f"checkpoint 입력 크기 {expected_size}와 export 크기가 다릅니다.")
    model.eval().cpu()
    model.head.decode_in_inference = False
    output_dir = args.output_dir.resolve()
    if output_dir.exists() and any(output_dir.iterdir()):
        raise DatasetContractError(f"비어 있지 않은 export 디렉터리는 덮어쓸 수 없습니다: {output_dir}")
    output_dir.mkdir(parents=True, exist_ok=True)
    onnx_path = output_dir / "vehicle_detector.onnx"
    sample = torch.zeros(1, 3, args.height, args.width, dtype=torch.float32)
    with torch.inference_mode():
        raw = model(sample)
    expected_shape = [1, expected_prediction_count((args.height, args.width)), 5 + len(class_names)]
    if list(raw.shape) != expected_shape:
        raise DatasetContractError(f"예상하지 못한 YOLOX raw shape: {list(raw.shape)} != {expected_shape}")
    torch.onnx.export(
        model,
        sample,
        onnx_path,
        input_names=["images"],
        output_names=["raw_predictions"],
        opset_version=int(settings["export"]["opset_version"]),
        dynamic_axes=None,
        do_constant_folding=True,
        dynamo=False,
    )
    import onnx

    onnx.checker.check_model(onnx.load(str(onnx_path)))
    evaluation = read_json(args.evaluation.resolve()) if args.evaluation else None
    explicit_threshold = getattr(args, "confidence_threshold", None)
    threshold = explicit_threshold
    if threshold is None and evaluation:
        threshold = evaluation.get("recommended_confidence_threshold")
    if evaluation and (
        evaluation.get("split") != "val"
        or evaluation.get("dataset_sha256") != dataset_manifest["dataset_sha256"]
        or evaluation.get("input_size") != [args.width, args.height]
    ):
        raise DatasetContractError("ONNX에는 같은 dataset/해상도의 Validation 평가만 연결할 수 있습니다.")
    if evaluation and threshold is None:
        raise DatasetContractError(
            "release manifest를 확정하려면 Validation 추천값 또는 --confidence-threshold가 필요합니다."
        )
    if threshold is not None and not 0.0 <= float(threshold) <= 1.0:
        raise DatasetContractError("confidence threshold는 0~1이어야 합니다.")
    manifest = {
        "model_name": f"vehicle_detector_yolox_{model_variant}",
        "version": 1,
        "architecture_source": "Megvii-BaseDetection/YOLOX 0.3.0",
        "architecture_commit": settings["yolox"]["expected_commit"],
        "input_shape": [1, 3, args.height, args.width],
        "input_dtype": "float32",
        "input_color": "BGR",
        "input_layout": "NCHW",
        "preprocessing": {
            "resize": "top_left_letterbox",
            "pad_value": 114,
            "normalization": "none; uint8 0..255 values cast to float32",
        },
        "output_tensor_name": "raw_predictions",
        "output_shape": expected_shape,
        "output_fields": ["tx", "ty", "tw", "th", "objectness", *[f"class_score_{index}" for index in range(len(class_names))]],
        "bbox_representation": "raw YOLOX grid offsets/log-size; decode required",
        "objectness_and_class_scores": "sigmoid probabilities",
        "decode": {"strides": [8, 16, 32], "grid_order": "stride 8 then 16 then 32; row-major", "decoded_bbox": "pixel cx,cy,w,h"},
        "nms_included": False,
        "confidence_threshold": float(threshold) if threshold is not None else None,
        "confidence_threshold_source": (
            "explicit_validation_review" if explicit_threshold is not None
            else "evaluation_recommendation"
        ),
        "nms_iou_threshold": float(settings["evaluation"]["nms_iou_threshold"]),
        "classes": class_names,
        "onnx_sha256": sha256_file(onnx_path),
        "dataset_version": args.dataset_version,
        "dataset_sha256": dataset_manifest["dataset_sha256"],
        "checkpoint_sha256": sha256_file(args.checkpoint.resolve()),
    }
    atomic_write_json(output_dir / "model_manifest.json", manifest)
    shutil.copy2(dataset_dir / "classes.json", output_dir / "classes.json")
    shutil.copy2(dataset_dir / "dataset_summary.json", output_dir / "dataset_summary.json")
    if evaluation:
        shutil.copy2(args.evaluation.resolve(), output_dir / "evaluation.json")
    (output_dir / "README.md").write_text(
        "# Vehicle detector release\n\n"
        f"이 디렉터리는 YOLOX-{model_variant.capitalize()} FP32 ONNX 후보와 재현 계약을 함께 보관합니다. "
        "전처리·raw output·decode·NMS·class 순서는 model_manifest.json을 기준으로 구현합니다. "
        "PyTorch↔ONNX 허용 오차가 확정되고 compare_outputs.py를 통과하기 전에는 최종 배포본으로 간주하지 않습니다.\n",
        encoding="utf-8",
    )
    return onnx_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("config.yaml"))
    parser.add_argument("--dataset-version", required=True)
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--width", type=int, required=True)
    parser.add_argument("--height", type=int, required=True)
    parser.add_argument("--evaluation", type=Path)
    parser.add_argument("--confidence-threshold", type=float)
    parser.add_argument("--output-dir", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    try:
        output = export_model(parse_args())
    except (DatasetContractError, OSError, RuntimeError, ValueError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print(f"ONNX export 완료: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
