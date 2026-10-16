# 모델 학습 모듈 — 실장치 조건을 학습·평가·배포까지 유지하기

[설계 선택 요약](DECISIONS.md) · [학습 소스](../../source/model_training) · [모델 학습 개요](../../ml/README.md)

## 문제와 제약

목표는 일반 자동차 사진 분류가 아니라 낮은 카메라 높이의 모형차 영상에서 대상 차종이 보이는지를 판단하는 것입니다. 촬영 각도·거리·가림·압축·부재 장면을 포함해야 하고, Mac 학습 결과를 Pi C++ 추론에서 같은 의미로 실행해야 했습니다.

## 선택 1. 새로운 모델 구조보다 기존 YOLOX-Tiny fine-tuning

직접 검출기를 설계하면 비교·학습·배포 비용이 커지고, 기존 범용 모델만 쓰면 모형차 도메인 차이가 남습니다. 공식 사전학습 가중치를 출발점으로 실제 촬영 데이터에 맞춰 Tiny를 학습하는 흐름을 택했습니다. Nano는 비교 설정으로 남기고 최종 배포는 Tiny로 구분합니다. 모든 환경에서 Tiny가 가장 우수하다고 실측한 주장은 아닙니다.

최종 출력은 아이오닉 target / 기타 차량의 2-class입니다. 검수 정보는 팰리세이드와 기타 실제 차량 구분을 유지하고, `derive_two_class_dataset.py`에서 학습용 class로 변환합니다. 라벨 단계부터 합쳐 버리는 방식보다 원래 정보를 보존하고, 출력 class 수는 시연 목표와 검수 작업량에 맞춥니다. 비용은 개별 차량·번호 식별 능력이 없다는 점입니다.

## 선택 2. 자동 초안과 사람의 정답 확정 분리

자동 proposal을 정답으로 바로 학습하지 않습니다. `review_annotations.py`에서 박스·차종·차량·승인 상태를 관리하고 부재·사용 제외 상태도 구분합니다. 검수 비용을 없애는 대신 데이터 오류가 결과를 오염시키는 위험을 줄이는 선택입니다.

`register_recordings.py`는 영상별 식별자·출처를 남기고, 작업자는 서로 다른 영상 단위로 분담합니다. 병합 시 같은 결과는 건너뛰고 다른 확정 라벨은 충돌로 남깁니다. ‘마지막 작업자가 덮어쓰기’보다 느리지만 조용한 라벨 손실을 피합니다.

## 선택 3. 프레임 수 비율보다 촬영 계열 독립성

한 영상에서 추출한 비슷한 프레임을 무작위 train/test로 나누면 배경·시점이 중복됩니다. 원본·세션 계열 단위로 split을 배정하고 manifest로 추적합니다. 목표 비율은 70/15/15이지만 정확한 프레임 수 일치보다 계열 분리를 우선합니다. split 크기와 class 균형이 완전히 같아지지 않는 비용이 있습니다.

`export_coco.py` → `validate_dataset.py` → `derive_two_class_dataset.py` 순서로 자료 구조·class·좌표와 분리를 확인합니다. 원본, 검수 작업 디렉터리, 버전별 데이터셋, 실험 디렉터리를 분리해 재실행이 기존 결과를 조용히 바꾸지 않게 합니다.

## 선택 4. 데이터 복제 대신 학습 sampling 가중치

로컬 `create_training_loader()`는 COCO image ID 순서와 촬영 session을 연결해 `WeightedRandomSampler`의 가중치를 구성합니다. epoch당 전체 train 이미지 수만큼 replacement sampling하고, 설정이 없으면 기존 shuffle을 사용합니다. 실제 장착·디오라마·취약 장면의 학습 노출을 3배·6배로 조절할 수 있지만, 모든 항목이 정확히 그 배수로 한 epoch에 등장한다는 뜻은 아닙니다.

