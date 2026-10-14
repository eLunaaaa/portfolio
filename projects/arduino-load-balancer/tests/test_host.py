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
    def test_signed_frame(self):
        frame = host.packet([-128] * 25, [127] * 9)
        self.assertEqual(len(frame), 38)
        self.assertEqual(frame, b'<M' + bytes([128]) * 25 + b'F' + bytes([127]) * 9 + b'>')
        for values in [[128] * 25, [True] * 25, [0] * 24]:
            with self.assertRaises(ValueError): host.packet(values, [0] * 9)

    def test_reference(self):
        self.assertEqual(host.reference(list(range(25)), [1,0,-1,2,0,-2,1,0,-1]), [-8] * 9)
        self.assertEqual(host.reference([-128] * 25, [-128] * 9), [147456] * 9)

    def test_ready_credits_and_out_of_order_results(self):
        lines = ['ARDUINO_READY', 'ACCEPTED 0', 'RDY_FOR_NEXT', 'ACCEPTED 1',
                 'RESULT 1 ' + ' '.join(map(str, [6,18,12] * 3)),
                 'RDY_FOR_NEXT', 'RESULT 0 ' + ' '.join(['-8'] * 9)]
        class UART:
            def __init__(self): self.sent = []
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def readline(self): return (lines.pop(0) + '\n').encode()
            def write(self, data): self.sent.append(data)
            def flush(self): pass
        uart = UART()
        with patch.object(sys, 'argv', ['stream_matrices.py', 'MOCK']), patch.object(host.serial, 'Serial', return_value=uart), contextlib.redirect_stdout(io.StringIO()):
            host.main()
        self.assertEqual(len(uart.sent), 2)
        self.assertEqual(len(uart.sent[0]), 38)

if __name__ == '__main__':
    unittest.main()
