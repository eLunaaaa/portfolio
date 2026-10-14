#!/usr/bin/env python3
# 파일 설명: MNIST 데이터 전처리, Adam 학습, 계층 출력 Q3.4 평가와 가중치 저장을 담당합니다.
"""
JSON CNN의 실수 학습과 계층 출력의 Q3.4 변환 평가
"""

import torch
import torch.nn as nn
import torch.optim as optim
from torchvision import datasets, transforms
from torch.utils.data import DataLoader, Subset
import numpy as np
import sys
import os
import json
import random

# Import our model builder
from build_model import build_model_from_config


# 기능: 재현 가능한 난수 환경 설정
# 입력: 정수 seed
# 출력: 전역 난수 설정 변경
# 처리 구조: Python·NumPy·PyTorch의 난수와 결정적 연산 옵션 설정
def set_seed(seed=42):
    """Set all random seeds for reproducibility"""
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed(seed)
        torch.cuda.manual_seed_all(seed)
    torch.backends.cudnn.deterministic = True
    torch.backends.cudnn.benchmark = False
    torch.use_deterministic_algorithms(True, warn_only=True)
    os.environ['PYTHONHASHSEED'] = str(seed)
    print(f"✓ All random seeds set to {seed} for reproducibility")


# 기능: MNIST 모델 학습 및 출력 Q3.4 평가
# 입력: 모델·설정 이름·epoch·batch·학습률
# 출력: 학습 모델과 Q3.4 출력 평가 정확도 튜플
# 처리 구조: 설정 크기 전처리→6,000개 학습→테스트→Q3.4 평가
def train_model(model, config_name, epochs=3, batch_size=64, lr=0.001):
    """실수 파라미터를 학습한 후 계층 출력 Q3.4 변환을 평가합니다."""
    if epochs <= 0 or batch_size <= 0 or not np.isfinite(lr) or lr <= 0:
        raise ValueError("epochs, batch_size and lr must be positive")
    device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
    print(f"Training on: {device}")

    model = model.to(device)

    input_size = model.input_size

    # MNIST dataset
    transform = transforms.Compose([
        transforms.Resize((input_size, input_size)),
        transforms.ToTensor(),
        transforms.Normalize((0.1307,), (0.3081,)) # mean/std-dev for MNIST dataset
    ])

    # Load MNIST datasets
    full_train_dataset = datasets.MNIST('./data/mnist', train=True, download=True, transform=transform)
    test_dataset = datasets.MNIST('./data/mnist', train=False, download=True, transform=transform)

    g = torch.Generator()
    g.manual_seed(42)

    train_indices = torch.arange(6000)
    train_dataset = Subset(full_train_dataset, train_indices)

    print(f"\n Using reduced training set for faster training:")
    print(f"   Training samples: {len(train_dataset)}")
    print(f"   Test samples: {len(test_dataset)}")

    train_loader = DataLoader(train_dataset, batch_size=batch_size, shuffle=True, generator = g, num_workers = 0)
    test_loader = DataLoader(test_dataset, batch_size=batch_size, shuffle=False, generator = g, num_workers = 0)

    criterion = nn.CrossEntropyLoss()
    optimizer = optim.Adam(model.parameters(), lr=lr)

    print(f"Training {config_name} for {epochs} epochs...")

    for epoch in range(epochs):
        # Training
        model.train()
        train_loss = 0
        correct = 0
        total = 0

        for batch_idx, (data, target) in enumerate(train_loader):
            data, target = data.to(device), target.to(device)

            optimizer.zero_grad()
            output = model(data)
            loss = criterion(output, target)
            loss.backward()
            optimizer.step()

            train_loss += loss.item()
            _, predicted = output.max(1)
            total += target.size(0)
            correct += predicted.eq(target).sum().item()

            if batch_idx % 50 == 0:  # Print every 50 batches
                print(f'Epoch: {epoch+1}/{epochs} [{batch_idx*len(data)}/{len(train_loader.dataset)} '
                      f'({100.*batch_idx/len(train_loader):.0f}%)] Loss: {loss.item():.6f}')

        train_acc = 100. * correct / total
        avg_loss = train_loss / len(train_loader)

        # Testing
        model.eval()
        test_loss = 0
        correct = 0
        total = 0

        with torch.no_grad():
            for data, target in test_loader:
                data, target = data.to(device), target.to(device)
                output = model(data)
                test_loss += criterion(output, target).item()
                _, predicted = output.max(1)
                total += target.size(0)
                correct += predicted.eq(target).sum().item()

        test_acc = 100. * correct / total
        avg_test_loss = test_loss / len(test_loader)

        print(f'Epoch {epoch+1}: Train Loss: {avg_loss:.4f}, Train Acc: {train_acc:.2f}%, '
              f'Test Loss: {avg_test_loss:.4f}, Test Acc: {test_acc:.2f}%\n')

    # Final evaluation
    print(f"\nFinal Test Accuracy (without quantization): {test_acc:.2f}%")

    # Now enable quantization for final evaluation
    print("\nEnabling quantization for hardware compatibility check...")
    for module in model.modules():
        if hasattr(module, 'quantize_weights'):
            module.quantize_weights = True

    # Evaluate again with quantization enabled
    model.eval()
    correct = 0
    total = 0
    with torch.no_grad():
        for data, target in test_loader:
            data, target = data.to(device), target.to(device)
            output = model(data)
            _, predicted = output.max(1)
            total += target.size(0)
            correct += predicted.eq(target).sum().item()

    quantized_acc = 100. * correct / total
    print(f"Test Accuracy with Q3.4 quantization: {quantized_acc:.2f}%")
    print(f"Accuracy drop due to quantization: {test_acc - quantized_acc:.2f}%")

    return model, quantized_acc

# 기능: 학습된 가중치 저장
# 입력: 모델과 설정 이름
# 출력: data/weights/<설정>.pth
# 처리 구조: 저장 폴더 생성 후 state_dict 직렬화
def save_model(model, config_name):
    """Save trained model"""
    os.makedirs('data/weights', exist_ok=True)
    save_path = f'data/weights/{config_name}.pth'
    torch.save(model.state_dict(), save_path)
    print(f"Model saved to {save_path}")

if __name__ == '__main__':
    if len(sys.argv) < 2:
        print("Usage: python train_cnn.py <config.json>")
        sys.exit(1)

    config_file = sys.argv[1]

    # Load configuration
    with open(config_file, 'r') as f:
        config = json.load(f)
    config_name = os.path.basename(config_file).split('.')[0]

    set_seed(42)

    # Build model
    print(f"Building model from {config_file}...")
    model = build_model_from_config(config_file)

    # Train model
    trained_model, accuracy = train_model(model, config_name, epochs=5)

    # Save model
    save_model(trained_model, config_name)

    # Save accuracy to metadata
    metadata = {
        'config': config_name,
        'test_accuracy': accuracy,
        'quantization': 'Q3.4'
    }

    metadata_path = f'data/weights/{config_name}_metadata.json'
    with open(metadata_path, 'w') as f:
        json.dump(metadata, f, indent=2)

    print(f"\nTraining complete! Metadata saved to {metadata_path}")
