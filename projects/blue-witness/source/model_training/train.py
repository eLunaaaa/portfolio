#!/usr/bin/env python3
"""검증된 dataset version으로 YOLOX-Nano를 MPS/CPU에서 전이학습한다."""

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
    load_trained_checkpoint,
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
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)


def build_optimizer(model: torch.nn.Module, learning_rate: float, settings: dict[str, Any]) -> torch.optim.Optimizer:
    """공식 YOLOX와 같은 BN/no-decay, weight/decay, bias 그룹의 SGD를 구성한다."""
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
# 기능:
#   공식 YOLOX 전처리와 train split을 연결하고 촬영 session별 sampling을 구성한다.
# 동작 방법:
#   COCO image ID 순서로 session 가중치를 대응시켜 WeightedRandomSampler에 전달한다.
#   설정이 없으면 기존 shuffle을 사용하며 sanity 실행은 작은 고정 subset을 사용한다.
# 사용 이유:
#   실제 차량 장착·디오라마 자료의 학습 노출을 늘리면서 평가 데이터의 분포를 유지한다.
# 입력:
#   training의 session_sampling_weights는 session별 양수 상대 선택 가중치다.
# 반환:
#   한 epoch에 train 이미지 수만큼 추출하는 DataLoader. 중복 추출을 허용한다.
# -----------------------------------------------------------------------------
def create_training_loader(
    source_dir: Path,
    dataset_dir: Path,
    input_size: tuple[int, int],
    training: dict[str, Any],
    batch_size: int,
    sanity_image_count: int | None,
) -> torch.utils.data.DataLoader:
    """crop/Mosaic/MixUp 없이 공식 COCODataset과 TrainTransform만 연결한다."""
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
    session_weights = training.get("session_sampling_weights", {})
    sampler = None
    if session_weights and sanity_image_count is None:
        # COCO 이미지 ID 순서와 Dataset 순서를 연결해 실제 촬영 session의
        # 노출 빈도를 높인다. val/test 파일이나 bbox 정답은 변경하지 않는다.
        if not isinstance(session_weights, dict):
            raise DatasetContractError("session_sampling_weights는 session별 양수 가중치 객체여야 합니다.")
        coco_images = read_json(dataset_dir / "annotations/instances_train.json")["images"]
        images_by_id = {item["id"]: item for item in coco_images}
        weights = [float(session_weights.get(images_by_id[image_id]["session_id"], 1.0)) for image_id in dataset.ids]
        if any(not math.isfinite(weight) or weight <= 0 for weight in weights):
            raise DatasetContractError("sampling 가중치는 유한한 양수여야 합니다.")
        sampler = torch.utils.data.WeightedRandomSampler(
            weights, num_samples=len(dataset), replacement=True,
            generator=torch.Generator().manual_seed(int(training["random_seed"])),
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
        shuffle=sampler is None,
        sampler=sampler,
        num_workers=int(training["num_workers"]),
        drop_last=False,
    )


def _git_commit(project_dir: Path) -> str | None:
    result = subprocess.run(["git", "-C", str(project_dir), "rev-parse", "HEAD"], capture_output=True, text=True)
    return result.stdout.strip() if result.returncode == 0 else None


def train_model(args: argparse.Namespace) -> Path:
    """새 experiment를 생성하고 last/best checkpoint와 재현 정보를 함께 저장한다."""
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
    initial_checkpoint = training.get("initial_checkpoint")
    if initial_checkpoint:
        # 공식 사전학습의 backbone만 가져오는 경로와 달리 학습된 head까지 보존한다.
        # 새 데이터셋으로 미세조정하므로 optimizer/epoch는 새 실험에서 시작한다.
        initial_path = (config_path.parent / initial_checkpoint).resolve()
        expected_sha = training.get("initial_checkpoint_sha256")
        if not expected_sha or sha256_file(initial_path) != expected_sha:
            raise DatasetContractError("미세조정 initial checkpoint SHA가 설정과 다릅니다.")
        model, parent_checkpoint = load_trained_checkpoint(source_dir, initial_path, len(class_names), model_variant)
        if parent_checkpoint.get("classes") != class_names or parent_checkpoint.get("input_size") != list(input_size):
            raise DatasetContractError("미세조정 checkpoint의 클래스 순서 또는 입력 크기가 다릅니다.")
        transfer = {"initial_checkpoint": str(initial_path), "sha256": expected_sha,
                    "epoch": parent_checkpoint.get("epoch"), "mode": "full_model_finetune"}
    else:
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
        if backbone_frozen and training.get("freeze_backbone_batch_norm", False):
            # requires_grad만 끄면 BatchNorm 통계는 계속 변한다. 특징 추출기를
            # 온전히 보존하는 미세조정에서는 running mean/variance도 고정한다.
            model.backbone.eval()
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
        if initial_checkpoint:
            # 단기 미세조정은 epoch별 후보를 보존해 필수 장면과 기존 성능을
            # 함께 확인한 뒤 선택할 수 있게 한다. 정식 재학습 경로는 그대로 둔다.
            torch.save(checkpoint_state, experiment_dir / "checkpoints" / f"epoch_{epoch:03d}.pth")
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
    try:
        output = train_model(parse_args())
    except (DatasetContractError, OSError, RuntimeError, ValueError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print(f"학습 완료: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
