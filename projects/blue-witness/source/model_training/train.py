#!/usr/bin/env python3
"""@file train.py
@brief 불변 COCO 데이터셋으로 YOLOX Nano/Tiny를 전이학습하고 재현 가능한 실험을 저장한다.

config·YOLOX source·pretrained checkpoint·dataset SHA를 검증한 뒤 MPS 또는 CPU device를
선택한다. 공식 YOLOX loss와 augmentation loader로 epoch를 수행하고 last/best
checkpoint, 학습 log와 experiment manifest를 새 실험 디렉터리에 기록한다. sanity
mode와 정식 학습이 같은 경로를 사용하므로 본 학습 전 pipeline을 빠르게 확인할 수 있다.
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import os
import platform
import random
import shutil
import subprocess
import sys
from collections import Counter
from pathlib import Path
from typing import Any

os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")

import numpy as np
import torch
import yaml

from dataset_contracts import DatasetContractError, atomic_write_json, load_settings, read_json, sha256_file
from evaluate import evaluate_checkpoint
from yolox_runtime import (
    build_yolox_model,
    import_yolox,
    load_pretrained_backbone,
    resolve_dataset_dir,
    select_device,
    validate_yolox_installation,
)


LOGGER = logging.getLogger("vehicle_training")


def configure_logging(log_path: Path) -> None:
    """console과 experiment training.log에 같은 학습 상태를 남긴다."""
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    LOGGER.setLevel(logging.INFO)
    LOGGER.handlers.clear()
    for handler in (logging.StreamHandler(), logging.FileHandler(log_path, encoding="utf-8")):
        handler.setFormatter(formatter)
        LOGGER.addHandler(handler)


def seed_everything(seed: int) -> None:
    """Python·NumPy·PyTorch 난수 seed를 맞춰 학습 재현성을 높인다."""
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)


# -----------------------------------------------------------------------------
# 기능: 공식 YOLOX와 같은 BN/no-decay, weight/decay, bias 그룹의 SGD를 구성한다.
#
# 동작 방법:
#   1. 공식 YOLOX와 같은 BN/no-decay, weight/decay, bias 그룹의 SGD를 구성한다
#   2. 입력 계약을 유지한 결과를 만들어 호출한 pipeline 단계에 반환한다.
#
# 사용 이유/연결: 검증된 COCO 버전을 입력으로 받아 평가·ONNX export용 checkpoint를 만든다.
#
# 입력:
#   model (torch.nn.Module) - 학습 또는 inference에 사용할 YOLOX model.
#   learning_rate (float) - 이 함수의 learning_rate 처리에 사용하는 값.
#   settings (dict[str, Any]) - 검증을 통과한 프로젝트 설정 객체.
#
# 반환: torch.optim.Optimizer 형식의 처리 결과를 반환하며 의미는 기능 설명과 같다.
# -----------------------------------------------------------------------------
def build_optimizer(model: torch.nn.Module, learning_rate: float, settings: dict[str, Any]) -> torch.optim.Optimizer:
    batch_norm_weights, decay_weights, biases = [], [], []
    for module_name, module in model.named_modules():
        del module_name
        if hasattr(module, "bias") and isinstance(module.bias, torch.nn.Parameter):
            biases.append(module.bias)
        if isinstance(module, torch.nn.BatchNorm2d):
            batch_norm_weights.append(module.weight)
        elif hasattr(module, "weight") and isinstance(module.weight, torch.nn.Parameter):
            decay_weights.append(module.weight)
    optimizer = torch.optim.SGD(
        batch_norm_weights,
        lr=learning_rate,
        momentum=float(settings["momentum"]),
        nesterov=True,
    )
    optimizer.add_param_group({"params": decay_weights, "weight_decay": float(settings["weight_decay"])})
    optimizer.add_param_group({"params": biases})
    return optimizer


# -----------------------------------------------------------------------------
# 기능: crop/Mosaic/MixUp 없이 공식 COCODataset과 TrainTransform만 연결한다.
#
# 동작 방법:
#   1. crop/Mosaic/MixUp 없이 공식 COCODataset과 TrainTransform만 연결한다
#   2. 입력 계약을 유지한 결과를 만들어 호출한 pipeline 단계에 반환한다.
#
# 사용 이유/연결: 검증된 COCO 버전을 입력으로 받아 평가·ONNX export용 checkpoint를 만든다.
#
# 입력:
#   source_dir (Path) - 검증된 공식 YOLOX source 디렉터리.
#   dataset_dir (Path) - 불변 COCO 데이터셋 버전의 루트 경로.
#   input_size (tuple[int, int]) - 이 함수의 input_size 처리에 사용하는 값.
#   training (dict[str, Any]) - 이 함수의 training 처리에 사용하는 값.
#   batch_size (int) - 이 함수의 batch_size 처리에 사용하는 값.
#   sanity_image_count (int | None) - 이 함수의 sanity_image_count 처리에 사용하는 값.
#
# 반환: torch.utils.data.DataLoader 형식의 처리 결과를 반환하며 의미는 기능 설명과 같다.
# -----------------------------------------------------------------------------
def create_training_loader(
    source_dir: Path,
    dataset_dir: Path,
    input_size: tuple[int, int],
    training: dict[str, Any],
    batch_size: int,
    sanity_image_count: int | None,
) -> torch.utils.data.DataLoader:
    import_yolox(source_dir)
    from yolox.data import COCODataset, TrainTransform

    if training.get("mosaic") or training.get("mixup"):
        raise DatasetContractError("첫 baseline은 mosaic=false, mixup=false여야 합니다.")
    dataset: torch.utils.data.Dataset = COCODataset(
        data_dir=str(dataset_dir),
        json_file="instances_train.json",
        name="images/train",
        img_size=input_size,
        preproc=TrainTransform(
            max_labels=50,
            flip_prob=float(training["horizontal_flip_probability"]),
            hsv_prob=float(training["hsv_probability"]),
        ),
        cache=False,
    )
    if sanity_image_count is not None:
        if not 20 <= sanity_image_count <= 50:
            raise DatasetContractError("overfit sanity image 수는 20~50이어야 합니다.")
        if len(dataset) < sanity_image_count:
            raise DatasetContractError(f"sanity에 {sanity_image_count}장을 요청했지만 train은 {len(dataset)}장입니다.")
        dataset = torch.utils.data.Subset(dataset, list(range(sanity_image_count)))
    return torch.utils.data.DataLoader(
        dataset,
        batch_size=batch_size,
        shuffle=True,
        num_workers=int(training["num_workers"]),
        drop_last=False,
    )


def _git_commit(project_dir: Path) -> str | None:
    """실험 manifest에 남길 Git commit을 읽고 저장소가 아니면 None을 반환한다."""
    result = subprocess.run(["git", "-C", str(project_dir), "rev-parse", "HEAD"], capture_output=True, text=True)
    return result.stdout.strip() if result.returncode == 0 else None


# -----------------------------------------------------------------------------
# 기능: 새 experiment를 생성하고 last/best checkpoint와 재현 정보를 함께 저장한다.
#
# 동작 방법:
#   1. config와 YOLOX source/checkpoint SHA, dataset version·SHA와 class 순서를 검증한다.
#   2. random seed를 고정하고 MPS 우선 device, Nano/Tiny 구조와 SGD optimizer를 준비한다.
#   3. 공식 COCODataset loader로 train batch를 만들고 pretrained backbone weight를 적용한다.
#   4. epoch마다 forward, YOLOX loss backward, optimizer update와 learning-rate schedule을 수행한다.
#   5. 평균 loss가 가장 낮은 weight를 best.pt로, 매 epoch 최신 상태를 last.pt로 저장한다.
#   6. 실행 인자, 환경, Git commit, dataset SHA와 checkpoint SHA를 experiment manifest에 기록한다.
#
# 사용 이유/연결: 검증된 COCO 버전을 입력으로 받아 평가·ONNX export용 checkpoint를 만든다.
#
# 입력:
#   args (argparse.Namespace) - CLI에서 해석한 실행 인자 묶음.
#
# 반환: checkpoint와 log, 재현 manifest가 저장된 새 experiment 디렉터리 Path를 반환한다.
# -----------------------------------------------------------------------------
def train_model(args: argparse.Namespace) -> Path:
    config_path = args.config.resolve()
    settings = load_settings(config_path)
    source_dir, pretrained_checkpoint = validate_yolox_installation(config_path, settings)
    dataset_dir = resolve_dataset_dir(config_path, settings, args.dataset_version)
    training = settings["training"]
    epochs = args.epochs if args.epochs is not None else training.get("epochs")
    batch_size = args.batch_size if args.batch_size is not None else training.get("batch_size")
    if epochs is None or batch_size is None:
        raise DatasetContractError("학습 epochs와 batch_size는 config 또는 CLI에서 명시해야 합니다.")
    epochs, batch_size = int(epochs), int(batch_size)
    if epochs <= 0 or batch_size <= 0:
        raise DatasetContractError("epochs와 batch_size는 양수여야 합니다.")
    input_size = (args.height, args.width)
    allowed = {(int(item["height"]), int(item["width"])) for item in settings["resolution_experiments"]}
    if input_size not in allowed:
        raise DatasetContractError(f"설계에 없는 입력 크기입니다: {args.width}x{args.height}")

    runs_dir = config_path.parent / "runs"
    experiment_dir = runs_dir / args.experiment_name
    if experiment_dir.exists():
        raise DatasetContractError(f"기존 experiment는 덮어쓸 수 없습니다: {experiment_dir}")
    for name in ("checkpoints", "metrics", "predictions", "exports"):
        (experiment_dir / name).mkdir(parents=True, exist_ok=True)
    configure_logging(experiment_dir / "training.log")
    seed = int(training["random_seed"])
    seed_everything(seed)
    device = select_device()
    class_names = [item["name"] for item in settings["classes"]]
    model_variant = str(settings["yolox"].get("model_variant", "nano"))
    model = build_yolox_model(source_dir, len(class_names), model_variant)
    transfer = load_pretrained_backbone(model, pretrained_checkpoint)
    model.to(device).train()
    learning_rate = float(training["learning_rate_per_image"]) * batch_size
    optimizer = build_optimizer(model, learning_rate, training)
    loader = create_training_loader(
        source_dir, dataset_dir, input_size, training, batch_size, args.sanity_image_count
    )
    if not loader.dataset:
        raise DatasetContractError("train split이 비어 있습니다.")

    resolved = {
        "experiment_name": args.experiment_name,
        "dataset_version": args.dataset_version,
        "dataset_sha256": read_json(dataset_dir / "dataset_manifest.json")["dataset_sha256"],
        "input_width": args.width,
        "input_height": args.height,
        "epochs": epochs,
        "batch_size": batch_size,
        "sanity_image_count": args.sanity_image_count,
        "device": str(device),
        "class_order": class_names,
        "model_variant": model_variant,
        "training": training,
        "pretrained_checkpoint_sha256": sha256_file(pretrained_checkpoint),
        "yolox_commit": settings["yolox"]["expected_commit"],
    }
    (experiment_dir / "resolved_config.yaml").write_text(yaml.safe_dump(resolved, sort_keys=False), encoding="utf-8")
    shutil.copy2(dataset_dir / "dataset_manifest.json", experiment_dir / "dataset_manifest.json")
    atomic_write_json(
        experiment_dir / "environment.json",
        {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "torch": torch.__version__,
            "device": str(device),
            "mps_built": torch.backends.mps.is_built(),
            "mps_available": torch.backends.mps.is_available(),
            "project_git_commit": _git_commit(config_path.parent),
            "transfer": transfer,
        },
    )

    best_metric = math.inf if args.sanity_image_count is not None else -math.inf
    freeze_epochs = int(training["freeze_backbone_epochs"])
    metrics_path = experiment_dir / "metrics" / "training.jsonl"
    for epoch in range(1, epochs + 1):
        # 첫 epoch의 optimizer step부터 warmup learning rate를 적용한다.
        warmup_epochs = int(training["warmup_epochs"])
        if epoch <= warmup_epochs and warmup_epochs > 0:
            factor = epoch / warmup_epochs
        else:
            progress = (epoch - warmup_epochs) / max(epochs - warmup_epochs, 1)
            factor = 0.05 + 0.95 * 0.5 * (1.0 + math.cos(math.pi * progress))
        for group in optimizer.param_groups:
            group["lr"] = learning_rate * factor
        backbone_frozen = epoch <= freeze_epochs
        for parameter in model.backbone.parameters():
            parameter.requires_grad = not backbone_frozen
        model.train()
        epoch_losses = Counter()
        batch_count = 0
        for images, targets, _, _ in loader:
            images = images.to(device=device, dtype=torch.float32)
            targets = targets.to(device=device, dtype=torch.float32)
            optimizer.zero_grad(set_to_none=True)
            outputs = model(images, targets)
            outputs["total_loss"].backward()
            optimizer.step()
            for name in ("total_loss", "iou_loss", "conf_loss", "cls_loss", "l1_loss"):
                value = outputs[name]
                epoch_losses[name] += float(value.detach().cpu()) if torch.is_tensor(value) else float(value)
            batch_count += 1
        record = {"epoch": epoch, **{name: value / batch_count for name, value in epoch_losses.items()}, "learning_rate": optimizer.param_groups[0]["lr"]}
        checkpoint_state = {
            "model": model.state_dict(), "optimizer": optimizer.state_dict(), "epoch": epoch,
            "dataset_version": args.dataset_version, "dataset_sha256": resolved["dataset_sha256"],
            "input_size": [args.height, args.width], "classes": class_names,
            "model_variant": model_variant,
        }
        last_path = experiment_dir / "checkpoints" / "last.pth"
        torch.save(checkpoint_state, last_path)
        if args.sanity_image_count is not None:
            selection_metric = record["total_loss"]
            is_best = selection_metric < best_metric
        else:
            validation = evaluate_checkpoint(config_path, args.dataset_version, last_path, "val", input_size, "jpeg")
            selection_metric = validation["coco"]["map50_95"]
            record["validation_map50_95"] = selection_metric
            atomic_write_json(experiment_dir / "metrics" / f"validation_epoch_{epoch:03d}.json", validation)
            is_best = selection_metric > best_metric
        if is_best:
            best_metric = selection_metric
            shutil.copy2(last_path, experiment_dir / "checkpoints" / "best.pth")
        with metrics_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
        LOGGER.info("epoch=%d total_loss=%.5f selection_metric=%.5f best=%.5f", epoch, record["total_loss"], selection_metric, best_metric)
    return experiment_dir


def parse_args() -> argparse.Namespace:
    """학습 데이터셋, epoch, batch size와 실행 mode를 CLI에서 읽는다."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("config.yaml"))
    parser.add_argument("--dataset-version", required=True)
    parser.add_argument("--experiment-name", required=True)
    parser.add_argument("--width", type=int, required=True)
    parser.add_argument("--height", type=int, required=True)
    parser.add_argument("--epochs", type=int)
    parser.add_argument("--batch-size", type=int)
    parser.add_argument("--sanity-image-count", type=int)
    return parser.parse_args()


def main() -> int:
    """학습을 실행하고 설정·데이터·device 오류를 CLI 실패로 보고한다."""
    try:
        output = train_model(parse_args())
    except (DatasetContractError, OSError, RuntimeError, ValueError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print(f"학습 완료: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
