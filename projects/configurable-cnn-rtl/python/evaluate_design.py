#!/usr/bin/env python3
"""JSON 설정에서 RTL 생성·모델 학습·Verilog 변환·합성·비교 점수를 실행합니다.
프로젝트 루트에서 실행하며 각 단계의 실패를 종료 코드로 전달합니다.
"""
import argparse
import json
import math
from pathlib import Path
import subprocess
import sys


def run_command(cmd, description, output=None):
    """기능: 외부 도구 실행. 입력: 인자 목록·설명·로그 경로. 출력: 성공 여부.
    처리 구조: stderr를 stdout에 합쳐 순서대로 읽고 종료 코드를 확인합니다.
    """
    print(description, flush=True)
    with subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True) as process:
        handle = open(output, 'w') if output else None
        try:
            for line in process.stdout:
                if handle: handle.write(line)
                else: print(line, end='')
        finally:
            if handle: handle.close()
        return process.wait() == 0


def generate_rtl(config_file):
    """기능: 설정을 RTL로 변환. 입력: JSON 경로. 출력: 생성 성공 여부.
    처리 구조: 현재 Python 인터프리터로 생성기를 실행합니다.
    """
    return run_command([sys.executable, 'python/generate_cnn_top.py', config_file], 'RTL 생성')


def convert_sv_to_v():
    """기능: 배열 포트를 일반 Verilog로 변환. 입력: rtl/*.v. 출력: 성공 여부.
    처리 구조: 모든 모듈을 함께 변환하여 상위·하위 배열 연결을 유지합니다.
    """
    Path('rtl/converted').mkdir(parents=True, exist_ok=True)
    with open('rtl/converted/design.v', 'w') as output:
        result = subprocess.run(['sv2v', *map(str, sorted(Path('rtl').glob('*.v')))], stdout=output)
    return result.returncode == 0


def synthesize_design(config_name):
    """기능: 합성과 통계 추출. 입력: 설정 이름. 출력: 성공 여부.
    처리 구조: Yosys 로그를 저장한 뒤 분석기로 최종 셀 통계를 읽습니다.
    """
    Path('synthesis/results').mkdir(parents=True, exist_ok=True)
    log = f'synthesis/results/{config_name}_synth.log'
    return run_command(['yosys', '-s', 'synthesis/synth.ys'], 'Yosys 합성', log) and run_command(
        [sys.executable, 'python/analyze_synthesis.py', log, config_name], '합성 통계 분석')


def calculate_score(config_name, accuracy):
    """기능: 정확도와 셀 기반 추정 자원량의 비교 점수 계산.
    입력: 설정 이름·정확도(%). 출력: 실수 점수. 구조: A−0.02 ln(면적×전력).
    """
    metrics = json.loads(Path(f'synthesis/results/{config_name}_metrics.json').read_text())
    area, power = metrics['area_ge'], metrics['power_mw']
    if area <= 0 or power <= 0: raise ValueError('합성 자원량은 양수여야 합니다')
    score = accuracy - 0.02 * math.log(area * power)
    print(f'비교 점수: {score:.4f}')
    return score


def main():
    """기능: 실행 단계 조정. 입력: CLI 설정·--skip-training. 출력: 종료 상태.
    구조: 생성→선택적 학습→변환→합성→저장된 정확도로 비교 점수 계산.
    """
    parser = argparse.ArgumentParser()
    parser.add_argument('config')
    parser.add_argument('--skip-training', action='store_true')
    args = parser.parse_args()
    name = Path(args.config).stem
    if not generate_rtl(args.config): return 1
    if not args.skip_training and not run_command([sys.executable, 'python/train_cnn.py', args.config], '모델 학습'): return 1
    if not convert_sv_to_v() or not synthesize_design(name): return 1
    metadata = json.loads(Path(f'data/weights/{name}_metadata.json').read_text())
    calculate_score(name, metadata['test_accuracy'])
    return 0


if __name__ == '__main__':
    sys.exit(main())
