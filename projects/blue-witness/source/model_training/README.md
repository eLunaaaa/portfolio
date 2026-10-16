# 모델 학습 담당 코드

[설계 선택](../../docs/design/MODEL_TRAINING.md) · [학습·평가 설정](../../ml/README.md) · [공통 의존성 안내](../README.md#학습-환경)

| 순서 | 구현 |
|---|---|
| 촬영 등록·출처 | [register_recordings.py](register_recordings.py), [sync_recordings.py](sync_recordings.py) |
| 프레임 준비·검수 | [prepare_dataset.py](prepare_dataset.py), [review_annotations.py](review_annotations.py) |
| COCO·split·검증 | [export_coco.py](export_coco.py), [validate_dataset.py](validate_dataset.py), [dataset_contracts.py](dataset_contracts.py) |
| class 통합 | [derive_two_class_dataset.py](derive_two_class_dataset.py) |
| 학습 | [train.py](train.py), [yolox_runtime.py](yolox_runtime.py), [config_2class_tiny.yaml](config_2class_tiny.yaml) |
| 평가·배포 | [evaluate.py](evaluate.py), [export_onnx.py](export_onnx.py), [compare_outputs.py](compare_outputs.py) |
| 장치 계약 | [runtime_contract.py](runtime_contract.py) |
| 시험 | [tests](tests) |

서드파티 YOLOX·실제 영상/라벨·checkpoint·ONNX 가중치는 포함하지 않았습니다. 해당 자료가 없으면 전체 학습을 바로 실행할 수 없지만, 데이터·학습·배포 설계 코드는 직접 읽을 수 있습니다. 최종 실행 설정 공통화 등 후속 팀 변경은 통합본 출처로 구분합니다.
