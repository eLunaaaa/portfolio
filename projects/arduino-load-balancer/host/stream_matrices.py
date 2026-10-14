#!/usr/bin/env python3
# 분산 합성곱의 PC 호스트 프로그램입니다.
# JSON의 입력·필터를 UART frame으로 만들고 Master의 준비 신호에 맞춰 전송합니다.
# 반환된 batch 번호를 입력 순서와 연결해 Python 기준 합성곱과 비교합니다.

"""Publication host utility for framed input and result validation."""
import argparse
import json
import math
from pathlib import Path
import time
import serial


# 기능: 행렬·필터의 개수와 signed byte 범위를 검증합니다.
# 입력: value: 값 배열, count: 기대 원소 수.
# 출력: 유효하면 원 배열, 아니면 ValueError.
# 처리 구조: flat list의 길이를 검사하고 bool을 제외한 정수 -128~127인지 모든 항을 확인합니다.
def signed_values(value, count):
    if not isinstance(value, list) or len(value) != count:
        raise ValueError(f"expected a flat list of {count} integers")
    if any(type(v) is not int or not -128 <= v <= 127 for v in value):
        raise ValueError("matrix values must be signed bytes (-128..127)")
    return value


# 기능: 호스트의 행렬·필터를 Master UART frame으로 만듭니다.
# 입력: input_values: 25개, filter_values: 9개 signed 정수.
# 출력: 38바이트의 <M[input]F[filter]> bytes.
# 처리 구조: signed_values로 검사한 뒤 각 값을 8비트 패턴으로 바꿔 정해진 marker 사이에 배치합니다.
def packet(input_values, filter_values):
    values = signed_values(input_values, 25) + signed_values(filter_values, 9)
    return b"<M" + bytes(v & 255 for v in values[:25]) + b"F" + bytes(v & 255 for v in values[25:]) + b">"


# 기능: 분산 처리 결과를 검산할 PC 기준 합성곱을 계산합니다.
# 입력: input_values: 5×5 배열, filter_values: 3×3 배열.
# 출력: 행 우선의 3×3 결과 9개.
# 처리 구조: 9개 출력 시작점마다 3×3 곱을 모두 합산해 MCU 결과와 비교할 값을 만듭니다.
def reference(input_values, filter_values):
    return [sum(input_values[(p // 3 + y) * 5 + p % 3 + x] * filter_values[y * 3 + x]
                for y in range(3) for x in range(3)) for p in range(9)]


# 기능: PC에서 UART 입력을 보내고 분산 결과를 검산합니다.
# 입력: 포트·JSON 행렬 파일·timeout CLI 옵션.
# 출력: 준비/수신/결과 로그와 기준 계산 일치 확인; 오류·timeout이면 예외.
# 처리 구조: 자료를 검증하고 준비 credit마다 한 frame을 보냅니다. ACCEPTED 번호를 입력 순서와 연결해 순서가 뒤바뀐 RESULT도 올바른 기준값과 비교합니다.
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("port")
    parser.add_argument("--data", type=Path, default=Path(__file__).with_name("example_matrices.json"))
    parser.add_argument("--timeout", type=float, default=600, help="seconds; allows demonstration delays")
    args = parser.parse_args()
    if not math.isfinite(args.timeout) or args.timeout <= 0:
        parser.error("timeout must be positive and finite")
    items = json.loads(args.data.read_text())
    if not isinstance(items, list) or not items:
        parser.error("data must contain a nonempty list of matrix/filter objects")
    packets = [packet(item["input"], item["filter"]) for item in items]
    expected = [reference(item["input"], item["filter"]) for item in items]
    with serial.Serial(args.port, 115200, timeout=1) as uart:
        next_to_send = 0
        accepted = {}
        accepted_count = 0
        completed = set()
        deadline = time.monotonic() + args.timeout
        print("Wait for ARDUINO_READY. Press the Master D8 button after data loads.")
        while time.monotonic() < deadline:
            line = uart.readline().decode("ascii", errors="replace").strip()
            if not line:
                continue
            print(line)
            if line in ("ARDUINO_READY", "RDY_FOR_NEXT") and next_to_send < len(items):
                uart.write(packets[next_to_send]); uart.flush(); next_to_send += 1
            elif line.startswith("ACCEPTED "):
                sequence = int(line.split()[1])
                accepted[sequence] = accepted_count
                accepted_count += 1
            elif line == "BUSY":
                raise RuntimeError("Master rejected a frame; reset the session and send only on readiness credits")
            elif line.startswith("RESULT "):
                fields = [int(v) for v in line.split()[1:]]
                if len(fields) != 10 or fields[0] not in accepted:
                    raise RuntimeError("unexpected result frame")
                index = accepted.pop(fields[0])
                if fields[1:] != expected[index]:
                    raise RuntimeError(f"matrix {index}: result differs from CPU reference")
                completed.add(index)
                print(f"Matrix {index}: reference match")
                if len(completed) == len(items):
                    return
        raise TimeoutError("session timed out")


if __name__ == "__main__":
    main()
