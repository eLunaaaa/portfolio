# 프로젝트 루트에서 실행. PYTHON으로 PyTorch 설치 인터프리터를 지정합니다.
PYTHON ?= python3
CONFIG ?= configs/baseline.json
.PHONY: generate train test evaluate clean
generate:
	$(PYTHON) python/generate_cnn_top.py $(CONFIG)
train:
	$(PYTHON) python/train_cnn.py $(CONFIG)
test:
	$(PYTHON) tests/run_checks.py
evaluate:
	$(PYTHON) python/evaluate_design.py $(CONFIG)
clean:
	rm -rf build rtl/converted
