# 기능 검산용 테스트 파일입니다.
# 고정·경계·오류 입력 또는 대역 환경에서 함수 결과를 기대값과 비교합니다.

import contextlib
import importlib.util
import io
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

HOST = Path(__file__).resolve().parents[1] / 'host'
spec = importlib.util.spec_from_file_location('host_tool', HOST / 'stream_matrices.py')
host = importlib.util.module_from_spec(spec)
spec.loader.exec_module(host)

class HostTests(unittest.TestCase):
    # 기능: UART frame 길이·signed byte 표현과 잘못된 입력 거부를 확인합니다.
    # 입력: 함수 내부의 고정 표본·대역 환경.
    # 출력: 정상 조건은 assertion 통과, 차이는 assertion/테스트 실패.
    # 처리 구조: 정상·경계·오류 조건을 구성해 기능의 기대 결과와 비교합니다.
    def test_signed_frame(self):
        frame = host.packet([-128] * 25, [127] * 9)
        self.assertEqual(len(frame), 38)
        self.assertEqual(frame, b'<M' + bytes([128]) * 25 + b'F' + bytes([127]) * 9 + b'>')
        for values in [[128] * 25, [True] * 25, [0] * 24]:
            with self.assertRaises(ValueError): host.packet(values, [0] * 9)

    # 기능: PC 기준 합성곱을 알려진 결과와 큰 signed 누산값으로 확인합니다.
    # 입력: 함수 내부의 고정 표본·대역 환경.
    # 출력: 정상 조건은 assertion 통과, 차이는 assertion/테스트 실패.
    # 처리 구조: 정상·경계·오류 조건을 구성해 기능의 기대 결과와 비교합니다.
    def test_reference(self):
        self.assertEqual(host.reference(list(range(25)), [1,0,-1,2,0,-2,1,0,-1]), [-8] * 9)
        self.assertEqual(host.reference([-128] * 25, [-128] * 9), [147456] * 9)

    # 기능: UART 대역의 준비 credit와 역순 결과 응답에도 올바른 입력을 검산하는지 확인합니다.
    # 입력: 함수 내부의 고정 표본·대역 환경.
    # 출력: 정상 조건은 assertion 통과, 차이는 assertion/테스트 실패.
    # 처리 구조: 정상·경계·오류 조건을 구성해 기능의 기대 결과와 비교합니다.
    def test_ready_credits_and_out_of_order_results(self):
        lines = ['ARDUINO_READY', 'ACCEPTED 0', 'RDY_FOR_NEXT', 'ACCEPTED 1',
                 'RESULT 1 ' + ' '.join(map(str, [6,18,12] * 3)),
                 'RDY_FOR_NEXT', 'RESULT 0 ' + ' '.join(['-8'] * 9)]
        class UART:
            # 기능: UART 대역의 송신 기록 공간을 만듭니다.
            # 입력: 테스트 client의 인자 또는 준비된 응답 목록.
            # 출력: UART 대역의 반환값·송신 기록.
            # 처리 구조: 실제 serial 장치 대신 host main의 입출력 흐름을 재현합니다.
            def __init__(self): self.sent = []
            # 기능: with 구문에서 UART 대역 자신을 반환합니다.
            # 입력: 테스트 client의 인자 또는 준비된 응답 목록.
            # 출력: UART 대역의 반환값·송신 기록.
            # 처리 구조: 실제 serial 장치 대신 host main의 입출력 흐름을 재현합니다.
            def __enter__(self): return self
            # 기능: UART 대역의 with 종료 호출을 받습니다.
            # 입력: 테스트 client의 인자 또는 준비된 응답 목록.
            # 출력: UART 대역의 반환값·송신 기록.
            # 처리 구조: 실제 serial 장치 대신 host main의 입출력 흐름을 재현합니다.
            def __exit__(self, *args): pass
            # 기능: 미리 준비한 UART 응답 한 줄을 반환합니다.
            # 입력: 테스트 client의 인자 또는 준비된 응답 목록.
            # 출력: UART 대역의 반환값·송신 기록.
            # 처리 구조: 실제 serial 장치 대신 host main의 입출력 흐름을 재현합니다.
            def readline(self): return (lines.pop(0) + '\n').encode()
            # 기능: 호스트가 전송한 bytes를 기록합니다.
            # 입력: 테스트 client의 인자 또는 준비된 응답 목록.
            # 출력: UART 대역의 반환값·송신 기록.
            # 처리 구조: 실제 serial 장치 대신 host main의 입출력 흐름을 재현합니다.
            def write(self, data): self.sent.append(data)
            # 기능: UART 대역에서 실제 출력 대기 없이 반환합니다.
            # 입력: 테스트 client의 인자 또는 준비된 응답 목록.
            # 출력: UART 대역의 반환값·송신 기록.
            # 처리 구조: 실제 serial 장치 대신 host main의 입출력 흐름을 재현합니다.
            def flush(self): pass
        uart = UART()
        with patch.object(sys, 'argv', ['stream_matrices.py', 'MOCK']), patch.object(host.serial, 'Serial', return_value=uart), contextlib.redirect_stdout(io.StringIO()):
            host.main()
        self.assertEqual(len(uart.sent), 2)
        self.assertEqual(len(uart.sent[0]), 38)

if __name__ == '__main__':
    unittest.main()
