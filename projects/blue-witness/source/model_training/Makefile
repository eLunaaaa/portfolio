PYTHON ?= .venv/bin/python
CONFIG ?= config.yaml

.PHONY: prepare review export validate train evaluate onnx compare test

prepare:
	$(PYTHON) prepare_dataset.py --config $(CONFIG)

review:
	$(PYTHON) review_annotations.py --config $(CONFIG)

export:
	$(PYTHON) export_coco.py --config $(CONFIG)

validate:
	$(PYTHON) validate_dataset.py --config $(CONFIG)

train:
	@echo "dataset version, experiment name, 해상도, epochs, batch size를 명시해 train.py를 실행하세요."

evaluate:
	@echo "checkpoint와 val/test split을 명시해 evaluate.py를 실행하세요."

onnx:
	@echo "검증된 checkpoint를 명시해 export_onnx.py를 실행하세요."

compare:
	@echo "동일 checkpoint/ONNX/image를 명시해 compare_outputs.py를 실행하세요."

test:
	$(PYTHON) -m unittest discover -s tests -v
