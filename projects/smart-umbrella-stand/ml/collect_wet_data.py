#!/usr/bin/env python3
# 스마트 우산꽂이 모델의 UART 표본 수집 도구입니다.
# Master의 H/UMB/HMEAN/DH 로그를 읽고 사용자가 지정한 dry/wet label을 붙여 CSV에 저장합니다.

"""Collect Master UART samples formatted as H=.. UMB=.. HMEAN=.. DH=..."""

import argparse
import csv
import re
from pathlib import Path

import serial

SAMPLE = re.compile(
    r"H=(?P<H>-?\d+(?:\.\d+)?)\s+UMB=(?P<UMB>[01])\s+"
    r"HMEAN=(?P<HMEAN>-?\d+(?:\.\d+)?)\s+DH=(?P<DH>-?\d+(?:\.\d+)?)"
)
FIELDS = ["H", "UMB", "HMEAN", "DH", "LABEL"]


# 기능: Master UART 로그를 dry/wet label을 붙인 CSV로 수집합니다.
# 입력: serial 포트, label 0/1, baud, output 경로.
# 출력: H/UMB/HMEAN/DH/LABEL 열의 CSV.
# 처리 구조: UART를 읽어 정규식에 맞는 표본만 추출하고 label을 붙여 flush합니다. Ctrl+C 시 파일을 닫고 종료합니다.
def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("port", help="serial port, e.g. COM5 or /dev/tty.usbmodem...")
    parser.add_argument("label", type=int, choices=(0, 1), help="0=dry, 1=wet")
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument("--output", type=Path, default=Path("wet_dataset.csv"))
    args = parser.parse_args()

    new_file = not args.output.exists() or args.output.stat().st_size == 0
    with serial.Serial(args.port, args.baud, timeout=1) as uart, args.output.open(
        "a", newline="", encoding="utf-8"
    ) as stream:
        writer = csv.DictWriter(stream, fieldnames=FIELDS)
        if new_file:
            writer.writeheader()
        print("Collecting samples. Press Ctrl+C to stop.")
        try:
            while True:
                line = uart.readline().decode("utf-8", errors="ignore")
                match = SAMPLE.search(line)
                if not match:
                    continue
                row = match.groupdict()
                row["LABEL"] = args.label
                writer.writerow(row)
                stream.flush()
                print(row)
        except KeyboardInterrupt:
            print(f"Saved to {args.output}")


if __name__ == "__main__":
    main()
