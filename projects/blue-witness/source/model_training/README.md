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
| 추가 미세조정 | [train.py](train.py)의 parent checkpoint 검증·학습 세션 가중 sampling·backbone/BatchNorm 동결 |
| 시험 | [tests](tests) |

로컬 `Desktop/ACE_Ai/vehicle_model`을 기준으로 갱신했습니다. [블러 검수 후 미세조정](config_2class_tiny_blur_removed.yaml), [특정 세션 6배 노출](config_2class_tiny_other1_finetune_6x.yaml), [디오라마 가중 설정](config_2class_tiny_diorama.yaml)을 포함합니다.

서드파티 YOLOX·실제 영상/라벨·checkpoint·ONNX 가중치는 포함하지 않았습니다. 로컬 코드의 YOLOX 경로는 `../YOLOX`입니다. 내보내기 Confidence는 validation 평가·명시값에서 정하며 원격의 runtime_config 공통화 코드와 구분합니다. [로컬 변경 및 검증](../../docs/LOCAL_SNAPSHOT.md)
