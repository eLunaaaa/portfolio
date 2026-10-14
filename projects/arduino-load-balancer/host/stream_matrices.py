#!/usr/bin/env python3
"""Publication host utility for framed input and result validation."""
import argparse
import json
import math
from pathlib import Path
import time
import serial


def signed_values(value, count):
    if not isinstance(value, list) or len(value) != count:
        raise ValueError(f"expected a flat list of {count} integers")
    if any(type(v) is not int or not -128 <= v <= 127 for v in value):
        raise ValueError("matrix values must be signed bytes (-128..127)")
    return value


def packet(input_values, filter_values):
    values = signed_values(input_values, 25) + signed_values(filter_values, 9)
    return b"<M" + bytes(v & 255 for v in values[:25]) + b"F" + bytes(v & 255 for v in values[25:]) + b">"


def reference(input_values, filter_values):
    return [sum(input_values[(p // 3 + y) * 5 + p % 3 + x] * filter_values[y * 3 + x]
                for y in range(3) for x in range(3)) for p in range(9)]


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
