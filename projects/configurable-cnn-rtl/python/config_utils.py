"""CNN 모델과 RTL 생성기가 공유하는 구조·차원 검증입니다."""
# 파일 설명: Python 모델과 RTL 생성기가 공유하는 입력 크기·채널·FC 형상 검증 파일입니다.
import json
from pathlib import Path


# 기능: 계층 간 형상과 설정 유효성 검사
# 입력: JSON 파일 경로
# 출력: 설정 dict와 계층별 입력·출력 형상
# 처리 구조: 채널·커널·stride·FC 특징 수를 순차 검증하고 형상 추적
def load_config(path):
    config = json.loads(Path(path).read_text(encoding="utf-8"))
    size = config.get("input_size", 15)
    if type(size) is not int or size <= 0:
        raise ValueError("input_size must be a positive integer")
    if not isinstance(config.get("layers"), list) or not config["layers"]:
        raise ValueError("layers must be a nonempty list")
    channels, height, width = 1, size, size
    features = None
    shapes = []
    for layer in config["layers"]:
        kind = layer.get("type")
        before = (channels, height, width) if features is None else features
        if kind == "conv":
            if features is not None:
                raise ValueError("conv cannot follow fc")
            for key in ("in_channels", "out_channels", "kernel_size", "stride"):
                if type(layer.get(key)) is not int or layer[key] <= 0:
                    raise ValueError(f"invalid conv {key}")
            if layer["in_channels"] != channels:
                raise ValueError("conv in_channels does not match previous output")
            height = (height - layer["kernel_size"]) // layer["stride"] + 1
            width = (width - layer["kernel_size"]) // layer["stride"] + 1
            channels = layer["out_channels"]
        elif kind == "pool":
            if features is not None:
                raise ValueError("pool cannot follow fc")
            for key in ("size", "stride"):
                if type(layer.get(key)) is not int or layer[key] <= 0:
                    raise ValueError(f"invalid pool {key}")
            height = (height - layer["size"]) // layer["stride"] + 1
            width = (width - layer["size"]) // layer["stride"] + 1
        elif kind == "relu":
            if features is not None:
                raise ValueError("this RTL ReLU expects a spatial feature map")
        elif kind == "fc":
            expected = channels * height * width if features is None else features
            if type(layer.get("in_features")) is not int or layer["in_features"] != expected:
                raise ValueError(f"fc in_features must be {expected}")
            if type(layer.get("out_features")) is not int or layer["out_features"] <= 0:
                raise ValueError("invalid fc out_features")
            features = layer["out_features"]
        else:
            raise ValueError(f"unsupported layer type: {kind}")
        if height <= 0 or width <= 0:
            raise ValueError("kernel or pooling window exceeds feature map")
        after = (channels, height, width) if features is None else features
        shapes.append({"input": before, "output": after})
    if features != 10 or config["layers"][-1]["type"] != "fc":
        raise ValueError("MNIST classifier must end with 10 FC outputs")
    config["input_size"] = size
    return config, shapes
