#!/usr/bin/env python3
# 파일 설명: JSON CNN을 PyTorch 계층으로 구성합니다. NCHW 입력을 10개 분류 점수로 변환합니다.
"""
Build PyTorch CNN model from JSON configuration
동일한 JSON에서 Python 모델과 Verilog의 계층 구조를 구성합니다.
"""

import os
import json
import torch
import torch.nn as nn
from config_utils import load_config

class QuantizedConv2d(nn.Module):
    """합성곱 출력의 Q3.4 변환을 선택적으로 적용하는 계층"""
    # 기능: 계층 파라미터와 내부 연산 객체 초기화
    # 입력: 채널·특징 수와 커널·stride
    # 출력: 초기화된 계층 객체
    # 처리 구조: PyTorch 계층을 생성하고 출력 변환 옵션을 초기화
    def __init__(self, in_channels, out_channels, kernel_size, stride=1):
        super().__init__()
        self.conv = nn.Conv2d(in_channels, out_channels, kernel_size,
                             stride=stride, padding=0, bias=True)
        self.quantize_weights = False  # 기존 이름 유지: 가중치가 아니라 계층 출력의 Q3.4 변환 옵션

    # 기능: 텐서를 Q3.4 값으로 변환
    # 입력: 실수 텐서 x
    # 출력: 동일한 크기의 변환 텐서
    # 처리 구조: −8~7.9375로 제한하고 1/16 단위로 반올림
    def quantize_q3_4(self, x):
        """Quantize to Q3.4 format (8-bit: 1 sign, 3 int, 4 frac)"""
        # Q3.4 range: -8.0 to +7.9375, step = 0.0625
        x = torch.clamp(x, -8.0, 7.9375)
        x = torch.round(x * 16) / 16  # Quantize to nearest 1/16
        return x

    # 기능: 계층 순전파
    # 입력: 배치 입력 텐서 x
    # 출력: 계층 출력 텐서
    # 처리 구조: 내부 계층 연산 후 옵션이 켜져 있으면 Q3.4 변환
    def forward(self, x):
        # Apply convolution first (without quantization during training)
        out = self.conv(x)

        # Only quantize if enabled (for final evaluation)
        if self.quantize_weights:
            out = self.quantize_q3_4(out)

        return out

class QuantizedLinear(nn.Module):
    """완전연결 출력의 Q3.4 변환을 선택적으로 적용하는 계층"""
    # 기능: 계층 파라미터와 내부 연산 객체 초기화
    # 입력: 입력 특징 수 in_features, 출력 특징 수 out_features
    # 출력: 초기화된 계층 객체
    # 처리 구조: nn.Linear를 구성하고 출력의 Q3.4 양자화 사용 여부를 초기화
    def __init__(self, in_features, out_features):
        super().__init__()
        self.fc = nn.Linear(in_features, out_features, bias=True)
        self.quantize_weights = False  # 기존 이름 유지: 가중치가 아니라 계층 출력의 Q3.4 변환 옵션

    # 기능: 텐서를 Q3.4 값으로 변환
    # 입력: 실수 텐서 x
    # 출력: 동일한 크기의 변환 텐서
    # 처리 구조: −8~7.9375로 제한하고 1/16 단위로 반올림
    def quantize_q3_4(self, x):
        x = torch.clamp(x, -8.0, 7.9375)
        x = torch.round(x * 16) / 16
        return x

    # 기능: 계층 순전파
    # 입력: 배치 입력 텐서 x
    # 출력: 계층 출력 텐서
    # 처리 구조: 내부 계층 연산 후 옵션이 켜져 있으면 Q3.4 변환
    def forward(self, x):
        out = self.fc(x)

        # Only quantize if enabled (for final evaluation)
        if self.quantize_weights:
            out = self.quantize_q3_4(out)

        return out

# 기능: JSON 구조로 CNN 구성
# 입력: 설정 파일 경로
# 출력: 입력 크기를 가진 Sequential 모델
# 처리 구조: 설정 검증→Conv/ReLU/Pool/Flatten/Linear를 순서대로 조립
def build_model_from_config(config_file):
    """Build PyTorch model matching the JSON configuration"""

    config, _ = load_config(config_file)
    config_name = os.path.basename(config_file).split('.')[0]

    layers = []
    current_h, current_w = config["input_size"], config["input_size"]

    first_fc_layer_done = False
    last_fc_channels = None

    for layer_def in config['layers']:
        if layer_def['type'] == 'conv':
            layers.append(QuantizedConv2d(
                in_channels=layer_def['in_channels'],
                out_channels=layer_def['out_channels'],
                kernel_size=layer_def['kernel_size'],
                stride=layer_def['stride']
            ))
            # Update dimensions
            k = layer_def['kernel_size']
            s = layer_def['stride']
            current_h = (current_h - k) // s + 1
            current_w = (current_w - k) // s + 1

        elif layer_def['type'] == 'relu':
            layers.append(nn.ReLU())

        elif layer_def['type'] == 'pool':
            layers.append(nn.MaxPool2d(
                kernel_size=layer_def['size'],
                stride=layer_def['stride']
            ))
            # Update dimensions
            p = layer_def['size']
            s = layer_def['stride']
            current_h = (current_h - p) // s + 1
            current_w = (current_w - p) // s + 1

        elif layer_def['type'] == 'fc':
            last_fc_channels = layer_def['out_features']
            if not first_fc_layer_done:
                layers.append(nn.Flatten())
                first_fc_layer_done = True
            layers.append(QuantizedLinear(
                in_features=layer_def['in_features'],
                out_features=layer_def['out_features']
            ))

    if last_fc_channels != 10:
        raise ValueError("final classifier must have 10 outputs")
    model = nn.Sequential(*layers)
    model.input_size = config["input_size"]

    # Print model architecture
    print(f"\nBuilt model from {config_file}:")
    print(f"Architecture: {config_name}")
    print(f"Layers: {' -> '.join([l['type'] for l in config['layers']])}")
    print(model)

    return model

if __name__ == '__main__':
    import sys
    if len(sys.argv) < 2:
        print("Usage: python build_model.py <config.json>")
        sys.exit(1)

    config_file = sys.argv[1]
    model = build_model_from_config(config_file)

    # Test with random input
    x = torch.randn(1, 1, model.input_size, model.input_size)
    y = model(x)
    print(f"\nTest: Input shape {x.shape} -> Output shape {y.shape}")
