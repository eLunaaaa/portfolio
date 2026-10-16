# 차량 검출 모델 학습·평가·배포

[프로젝트 개요](../README.md) · [원본 학습 도구](https://github.com/kingwooseok/2026ESWContest_mobility_ACE/tree/5148804/model_training)

현재 포함 코드는 로컬 `ACE_Ai/vehicle_model` 기준입니다. GitHub 정리본과 다른 학습·export 선택은 [로컬 갱신 기록](../docs/LOCAL_SNAPSHOT.md)에 구분했습니다.

## 담당한 흐름

촬영 영상을 프레임으로 등록하고, 작업자별 BBox 결과를 병합·검수한 뒤 데이터셋 버전을 확정합니다. COCO 변환 → 2-class 변환 → YOLOX 학습 → 평가 → ONNX 내보내기를 하나의 도구 흐름으로 구성했습니다. 라벨 검수는 동료와 영상 단위로 분담했습니다.

| 단계 | 구현·관리한 내용 |
|---|---|
| 영상 등록 | 한글 파일명·MP4 처리, 원본 출처·영상별 식별자, 중복 등록 방지 |
| 프레임·라벨 | 기본 5프레임 간격 추출, 동일 이미지 중복 제거, BBox 초안·수동 검수·승인·차량 없음 상태 |
| 공동 작업 | 작업 디렉터리 병합, 동일 결과 건너뛰기, 확정 라벨 충돌 시 기존 결과 보존 |
| 데이터셋 | 촬영 세션·원본 계열 단위 train/validation/test 분리, COCO 검증, negative 포함, 데이터 버전·해시 |
| 학습 | 공식 사전학습 가중치 기반 YOLOX-Tiny fine-tuning, Apple Silicon MPS 우선·CPU fallback, seed·환경·실험 로그 |
| 평가 | class별 BBox 평가, 차종 유무·오탐·검출률, Confidence sweep, 원본 영상 기반 실행 검증 |
| 배포 | ONNX graph 검사, 고정 입력·출력 계약, PyTorch/ONNX 비교 도구, checkpoint·ONNX SHA-256 manifest |

## 차종과 차량 식별의 구분

검수 시에는 은색 아이오닉 5·검은 팰리세이드·기타 모형차의 정보를 남깁니다. 최종 학습에서는 `ioniq5_silver`를 class 0, 팰리세이드와 나머지 차량을 `other_vehicle` class 1로 통합합니다. **차량 번호나 개별 차량 ID를 이미지로 식별하는 모델이 아니라 대상 차종을 찾는 모델**입니다.

## 학습 설정과 실행 모델

| 항목 | 기준 코드·자료 |
|---|---|
| 학습 구조 | YOLOX-Tiny, 640×480, 2 classes |
| 데이터 분리 목표 | train 70% / validation 15% / test 15%, 촬영 묶음 단위이므로 실제 프레임 비율은 달라질 수 있음 |
| 기본 학습 설정 | 최대 100 epochs, batch 8, SGD momentum 0.9, weight decay 0.0005, warmup 5 epochs |
| 로컬 추가 실험 | 세션 가중 sampling 3배·6배, parent checkpoint 미세조정, backbone·BatchNorm 동결, 블러 검수 설정 |
| 증강 설정 | 좌우 반전 확률 0.5, HSV·Mosaic·MixUp 비활성화 |
| 재현 정보 | Python/NumPy/PyTorch seed, dataset/checkpoint hash, Git commit, 실행 장치·환경 |
| 배포 평가 모델 | `tiny_blur_removed_v009_b8`, `dataset_v009`, 선택 checkpoint epoch 1 |
| 추론 전처리 | BGR, FP32 NCHW, 0~255, top-left letterbox pad 114, `/255` 정규화 없음 |
| ONNX 계약 | 입력 `images`: `[1,3,480,640]`; 출력 `raw_predictions`: `[1,6300,7]`; graph 외부에서 grid decode·NMS |
| 배포 판정 | objectness × class score ≥ 0.60, class별 NMS IoU 0.65, 6프레임마다 추론, 최근 3회 중 2회 검출 |

기본 설정의 100 epochs를 최종 모델이 100 epoch 학습됐다는 성과로 해석하지 않습니다. 실제 선택된 모델과 데이터 버전은 평가 명세로 확인합니다. 최종 Confidence 0.60 선정·공통 설정화는 팀의 후속 평가·통합 작업을 포함합니다.

로컬 블러 검수 실험은 5 epochs·backbone 동결·warmup 0을 별도로 설정하고, 보존된 로컬 release manifest의 Confidence는 0.55입니다. 팀 평가 자료의 0.60과 서로 다른 버전·선정 단계이므로 하나로 덮어쓰지 않습니다. 로컬 export는 validation 결과 또는 명시값을 기록합니다.

## 학습 실행 흐름

아래 명령은 **팀 원본 저장소를 내려받은 환경**에서 실행합니다. 원본 영상·라벨·사전학습 가중치는 별도 준비가 필요합니다. 이 포트폴리오는 모델이나 데이터셋을 재배포하지 않습니다.

```bash
cd model_training
python export_coco.py --config config.yaml
python validate_dataset.py --config config.yaml
# 출력된 데이터 버전을 확인한 뒤 2-class 데이터셋을 생성
python derive_two_class_dataset.py --source-dataset data/datasets/dataset_v001 --target-config config_2class_tiny.yaml --target-version dataset_v001
python train.py --config config_2class_tiny.yaml --dataset-version dataset_v001 --experiment-name portfolio_tiny --width 640 --height 480
```

학습·내보내기 세부 CLI는 [원본 사용 안내](https://github.com/kingwooseok/2026ESWContest_mobility_ACE/blob/5148804/model_training/README.md)를 따릅니다. 버전 이름은 예시이며 실제 준비한 데이터 버전으로 바꿉니다.

## 평가 해석

전체 12,873프레임의 유무 판정은 TP 4,560 / FN 111 / FP 96 / TN 8,106입니다. 정답률은 `(TP+TN)/전체 = 98.39%`, 대상 검출률은 `TP/(TP+FN) = 97.62%`, 부재 시 오탐률은 `FP/(FP+TN) = 1.17%`입니다.

전체 묶음은 임계값 선정에도 사용됐으므로 독립 최종 시험 점수로 주장하지 않습니다. 저장소가 별도로 보고한 test 1,633프레임의 유무 정답률은 98.65%입니다. 이 값은 BBox 위치의 mAP와 다른 지표입니다. 실제 도로·다른 차종·야간·우천으로 일반화하려면 별도 수집과 평가가 필요합니다.

[설정 파일](https://github.com/kingwooseok/2026ESWContest_mobility_ACE/blob/5148804/model_training/config_2class_tiny.yaml) · [평가 CSV](https://github.com/kingwooseok/2026ESWContest_mobility_ACE/blob/5148804/docs/measurements/ai-evaluation/selected_threshold_metrics.csv)
