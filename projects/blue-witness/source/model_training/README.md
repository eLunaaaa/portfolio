# YOLOX-Tiny 학습 — 촬영부터 차량 검출 모델까지

**실제 모형차 카메라로 촬영하고, 차량 박스를 검수해 YOLOX-Tiny를 학습합니다.** 완성한 모델은 ONNX로 내보내 Raspberry Pi의 영상 선별 모듈에 연결합니다.

[![촬영·프레임 추출, 박스 검수, 촬영 묶음별 데이터셋 분리, YOLOX-Tiny 학습·ONNX 배포 흐름](../docs/assets/vision-training-flow.png)](../docs/assets/vision-training-flow.png)

## 시연 목표에 맞춘 차량 학습

테스트베드는 **사고 위치의 대상 차종을 찾아 필요한 관측 영상을 수집하는 흐름**을 검증합니다. 시연 조건과 프레임별 박스 작성·검수 작업량을 고려해 은색 아이오닉 5를 대상 차종으로 정하고, 나머지 차량을 함께 학습했습니다.

| 최종 모델 출력 | 역할 |
|---|---|
| **0 · 은색 아이오닉 5** | 사고 알림의 차량 정보와 연결해 찾는 대상 차종 |
| **1 · 기타 차량** | 팰리세이드 등 주변 차량을 함께 학습 |