파일을 물리적으로 여러 번 복제하는 대안보다 저장 중복을 줄이고 validation/test 분포를 그대로 둡니다. 대가로 일부 이미지가 반복·누락될 수 있어 class·session 노출과 실제 평가 결과를 함께 봐야 합니다. 기존 test 프레임을 가중치만 줘서 학습에 섞는 설계가 아닙니다.

## 선택 5. parent checkpoint를 검증한 미세조정

`training.initial_checkpoint`가 있으면 파일 SHA·class 순서·입력 크기를 확인한 뒤 불러옵니다. 파일 이름만 보고 맞는 모델로 가정하지 않습니다. 전이 정보를 실험 기록에 남기고, 일반 pretrained 시작과 parent 모델 미세조정을 구분합니다.

로컬 블러 검수 설정은 5 epochs·batch 8·작은 learning rate·warmup 0·backbone 5 epochs 동결을 사용합니다. `freeze_backbone_batch_norm`으로 고정 backbone의 running 통계까지 변하는 것을 막습니다. 전체를 재학습하는 대안보다 기존 특징 보존과 짧은 조정을 선택한 것이지만, 새로운 도메인에 backbone 적응이 충분하지 않을 수 있습니다.

## 선택 6. 환경에 맞는 MPS와 실험 추적

`train.py`는 MPS가 있으면 사용하고 그렇지 않으면 CPU로 실행할 수 있습니다. 실행 장치, seed, 환경, 데이터 hash, checkpoint hash, Git commit을 남깁니다. 단일 seed를 고정해도 모든 장치에서 bitwise 동일 결과가 보장되는 것은 아닙니다.

설정은 batch 8·warmup 5·SGD 등이며 CLI에 해상도와 실험 이름을 명시합니다. 설정상 100 epochs는 실제 선택 모델의 학습 이력을 뜻하지 않습니다. 배포 평가 자료의 선택 checkpoint는 epoch 1로 별도 기록합니다.

## 선택 7. 내보내기를 파일 변환으로 끝내지 않기

`export_onnx.py`와 `compare_outputs.py`를 나눕니다. 입력은 640×480 BGR FP32 NCHW, 0~255, top-left letterbox 114이며 `/255`를 하지 않습니다. graph 밖에서 grid decode·class별 NMS를 수행합니다.

input/output name·shape·dtype, Confidence·NMS를 장치와 맞춰야 합니다. 로컬 export는 동일 dataset·해상도의 validation 평가만 연결하고 추천값 또는 `--confidence-threshold`를 기록합니다. ONNX checker는 graph 형식, 출력 비교는 같은 입력에서 수치 차이, manifest는 사용 파일 동일성을 확인합니다. 각각의 검사가 서로를 대신하지 않습니다. 로컬 export는 팀 공통 header를 자동으로 읽지 않으므로, 배포할 때 manifest와 장치 설정을 별도로 대조해야 합니다.

## 실패와 복구

| 실패 | 선택한 대응 |
|---|---|
| 영상 재등록 | 출처·해시로 중복 확인 |
| 확정 라벨 충돌 | 자동 덮어쓰지 않고 사용자 확인 |
| 잘못된 class·좌표·누락 파일 | 학습 전 validation에서 차단 |
| 다른 pretrained·패치된 YOLOX | 설정 hash·commit 검사로 조건 불일치 확인 |
| export 평가 조건 불일치 | validation split·dataset SHA·해상도를 검사하고 threshold 명시·추천 출처 기록 |
| 학습 중단 | 기존 실험 결과·manifest 보존. 임의 checkpoint 자동 resume를 주장하지 않음 |

## 검증과 한계

통합본의 모델 파이프라인·런타임 계약 44개를 검사합니다. CI에서는 포함된 YOLOX 소스로 임시 checkpoint를 생성하므로 개인 개발 경로나 비공개 가중치 없이 검사를 실행할 수 있습니다. 프레임 평가 수치는 당시 팀 모델 평가 자료로 연결합니다.
