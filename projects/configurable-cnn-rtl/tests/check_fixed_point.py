"""저장 가중치→Q3.4 계수→정수 기준 출력→RTL을 연결해 비교합니다."""
from pathlib import Path
import sys
import hashlib
import json
import subprocess
import numpy as np
import torch
sys.path.insert(0,str(Path('python').resolve()))
from fixed_point import quantize,forward


def main():
    """입력: baseline 가중치·고정 테스트 벡터. 출력: 100개 점수의 bit-exact 검사.
    구조·이유: 현재 가중치 hash와 계수부터 확인한 후 정수 기준 모델·RTL 양쪽에서 출력합니다.
    """
    meta=json.loads(Path('tests/vectors/baseline.json').read_text())
    model=Path('data/weights/baseline.pth')
    assert hashlib.sha256(model.read_bytes()).hexdigest()==meta['model_sha256']
    state=torch.load(model,map_location='cpu',weights_only=True)
    parameters=[(quantize(state['0.conv.weight'].numpy()),quantize(state['0.conv.bias'].numpy())),(quantize(state['4.fc.weight'].numpy()),quantize(state['4.fc.bias'].numpy()))]
    raw=np.array([int(line,16) for line in Path('tests/vectors/baseline.mem').read_text().splitlines()],dtype=np.int64)
    raw=np.where(raw>=128,raw-256,raw)
    coefficients=np.concatenate([value.flatten() for pair in parameters for value in pair])
    np.testing.assert_array_equal(raw[:750],coefficients)
    layers=json.loads(Path('configs/baseline.json').read_text())['layers']
    for i in range(10):
        start=750+i*235
        image=raw[start:start+225].reshape(1,15,15)
        np.testing.assert_array_equal(forward(image,layers,parameters),raw[start+225:start+235])
    assert int((np.int64(-1)*np.int64(1))>>4)==-1
    Path('build').mkdir(exist_ok=True)
    subprocess.run(['iverilog','-g2012','-s','tb_fixed_point','-o','build/fixed-point.out',*map(str,Path('rtl').glob('*.v')),'testbench/tb_fixed_point.v'],check=True)
    subprocess.run(['vvp','build/fixed-point.out'],check=True)


if __name__=='__main__':main()
