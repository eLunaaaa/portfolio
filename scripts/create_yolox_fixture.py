"""CI용 임시 YOLOX checkpoint 생성. 실제 학습 성능 대신 로딩·전파·export 계약을 검사합니다."""
import argparse
from pathlib import Path
import sys


def main():
    """입력: 출력 경로. 출력: seed 2026의 임시 80-class Tiny state_dict.
    구조·이유: 포함된 소스로 모델을 만들고 임시 경로에만 저장해 외부 checkpoint 다운로드를 제거합니다.
    """
    parser=argparse.ArgumentParser();parser.add_argument('output',type=Path);parser.add_argument('--variant',choices=['nano','tiny'],default='tiny');args=parser.parse_args()
    root=Path(__file__).resolve().parents[1]/'projects/blue-witness/source/model_training'
    sys.path.insert(0,str(root))
    import torch
    from yolox_runtime import build_yolox_model
    torch.set_num_threads(2);torch.manual_seed(2026)
    model=build_yolox_model(root/'third_party/YOLOX',80,args.variant)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    torch.save({'model':model.state_dict()},args.output)
    print('임시 모델 fixture 생성 완료')


if __name__=='__main__':main()
