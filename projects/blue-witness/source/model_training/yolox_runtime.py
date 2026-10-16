#!/usr/bin/env python3
"""@file yolox_runtime.py
@brief 학습·평가·ONNX 비교가 공유하는 YOLOX 모델 로딩, 전처리, decode와 NMS를 제공한다.

공식 YOLOX source commit과 checkpoint SHA를 확인하고 Nano/Tiny 모델을 구성한다.
전이학습용 backbone과 프로젝트 checkpoint 로딩을 구분하며, BGR top-left letterbox,
raw grid decode, confidence filtering과 class별 NMS를 한 구현으로 통일한다. 이를 통해
Python 학습·평가와 Raspberry Pi 배포 계약 사이의 전처리·후처리 불일치를 줄인다.
"""

from __future__ import annotations

import importlib
import subprocess
import sys
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import torch

from dataset_contracts import (
    DATASET_VERSION_PATTERN,
    DatasetContractError,
    read_json,
    resolve_project_path,
    sha256_file,
)


# -----------------------------------------------------------------------------
# 기능: 공식 소스 commit, 호환 패치, pretrained SHA가 설정 계약과 같은지 확인한다.
#
# 동작 방법:
#   1. 필수 field, type, 값 범위와 상호 관계를 순서대로 검사한다.
#   2. 계약이 맞으면 후속 처리를 허용하고 위반 시 구체적인 예외로 중단한다.
#
# 사용 이유/연결: 학습·평가·제안·ONNX 비교가 같은 모델 계약을 쓰도록 연결한다.
#
# 입력:
#   config_path (Path) - 경로·class·threshold 등 실행 계약이 기록된 YAML config 경로.
#   settings (dict[str, Any]) - 검증을 통과한 프로젝트 설정 객체.
#
# 반환: tuple[Path, Path] 형식의 처리 결과를 반환하며 의미는 기능 설명과 같다.
# -----------------------------------------------------------------------------
def validate_yolox_installation(config_path: Path, settings: dict[str, Any]) -> tuple[Path, Path]:
    yolox = settings.get("yolox")
    if not isinstance(yolox, dict):
        raise DatasetContractError("config.yaml에 yolox 설정이 필요합니다.")
    for section in ("training", "evaluation", "export"):
        if not isinstance(settings.get(section), dict):
            raise DatasetContractError(f"config.yaml에 {section} 설정 객체가 필요합니다.")
    resolutions = settings.get("resolution_experiments")
    if not isinstance(resolutions, list) or not resolutions:
        raise DatasetContractError("resolution_experiments가 비어 있습니다.")
    for item in resolutions:
        width, height = int(item.get("width", 0)), int(item.get("height", 0))
        if width <= 0 or height <= 0 or width % 32 or height % 32:
            raise DatasetContractError("YOLOX 입력 width/height는 양수이며 32의 배수여야 합니다.")
    if bool(settings["training"].get("mixed_precision")):
        raise DatasetContractError("첫 MPS baseline은 mixed_precision=false여야 합니다.")
    model_variant = str(yolox.get("model_variant", "nano"))
    if model_variant not in {"nano", "tiny"}:
        raise DatasetContractError("yolox.model_variant는 nano 또는 tiny여야 합니다.")
    source_dir = resolve_project_path(config_path, str(yolox.get("source_dir", "")))
    checkpoint = resolve_project_path(config_path, str(yolox.get("pretrained_checkpoint", "")))
    if not (source_dir / "yolox" / "models").is_dir():
        raise DatasetContractError(f"공식 YOLOX 소스를 찾을 수 없습니다: {source_dir}")
    source_commit_path = source_dir / "SOURCE_COMMIT"
    if source_commit_path.is_file():
        commit = source_commit_path.read_text(encoding="utf-8").strip()
    else:
        try:
            commit = subprocess.run(
                ["git", "-C", str(source_dir), "rev-parse", "HEAD"],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
        except (FileNotFoundError, subprocess.CalledProcessError) as error:
            raise DatasetContractError(
                f"YOLOX source commit을 확인할 수 없습니다: {source_dir}"
            ) from error
    if commit != yolox.get("expected_commit"):
        raise DatasetContractError(f"YOLOX commit 불일치: {commit}")
    patched_file_sha256 = yolox.get("patched_file_sha256")
    if not isinstance(patched_file_sha256, dict) or not patched_file_sha256:
        raise DatasetContractError("yolox.patched_file_sha256 설정이 필요합니다.")
    resolved_source_dir = source_dir.resolve()
    for relative_path, expected_sha in patched_file_sha256.items():
        if not isinstance(relative_path, str) or not isinstance(expected_sha, str):
            raise DatasetContractError("YOLOX 호환 패치 경로와 SHA는 문자열이어야 합니다.")
        patched_file = (resolved_source_dir / relative_path).resolve()
        if not patched_file.is_relative_to(resolved_source_dir):
            raise DatasetContractError(f"YOLOX 외부의 패치 경로는 허용하지 않습니다: {relative_path}")
        if not patched_file.is_file():
            raise DatasetContractError(f"YOLOX 호환 패치 파일이 없습니다: {patched_file}")
        actual_sha = sha256_file(patched_file)
        if actual_sha != expected_sha:
            raise DatasetContractError(
                f"YOLOX 호환 패치 SHA 불일치: {relative_path} ({actual_sha})"
            )
    if not checkpoint.is_file():
        raise DatasetContractError(f"YOLOX-{model_variant} checkpoint가 없습니다: {checkpoint}")
    checkpoint_sha = sha256_file(checkpoint)
    if checkpoint_sha != yolox.get("pretrained_sha256"):
        raise DatasetContractError(f"pretrained checkpoint SHA 불일치: {checkpoint_sha}")
    return source_dir, checkpoint


# -----------------------------------------------------------------------------
# 기능: 설치 과정에서 native CUDA extension을 빌드하지 않고 검증된 소스를 import한다.
#
# 동작 방법:
#   1. 설치 과정에서 native CUDA extension을 빌드하지 않고 검증된 소스를 import한다
#   2. 입력 계약을 유지한 결과를 만들어 호출한 pipeline 단계에 반환한다.
#
# 사용 이유/연결: 학습·평가·제안·ONNX 비교가 같은 모델 계약을 쓰도록 연결한다.
#
# 입력:
#   source_dir (Path) - 검증된 공식 YOLOX source 디렉터리.
#
# 반환: 반환값은 없으며 검증·저장 또는 객체 상태 변경을 완료한다.
# -----------------------------------------------------------------------------
def import_yolox(source_dir: Path) -> None:
    source = str(source_dir)
    if source not in sys.path:
        sys.path.insert(0, source)
    importlib.import_module("yolox")


# -----------------------------------------------------------------------------
# 기능: 설정된 공식 YOLOX Nano/Tiny experiment로 지정 클래스 수의 모델을 생성한다.
#
# 동작 방법:
#   1. 설정된 공식 YOLOX Nano/Tiny experiment로 지정 클래스 수의 모델을 생성한다
#   2. 입력 계약을 유지한 결과를 만들어 호출한 pipeline 단계에 반환한다.
#
# 사용 이유/연결: 학습·평가·제안·ONNX 비교가 같은 모델 계약을 쓰도록 연결한다.
#
# 입력:
#   source_dir (Path) - 검증된 공식 YOLOX source 디렉터리.
#   num_classes (int) - 이 함수의 num_classes 처리에 사용하는 값.
#   model_variant (str) - 이 함수의 model_variant 처리에 사용하는 값.
#
# 반환: torch.nn.Module 형식의 처리 결과를 반환하며 의미는 기능 설명과 같다.
# -----------------------------------------------------------------------------
def build_yolox_model(
    source_dir: Path, num_classes: int, model_variant: str
) -> torch.nn.Module:
    if model_variant not in {"nano", "tiny"}:
        raise DatasetContractError(f"지원하지 않는 YOLOX 모델입니다: {model_variant}")
    import_yolox(source_dir)
    from yolox.exp import get_exp

    experiment = get_exp(
        str(source_dir / "exps" / "default" / f"yolox_{model_variant}.py"), None
    )
    experiment.num_classes = num_classes
    return experiment.get_model()


def build_yolox_nano(source_dir: Path, num_classes: int) -> torch.nn.Module:
    """기존 호출과의 호환을 위해 Nano 모델을 생성한다."""
    return build_yolox_model(source_dir, num_classes, "nano")


# -----------------------------------------------------------------------------
# 기능: 80-class head의 shape 불일치만 제외하고 호환 tensor를 전이한다.
#
# 동작 방법:
#   1. 입력 경로 또는 외부 source에서 필요한 정보를 읽는다.
#   2. 읽은 값을 후속 단계가 사용하는 내부 계약 형태로 검증·변환한다.
#
# 사용 이유/연결: 학습·평가·제안·ONNX 비교가 같은 모델 계약을 쓰도록 연결한다.
#
# 입력:
#   model (torch.nn.Module) - 학습 또는 inference에 사용할 YOLOX model.
#   checkpoint_path (Path) - 학습 weight와 재현 metadata가 포함된 checkpoint 경로.
#
# 반환: dict[str, int] 형식의 처리 결과를 반환하며 의미는 기능 설명과 같다.
# -----------------------------------------------------------------------------
def load_pretrained_backbone(model: torch.nn.Module, checkpoint_path: Path) -> dict[str, int]:
    checkpoint = torch.load(checkpoint_path, map_location="cpu", weights_only=True)
    source_state = checkpoint["model"] if "model" in checkpoint else checkpoint
    model_state = model.state_dict()
    compatible = {
        name: value
        for name, value in source_state.items()
        if name in model_state and model_state[name].shape == value.shape
    }
    model.load_state_dict(compatible, strict=False)
    return {"loaded_tensor_count": len(compatible), "skipped_tensor_count": len(source_state) - len(compatible)}


# -----------------------------------------------------------------------------
# 기능: 공식 checkpoint를 원래 클래스 수의 모델에 빠짐없이 불러온다.
#
#   전이학습과 달리 bbox 제안 모델은 COCO 80-class head까지 그대로 사용해야 하므로
#   tensor 누락이나 shape 불일치를 허용하지 않는다.
#
# 동작 방법:
#   1. 입력 경로 또는 외부 source에서 필요한 정보를 읽는다.
#   2. 읽은 값을 후속 단계가 사용하는 내부 계약 형태로 검증·변환한다.
#
# 사용 이유/연결: 학습·평가·제안·ONNX 비교가 같은 모델 계약을 쓰도록 연결한다.
#
# 입력:
#   model (torch.nn.Module) - 학습 또는 inference에 사용할 YOLOX model.
#   checkpoint_path (Path) - 학습 weight와 재현 metadata가 포함된 checkpoint 경로.
#
# 반환: 반환값은 없으며 검증·저장 또는 객체 상태 변경을 완료한다.
# -----------------------------------------------------------------------------
def load_official_checkpoint(model: torch.nn.Module, checkpoint_path: Path) -> None:
    checkpoint = torch.load(checkpoint_path, map_location="cpu", weights_only=True)
    source_state = checkpoint["model"] if isinstance(checkpoint, dict) and "model" in checkpoint else checkpoint
    if not isinstance(source_state, dict):
        raise DatasetContractError(f"공식 YOLOX checkpoint 형식이 아닙니다: {checkpoint_path}")
    try:
        model.load_state_dict(source_state, strict=True)
    except RuntimeError as error:
        raise DatasetContractError(
            f"공식 YOLOX checkpoint가 80-class Nano 모델과 일치하지 않습니다: {checkpoint_path}"
        ) from error


# -----------------------------------------------------------------------------
# 기능: 프로젝트 학습 checkpoint를 strict하게 읽어 클래스 head까지 검증한다.
#
# 동작 방법:
#   1. 입력 경로 또는 외부 source에서 필요한 정보를 읽는다.
#   2. 읽은 값을 후속 단계가 사용하는 내부 계약 형태로 검증·변환한다.
#
# 사용 이유/연결: 학습·평가·제안·ONNX 비교가 같은 모델 계약을 쓰도록 연결한다.
#
# 입력:
#   source_dir (Path) - 검증된 공식 YOLOX source 디렉터리.
#   checkpoint_path (Path) - 학습 weight와 재현 metadata가 포함된 checkpoint 경로.
#   num_classes (int) - 이 함수의 num_classes 처리에 사용하는 값.
#   model_variant (str) - 이 함수의 model_variant 처리에 사용하는 값.
#
# 반환: tuple[torch.nn.Module, dict[str, Any]] 형식의 처리 결과를 반환하며 의미는 기능 설명과 같다.
# -----------------------------------------------------------------------------
def load_trained_checkpoint(
    source_dir: Path,
    checkpoint_path: Path,
    num_classes: int,
    model_variant: str = "nano",
) -> tuple[torch.nn.Module, dict[str, Any]]:
    model = build_yolox_model(source_dir, num_classes, model_variant)
    checkpoint = torch.load(checkpoint_path, map_location="cpu", weights_only=True)
    if not isinstance(checkpoint, dict) or "model" not in checkpoint:
        raise DatasetContractError(f"프로젝트 checkpoint 형식이 아닙니다: {checkpoint_path}")
    if checkpoint.get("model_variant", "nano") != model_variant:
        raise DatasetContractError("checkpoint의 YOLOX 모델 종류가 config와 다릅니다.")
    model.load_state_dict(checkpoint["model"], strict=True)
    return model, checkpoint


def select_device() -> torch.device:
    """요구된 우선순위에 따라 MPS를 선택하고 사용할 수 없으면 CPU로 내린다."""
    if torch.backends.mps.is_built() and torch.backends.mps.is_available():
        return torch.device("mps")
    return torch.device("cpu")


# -----------------------------------------------------------------------------
# 기능: 공식 YOLOX 0.3.0과 동일하게 BGR·무정규화·top-left letterbox를 적용한다.
#
# 동작 방법:
#   1. 공식 YOLOX 0.3.0과 동일하게 BGR·무정규화·top-left letterbox를 적용한다
#   2. 입력 계약을 유지한 결과를 만들어 호출한 pipeline 단계에 반환한다.
#
# 사용 이유/연결: 학습·평가·제안·ONNX 비교가 같은 모델 계약을 쓰도록 연결한다.
#
# 입력:
#   frame (np.ndarray) - OpenCV가 BGR 순서로 decode한 영상 frame.
#   input_size (tuple[int, int]) - 이 함수의 input_size 처리에 사용하는 값.
#
# 반환: tuple[np.ndarray, float] 형식의 처리 결과를 반환하며 의미는 기능 설명과 같다.
# -----------------------------------------------------------------------------
def preprocess_frame(frame: np.ndarray, input_size: tuple[int, int]) -> tuple[np.ndarray, float]:
    input_height, input_width = input_size
    scale = min(input_height / frame.shape[0], input_width / frame.shape[1])
    resized = cv2.resize(
        frame,
        (int(frame.shape[1] * scale), int(frame.shape[0] * scale)),
        interpolation=cv2.INTER_LINEAR,
    )
    padded = np.full((input_height, input_width, 3), 114, dtype=np.uint8)
    padded[: resized.shape[0], : resized.shape[1]] = resized
    tensor = np.ascontiguousarray(padded.transpose(2, 0, 1), dtype=np.float32)
    return tensor, scale


# -----------------------------------------------------------------------------
# 기능: ONNX raw bbox logits에 stride별 grid를 적용해 pixel 단위 cxcywh로 변환한다.
#
# 동작 방법:
#   1. 모델 입력 또는 prediction tensor를 설정된 계약에 맞게 처리한다.
#   2. confidence와 좌표 정보를 검증 가능한 detection 목록으로 변환한다.
#
# 사용 이유/연결: 학습·평가·제안·ONNX 비교가 같은 모델 계약을 쓰도록 연결한다.
#
# 입력:
#   raw (np.ndarray) - 이 함수의 raw 처리에 사용하는 값.
#   input_size (tuple[int, int]) - 이 함수의 input_size 처리에 사용하는 값.
#
# 반환: np.ndarray 형식의 처리 결과를 반환하며 의미는 기능 설명과 같다.
# -----------------------------------------------------------------------------
def decode_raw_predictions(raw: np.ndarray, input_size: tuple[int, int]) -> np.ndarray:
    input_height, input_width = input_size
    grids: list[np.ndarray] = []
    strides: list[np.ndarray] = []
    for stride in (8, 16, 32):
        height, width = input_height // stride, input_width // stride
        y_grid, x_grid = np.meshgrid(np.arange(height), np.arange(width), indexing="ij")
        grid = np.stack((x_grid, y_grid), axis=2).reshape(1, -1, 2)
        grids.append(grid)
        strides.append(np.full((1, height * width, 1), stride, dtype=np.float32))
    grid = np.concatenate(grids, axis=1).astype(np.float32)
    stride_values = np.concatenate(strides, axis=1)
    decoded = raw.copy()
    decoded[..., :2] = (decoded[..., :2] + grid) * stride_values
    decoded[..., 2:4] = np.exp(decoded[..., 2:4]) * stride_values
    return decoded


# -----------------------------------------------------------------------------
# 기능: objectness×class score로 필터링하고 class별 NMS 후 원본 좌표를 반환한다.
#
# 동작 방법:
#   1. 모델 입력 또는 prediction tensor를 설정된 계약에 맞게 처리한다.
#   2. confidence와 좌표 정보를 검증 가능한 detection 목록으로 변환한다.
#
# 사용 이유/연결: 학습·평가·제안·ONNX 비교가 같은 모델 계약을 쓰도록 연결한다.
#
# 입력:
#   decoded (np.ndarray) - 이 함수의 decoded 처리에 사용하는 값.
#   scale (float) - 이 함수의 scale 처리에 사용하는 값.
#   confidence_threshold (float) - 이 함수의 confidence_threshold 처리에 사용하는 값.
#   nms_iou_threshold (float) - 이 함수의 nms_iou_threshold 처리에 사용하는 값.
#
# 반환: list[dict[str, Any]] 형식의 처리 결과를 반환하며 의미는 기능 설명과 같다.
# -----------------------------------------------------------------------------
def filter_and_nms(
    decoded: np.ndarray,
    scale: float,
    confidence_threshold: float,
    nms_iou_threshold: float,
) -> list[dict[str, Any]]:
    rows = decoded[0]
    class_ids = np.argmax(rows[:, 5:], axis=1)
    class_scores = rows[np.arange(len(rows)), class_ids + 5]
    scores = rows[:, 4] * class_scores
    candidates = np.where(scores >= confidence_threshold)[0]
    detections: list[dict[str, Any]] = []
    for class_id in sorted(set(class_ids[candidates].tolist())):
        indexes = candidates[class_ids[candidates] == class_id]
        boxes: list[list[float]] = []
        for index in indexes:
            center_x, center_y, width, height = rows[index, :4]
            boxes.append([
                float((center_x - width / 2) / scale),
                float((center_y - height / 2) / scale),
                float(width / scale),
                float(height / scale),
            ])
        kept = cv2.dnn.NMSBoxes(boxes, scores[indexes].tolist(), confidence_threshold, nms_iou_threshold)
        for local_index in np.asarray(kept).reshape(-1).tolist() if len(kept) else []:
            detections.append(
                {"bbox": boxes[local_index], "score": float(scores[indexes[local_index]]), "class_id": int(class_id)}
            )
    return sorted(detections, key=lambda item: item["score"], reverse=True)


# -----------------------------------------------------------------------------
# 기능: 명시된 불변 dataset version을 찾고 manifest 이름을 교차 확인한다.
#
# 동작 방법:
#   1. 명시된 불변 dataset version을 찾고 manifest 이름을 교차 확인한다
#   2. 입력 계약을 유지한 결과를 만들어 호출한 pipeline 단계에 반환한다.
#
# 사용 이유/연결: 학습·평가·제안·ONNX 비교가 같은 모델 계약을 쓰도록 연결한다.
#
# 입력:
#   config_path (Path) - 경로·class·threshold 등 실행 계약이 기록된 YAML config 경로.
#   settings (dict[str, Any]) - 검증을 통과한 프로젝트 설정 객체.
#   version (str) - 이 함수의 version 처리에 사용하는 값.
#
# 반환: Path 형식의 처리 결과를 반환하며 의미는 기능 설명과 같다.
# -----------------------------------------------------------------------------
def resolve_dataset_dir(config_path: Path, settings: dict[str, Any], version: str) -> Path:
    if DATASET_VERSION_PATTERN.fullmatch(version) is None:
        raise DatasetContractError("--dataset-version은 dataset_v001 형식이어야 합니다.")
    dataset_dir = resolve_project_path(config_path, settings["paths"]["datasets_dir"]) / version
    manifest_path = dataset_dir / "dataset_manifest.json"
    if not manifest_path.is_file():
        raise DatasetContractError(f"dataset manifest가 없습니다: {manifest_path}")
    if read_json(manifest_path).get("dataset_version") != version:
        raise DatasetContractError("dataset version과 manifest가 일치하지 않습니다.")
    return dataset_dir


def expected_prediction_count(input_size: tuple[int, int]) -> int:
    """stride 8·16·32 feature map에서 생성되는 전체 YOLOX 후보 수를 계산한다."""
    return sum((input_size[0] // stride) * (input_size[1] // stride) for stride in (8, 16, 32))
