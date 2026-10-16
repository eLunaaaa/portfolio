"""C++ 실행기의 공통 설정에서 ONNX 배포 판정값을 읽는다."""

from __future__ import annotations

import math
import re
from pathlib import Path

RUNTIME_CONFIG = Path(__file__).resolve().parents[1] / "ai" / "inference_config.h"


def load_runtime_contract(path: Path = RUNTIME_CONFIG) -> dict[str, float | int]:
    text = path.read_text(encoding="utf-8")
    fields = {
        "confidence_threshold": "INFERENCE_CONFIDENCE_THRESHOLD",
        "nms_iou_threshold": "INFERENCE_NMS_IOU_THRESHOLD",
        "inference_frame_step": "INFERENCE_FRAME_STEP",
    }
    result: dict[str, float | int] = {}
    for key, macro in fields.items():
        match = re.search(rf"^\s*#define\s+{macro}\s+([0-9]+(?:\.[0-9]+)?)[fFuU]?\s*$", text, re.MULTILINE)
        if match is None:
            raise ValueError(f"실행 설정에 {macro}가 없습니다: {path}")
        value = float(match.group(1))
        if key == "inference_frame_step":
            if value < 1 or not value.is_integer():
                raise ValueError("추론 프레임 간격은 양의 정수여야 합니다.")
            result[key] = int(value)
        else:
            if not 0 < value <= 1:
                raise ValueError(f"{macro}는 0 초과 1 이하이어야 합니다.")
            result[key] = value
    return result


def resolve_export_confidence(requested: float | None, contract: dict[str, float | int]) -> float:
    threshold = float(contract["confidence_threshold"])
    if requested is not None and (not math.isfinite(requested) or not math.isclose(requested, threshold, rel_tol=0, abs_tol=1e-9)):
        raise ValueError(f"배포 confidence는 ai/inference_config.h의 {threshold:.2f}를 사용합니다.")
    return threshold
