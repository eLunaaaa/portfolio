#!/usr/bin/env python3
"""설정·모델·저장 가중치와 RTL 연산·연결의 재현 가능한 검증 실행 파일."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
sys.path.insert(0, str(Path('python').resolve()))
from config_utils import load_config
from generate_cnn_top import generate_cnn_top
from analyze_synthesis import parse_synthesis_log
import torch
from build_model import build_model_from_config


def main():
    # 기능: 전체 검증 실행. 입력: 프로젝트 파일. 출력: PASS 또는 예외 종료.
    # 처리 구조: 형상·모델·가중치→통계 파서→연산 모듈→top 통합 순서로 검사.
    torch.set_num_threads(2)
    build = Path('build'); build.mkdir(exist_ok=True)
    for name in ['baseline', 'two_convs', 'test_config']:
        config, shapes = load_config(f'configs/{name}.json')
        generate_cnn_top(f'configs/{name}.json', build/f'{name}.v')
        model = build_model_from_config(f'configs/{name}.json')
        checkpoint = Path(f'data/weights/{name}.pth')
        if checkpoint.exists(): model.load_state_dict(torch.load(checkpoint, map_location='cpu', weights_only=True))
        x = torch.randn(2,1,15,15); output = model(x)
        assert output.shape == (2,10)
        output.sum().backward()
        for layer in model.modules():
            if hasattr(layer, 'quantize_weights'): layer.quantize_weights = True
        assert torch.isfinite(model(x)).all()
        print(name, '모델·가중치·순전파·역전파·Q3.4 PASS')
    bad = json.loads(Path('configs/baseline.json').read_text())
    bad['layers'][-1]['in_features'] = 999
    path = build/'invalid.json'; path.write_text(json.dumps(bad))
    try: load_config(path)
    except ValueError: pass
    else: raise AssertionError('잘못된 FC 특징 수를 허용했습니다')
    log = build/'parser.log'
    log.write_text('Number of cells: 1\n $and 1\n\nNumber of cells: 3\n $_AND_ 2\n $_DFF_P_ 1\n')
    assert parse_synthesis_log(log)['total_cells'] == 3
    for layer in ['conv','fc','pool','relu']:
        executable = build/f'{layer}.out'
        subprocess.run(['iverilog','-g2012','-s',f'tb_{layer}_layer','-o',str(executable),f'rtl/{layer}_layer.v',f'testbench/tb_{layer}_layer.v'], check=True)
        result = subprocess.run(['vvp',executable.name], cwd=build, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        (build/f'{layer}.log').write_text(result.stdout)
        if result.returncode: raise RuntimeError(result.stdout)
        print(layer,'RTL PASS')
    subprocess.run(['iverilog','-g2012','-s','tb_cnn_top','-o','build/top.out',*map(str,Path('rtl').glob('*.v')),'testbench/tb_cnn_top.v'],check=True)
    subprocess.run(['vvp','build/top.out'],check=True)
    subprocess.run([sys.executable,'tests/check_fixed_point.py'],check=True)


if __name__ == '__main__': main()
