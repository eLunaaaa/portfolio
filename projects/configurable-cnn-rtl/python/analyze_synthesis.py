#!/usr/bin/env python3
"""Yosys 최종 통계에서 셀 수와 셀 가중치 기반 면적·전력 비교 지표를 계산합니다."""
import json
from pathlib import Path
import re
import sys


def parse_synthesis_log(log_file):
    """기능: 최종 셀 통계 추출. 입력: Yosys 로그 경로. 출력: 지표 dict.
    구조: 마지막 stat의 셀 표를 읽고 게이트 종류별 가중치를 합산합니다.
    """
    content = Path(log_file).read_text()
    headers = list(re.finditer(r'(?:Number of cells:\s+(\d+)|(\d+)\s+cells)', content))
    if not headers: raise ValueError('합성 로그에 셀 통계가 없습니다')
    header = headers[-1]
    cells = {}
    for line in content[header.end():].splitlines():
        old = re.fullmatch(r'\s*(\$\S+)\s+(\d+)\s*', line)
        new = re.fullmatch(r'\s*(\d+)\s+(\$\S+)\s*', line)
        if old: cells[old[1]] = int(old[2])
        elif new: cells[new[2]] = int(new[1])
        elif cells and line.strip(): break
    total = int(header[1] or header[2])
    if sum(cells.values()) != total: raise ValueError('최종 셀 수와 셀 표가 일치하지 않습니다')
    area = power = 0.0
    sequential = 0
    for cell, count in cells.items():
        name = cell.upper()
        if 'DFF' in name or 'LATCH' in name:
            weight, energy = 8.0, 0.0045
            sequential += count
        elif 'MUX' in name: weight, energy = 3.0, 0.0012
        elif 'XOR' in name or 'XNOR' in name: weight, energy = 4.0, 0.001
        elif 'NOT' in name: weight, energy = 0.5, 0.0002
        else: weight, energy = 1.5, 0.0004
        area += count * weight
        power += count * energy
    return dict(total_cells=total, cells=cells, area_ge=area, power_mw=power,
                sequential_cells=sequential, combinational_cells=total-sequential,
                estimation_method='셀 종류별 고정 가중치 합산')


def save_metrics(config_name, metrics):
    """기능: 지표 JSON 저장. 입력: 설정 이름·지표. 출력: 저장 경로.
    구조: 결과 폴더를 생성한 뒤 UTF-8 JSON으로 기록합니다.
    """
    path = Path('synthesis/results') / f'{config_name}_metrics.json'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(metrics, ensure_ascii=False, indent=2) + '\n')
    return path


if __name__ == '__main__':
    if len(sys.argv) != 3: sys.exit('사용법: analyze_synthesis.py <로그> <설정 이름>')
    metrics = parse_synthesis_log(sys.argv[1])
    print(json.dumps(metrics, ensure_ascii=False, indent=2))
    print(save_metrics(sys.argv[2], metrics))
