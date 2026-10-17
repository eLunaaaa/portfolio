# 프로젝트 루트에서 실행. PYTHON에 설치된 Python 인터프리터를 지정합니다.
PYTHON ?= python3
CXX ?= c++
.PHONY: test qt-test firmware-test uno
test:
	$(PYTHON) -m unittest discover -s tests -v
qt-test:
	$(PYTHON) tests/check_qt_controls.py
firmware-test:
	mkdir -p build
	$(CXX) -std=c++17 -Wall -Wextra -Werror -Itests/arduino_stub tests/test_firmware.cpp -o build/test_firmware
	./build/test_firmware
	PYTHONPATH=. $(PYTHON) tests/check_serial_contract.py
uno:
	arduino-cli compile --fqbn arduino:avr:uno firmware/news_lcd