제조사별 차종 데이터를 추가하고 사고 알림의 차량 정보와 검출 클래스를 연결하면, 해당 차종 간 협력 증거 수집으로 적용 범위를 넓힐 수 있습니다. [실제 차량으로의 확장성](../README.md#실제-차량으로의-확장성)

## 실제 촬영에서 박스 검수까지

[![은색 아이오닉 5와 주변 차량의 박스를 지정하고 차종·차량 정보를 검수하는 실제 작업 화면](../docs/assets/vision-annotation.png)](../docs/assets/vision-annotation.png)

앞·뒤·옆 시점, 거리 변화, 다른 차에 일부 가려진 장면, 대상 차량이 없는 장면을 수집합니다. 작업자는 **차량별 박스 → 차종·차량 지정 → 승인·저장** 순서로 검수합니다.

| 단계 | 환경 | 결과물 |
|---|---|---|
| 촬영·프레임 추출 | Raspberry Pi 카메라 → Windows | 원본 영상·프레임·출처 기록 |
| 박스 작성·검수 | Windows · 두 작업자가 영상별 분담 | 박스·차종·차량·완료 상태 |
| 데이터셋 분리 | 같은 촬영 세션·원본 계열을 한 묶음으로 배정 | train / validation / test |
| 학습·내보내기 | Apple Silicon Mac · PyTorch MPS | 학습 가중치·ONNX·모델 명세 |

같은 촬영 묶음에서 나온 프레임은 학습·검증·시험 중 한 세트에 묶어 배정합니다. 영상별 출처와 데이터셋 버전을 유지해, 사용한 사진과 모델을 연결합니다.

## 작업 순서

[① Pi 촬영](#1-pi에서-촬영하기) · [② 영상 가져오기](#2-windows로-영상을-가져오기) · [③ 박스 검수](#3-두-사람이-나눠서-박스-그리기) · [④ 전달·학습](#4-결과를-전달하고-학습하기)

## 1. Pi에서 촬영하기

관측차의 카메라를 사용하므로 **추론 서비스를 멈춘 뒤 촬영하고, 끝나면 다시 켭니다.** 아래는 `craft4`에서 640×480 · 30fps · 8초 영상을 촬영하는 예입니다.

<details>
<summary><b>Pi 촬영 명령</b></summary>

먼저 구도와 초점을 확인합니다. 미리보기는 `Ctrl+C`로 닫습니다.

```bash
sudo systemctl stop inference.service
rpicam-vid --width 640 --height 480 --framerate 30 --timeout 0 --fullscreen
```

```bash
mkdir -p "$HOME/vehicle_recordings"
rpicam-vid --width 640 --height 480 --framerate 30 --timeout 8000 \
  --codec libav --libav-format mp4 --libav-video-codec libx264 \
  --bitrate 2500000 --output "$HOME/vehicle_recordings/video01.mp4"
```

다음 영상은 `video02.mp4`처럼 이름을 바꿉니다. 카메라가 거꾸로 설치돼 있으면 미리보기와 촬영 명령 모두에 `--rotation 180`을 추가합니다.

```bash
sudo systemctl start inference.service
```

</details>

앞·뒤·옆에서 보이는 차량, 다른 차에 일부 가려진 차량, 아이오닉 없이 다른 차량만 있는 장면, 차량이 없는 장면을 함께 촬영합니다. **영상 개수와 파일명은 자유롭게 정할 수 있습니다.**

## 2. Windows로 영상을 가져오기

두 사람 모두 저장소를 내려받아 **같은 버전의 `model_training` 폴더**를 사용합니다. 수동 박스 작업에는 학습 가중치나 PyTorch가 필요 없습니다.

<details>
<summary><b>처음 한 번 설치</b></summary>

Python을 설치한 뒤 PowerShell에서 FFmpeg를 설치합니다.

```powershell
winget install --id Gyan.FFmpeg --exact
```

새 PowerShell을 열고 저장소의 `model_training` 폴더에서 실행합니다.

```powershell
python -m pip install numpy opencv-python PyYAML
ffprobe -version
New-Item -ItemType Directory -Force data/incoming
```

</details>

각자 맡은 MP4를 USB 또는 SCP로 `data/incoming`에 넣고 다음 명령을 실행합니다. **YAML에 영상 이름을 하나씩 적을 필요가 없습니다.**

```powershell
scp craft4@192.168.50.4:/home/craft4/vehicle_recordings/video01.mp4 data/incoming/
python register_recordings.py --source-dir data/incoming --review
```

영상 등록 → 사진 추출 → 박스 작업 창 열기가 이어집니다. 한글 파일명과 `.MP4`도 지원하며, 이미 등록한 영상은 건너뜁니다. 기본값은 **5프레임마다 한 장**을 추출하고 완전히 같은 이미지는 하나만 남깁니다.

## 3. 두 사람이 나눠서 박스 그리기

**영상 파일 단위로 작업을 나눕니다.** 작업자 A와 B가 서로 다른 MP4를 각자 등록하면, 프로그램이 원본 영상별 식별자를 붙여 결과를 합칠 때 사진을 구별합니다.

**차량마다 박스 그리기 → 차종·차량 지정 → 승인 → 저장·다음** 순서로 진행합니다.

| 작업 | 조작 |
|---|---|
| 박스 추가 | 빈 영역에서 드래그 |
| 박스 선택·이동·크기 조절 | 박스 클릭 · 내부 드래그 · 모서리 드래그 |
| 차종·실제 차량 지정 | `c` · `v` |
| 선택한 박스 삭제 | **`d`** |
| 모든 박스 승인 | `a` |
| 저장하고 다음 / 이전 | **`Space`** / **`[`** |
| 차량 없는 사진 / 사용하지 않을 사진 | `0` / `9` — 저장하고 다음으로 이동 |
| 저장하고 종료 | `q` 또는 `Esc` |

다시 시작할 때는 아래 명령으로 **미완료 사진부터** 이어갑니다.

```powershell
python review_annotations.py --config config.yaml
```

<details>
<summary><b>숫자 단축키와 고정 박스로 빠르게 작업하기</b></summary>

박스를 선택하고 차량에 맞는 숫자를 누르면 차종·차량 지정, 승인, 저장, 다음 이동을 한 번에 처리합니다. 여러 차량이 있으면 각각 지정하며, 미지정 박스가 남아 있으면 다음으로 넘어가지 않습니다.

| 키 | 지정하는 차량 |
|---|---|
| `1` | 은색 아이오닉 5 |
| `2` | 검은색 팰리세이드 |
| `3` · `4` · `5` | 기타 모형차 1 · 2 · 3 |
| `f` | 선택 박스를 다음 사진에도 같은 위치로 유지 / 해제 |
| `]` | 저장하고 다음 사진 |
| `{` · `}` | 저장하고 100장 이전 · 이후 |
| `n` · `x` | 차량 없음 · 사용 안 함으로 표시하고 현재 사진 유지 |

`f`는 움직이지 않는 차량에 사용합니다. 같은 영상의 미완료 사진에만 전파하며, 다른 영상으로 넘어가거나 뒤로 이동하면 해제됩니다. 이미 완료한 사진과 다른 차량의 박스는 자동으로 덮어쓰지 않습니다. `Space`는 승인 여부를 바꾸지 않고 저장·이동합니다.

</details>

## 4. 결과를 전달하고 학습하기

### 박스 작업자는 `data/work`를 전달

창을 `q`로 닫고 **`data/work` 전체를 작업자 이름의 ZIP으로 압축**합니다. 사진과 박스 정보가 함께 들어 있어, 각자 수정한 `config.yaml`을 따로 주고받을 필요가 없습니다.

```text
data/work/
├── frames/                 사진
├── annotations.jsonl       박스·차종·완료 상태
├── frame_manifest.jsonl    사진의 출처
└── recording_index.jsonl   처리한 영상 목록
```

받는 사람은 ZIP을 `data/received/wooseok/work`처럼 풀고, 박스 작업 창을 닫은 상태에서 합칩니다.

```powershell
python register_recordings.py --merge-work data/received/wooseok/work
```

새 결과는 추가하고 동일 결과는 건너뜁니다. 같은 사진의 확정 박스가 서로 다르면 충돌을 표시하고 기존 결과를 유지합니다. 합친 `data/work`를 학습 담당자에게 전달합니다. **원본 MP4로 모델을 시험할 경우 등록된 `data/recordings`도 함께 전달합니다.**

### 학습 담당자는 Tiny 모델 생성

박스 작업은 차량 정보를 보존하도록 세 부류로 기록하고, 학습할 때 두 부류로 합칩니다.

| 박스 작업에서 지정 | 최종 모델 출력 |
|---|---|
| `ioniq5_silver` | **0 — 아이오닉 5** |
| `palisade_black` · `other_vehicle` | **1 — 기타 차량** |

촬영·검수는 `config.yaml`, 최종 학습·평가·내보내기는 **`config_2class_tiny.yaml`**을 사용합니다. `config_2class_nano.yaml`은 과거 비교 실험용입니다.

<details>
<summary><b>학습 환경 준비 — Apple Silicon Mac</b></summary>

```bash
cd 2026ESWContest_mobility_ACE/model_training
brew install ffmpeg
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

공유받은 공식 사전학습 가중치 `yolox_tiny.pth`를 `models/pretrained/`에 넣습니다. 파일은 설정의 SHA-256으로 검증합니다. `train.py`는 이 가중치에서 **새 학습을 시작**하므로, 이전에 모형차 데이터로 학습한 모델을 이어 쓸 필요가 없습니다.

학습·평가는 MPS가 있으면 Apple GPU, 없으면 CPU를 사용합니다. 실행 장치는 `runs/<실험명>/environment.json`에 기록됩니다. 포함된 `third_party/YOLOX/`는 공식 0.3.0 소스에 MPS 호환 패치를 적용한 버전입니다.

</details>

<details>
<summary><b>① 데이터셋 확정 → ② 두 클래스 변환 → ③ 학습</b></summary>

```bash
.venv/bin/python export_coco.py --config config.yaml
.venv/bin/python validate_dataset.py --config config.yaml
```

명령이 출력한 데이터셋 이름을 `SOURCE_DATASET_VERSION`에 넣습니다. 변환 결과와 실험 이름은 새 이름을 사용합니다.

```bash
SOURCE_DATASET_VERSION="dataset_v001"
TWO_CLASS_DATASET_VERSION="dataset_v001"
EXPERIMENT_NAME="tiny_2class_640x480_v001"

.venv/bin/python derive_two_class_dataset.py \
  --source-dataset "data/datasets/$SOURCE_DATASET_VERSION" \
  --target-config config_2class_tiny.yaml \
  --target-version "$TWO_CLASS_DATASET_VERSION"

.venv/bin/python validate_dataset.py --config config_2class_tiny.yaml \
  --dataset-dir "data/datasets_2class/$TWO_CLASS_DATASET_VERSION"

.venv/bin/python train.py --config config_2class_tiny.yaml \
  --dataset-version "$TWO_CLASS_DATASET_VERSION" \
  --experiment-name "$EXPERIMENT_NAME" --width 640 --height 480 \
  --epochs 100 --batch-size 8
```

결과는 `runs/$EXPERIMENT_NAME/`에 저장됩니다. `checkpoints/best.pth`가 선택된 모델이며, `last.pth`, 학습 로그, 사용 설정, 실행 환경도 함께 남습니다.

짧게 실행을 점검하려면 새 실험 이름에 `--epochs 1 --sanity-image-count 32`를 사용합니다. 같은 촬영에서 나온 이미지는 하나의 train/val/test 그룹에 묶이며, 데이터셋을 다시 만들면 새 버전으로 저장됩니다.

</details>

<details>
<summary><b>④ 모델 성능 확인 → ⑤ ONNX로 내보내기</b></summary>

앞 단계에서 지정한 변수 값을 같은 터미널에서 사용합니다. 평가·배포 Confidence는 **0.60**입니다. `export_onnx.py`는 C++ 추론기와 같은 [`ai/inference_config.h`](../ai/inference_config.h)를 읽어 Confidence·NMS IoU·6프레임 추론 간격을 `model_manifest.json`에 기록합니다. 명시한 Confidence도 공통 설정과 일치하는지 검사합니다. [임계값 선정 근거와 검증 결과](../../analysis/measurements/ai-evaluation/README.md)

```bash
.venv/bin/python evaluate.py --config config_2class_tiny.yaml \
  --dataset-version "$TWO_CLASS_DATASET_VERSION" \
  --checkpoint "runs/$EXPERIMENT_NAME/checkpoints/best.pth" \
  --split val --confidence-threshold 0.60 --input-source jpeg \
  --width 640 --height 480 \
  --output "runs/$EXPERIMENT_NAME/metrics/validation.json"

.venv/bin/python export_onnx.py --config config_2class_tiny.yaml \
  --dataset-version "$TWO_CLASS_DATASET_VERSION" \
  --checkpoint "runs/$EXPERIMENT_NAME/checkpoints/best.pth" \
  --width 640 --height 480 \
  --evaluation "runs/$EXPERIMENT_NAME/metrics/validation.json" \
  --confidence-threshold 0.60 \
  --output-dir "runs/$EXPERIMENT_NAME/exports/release"
```

시험용 원본 영상도 준비했다면 `evaluate.py`의 `--split test --input-source mp4`로 영상 기준 결과를 저장할 수 있습니다. JPEG 기준 시험은 `--input-source jpeg`를 사용합니다.

배포 결과는 `vehicle_detector.onnx`와 `model_manifest.json`입니다. 모델은 batch 1 · FP32 · BGR/NCHW 입력, 114로 채우는 top-left letterbox, 정규화 없는 0~255 입력, NMS 전 YOLOX 출력 형식을 사용합니다. [AI 모듈](../ai/README.md)의 모델 경로와 입력 계약에 맞춰 배포합니다.

</details>

## 설정과 추가 도구

| 바꾸려는 항목 | 위치 |
|---|---|
| 이미지 추출 간격 | `config.yaml` → `frame_extraction.frame_interval` (기본 5) |
| 촬영 형식 | `config.yaml` → `encoding_profiles` |
| 자동 박스 초안 모델 | `config.yaml` → `proposal_detector` |
| 학습 횟수·배치·증강 | `config_2class_tiny.yaml` → `training` |
| 학습 입력 크기 | 640×480 · 512×384 · 416×320 중 선택 |

<details>
<summary><b>촬영 계획, 자동 박스 초안, 출력 비교</b></summary>

| 도구 | 용도 |
|---|---|
| `register_recordings.py --plan <계획명> --source-dir <폴더>` | `capture_plans.yaml`의 촬영 조건을 적용해 등록 (`--dry-run`으로 먼저 검사) |
| `register_recordings.py --source-dir <폴더> --session-id <묶음명>` | 같은 촬영 묶음을 이름으로 관리 |
| `sync_recordings.py --help` | Pi에서 촬영 정보와 함께 영상을 직접 가져오기 |
| `prepare_dataset.py --config config.yaml --manual` | 등록만 해둔 영상에서 수동 작업용 사진 추출 |
| `prepare_dataset.py --config config.yaml` | 설정한 Tiny ONNX로 새 사진의 박스 초안 생성 |
| `compare_outputs.py --help` | 같은 이미지에서 PyTorch와 ONNX의 출력 비교 |

자동 초안에는 `models/pretrained/vehicle_detector_2class_tiny.onnx`가 필요합니다. 모델이 못 찾은 차량도 사람이 박스를 추가하며, 이미 등록된 사진에는 초안을 다시 계산하지 않습니다. 원본 차량이 여러 후보로 묶인 `other_vehicle` 박스는 차종·차량을 직접 지정합니다.

</details>

## 작업이 막힐 때

| 증상 | 처리 |
|---|---|
| 검수할 프레임이 없음 | `register_recordings.py --source-dir data/incoming --review`로 등록·추출 |
| 사진 수가 예상보다 적음 | 기본 5프레임 간격 추출과 동일 이미지 제거 적용 여부 확인 |
| 내보내기에 미완료 사진 오류 | 남은 사진을 승인 · 차량 없음 · 사용 안 함으로 처리 |
| `primary_2_5mbps`가 모든 split에 없다는 오류 | 독립된 2.5Mbps 촬영 묶음을 추가 |
| 기존 실험 이름 오류 | 새 `--experiment-name` 지정 |
| 모델·YOLOX 파일 해시 오류 | 해당 버전에 맞는 사전학습 파일과 저장소 소스 사용 |

[프로젝트 소개](../README.md) · [Pi에서 AI 실행](../ai/README.md)
