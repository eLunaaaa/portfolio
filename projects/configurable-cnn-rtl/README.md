# Configurable CNN Training and RTL

MNIST 숫자 분류 CNN을 JSON으로 구성하고, 모델 학습과 Q3.4 출력 평가, Verilog 연산 모듈 생성 및 합성 분석을 연결한 프로젝트입니다. 모델 구조·학습, Q3.4 변환·평가, Verilog 연산 모듈, RTL 생성·합성·분석 자동화를 모두 직접 구현했습니다.
<!-- LOC:START -->
## 코드 규모와 기술적 과제

**1,726 LOC · 21개 파일** · 구현 790 / 테스트·예제 911 / 빌드·CI·설정 25.

주석·빈 줄·외부 코드·자동 생성 파일을 제외한 프로젝트 규모입니다. [언어별·범주별 분석](docs/LOC.md)

- **기술적 과제:** 학습 모델의 형상·정수 계산을 Verilog 회로와 일치시키기.
- **설계 선택:** 공통 JSON·채널 우선 flatten·signed Q3.4 정렬·포화 규칙을 명시.
- **확인 근거:** MNIST 입력 10개·클래스 점수 100개 정수/RTL 일치. [문제·해결·검증](../../docs/TECHNICAL_CHALLENGES.md#configurable-cnn-rtl)
<!-- LOC:END -->


## 동작 흐름

15×15 단일 채널 이미지 → 합성곱 → ReLU → Max Pooling → 채널 우선 Flatten → 완전연결 → 숫자 0~9의 점수입니다. JSON의 계층 목록에서 PyTorch 모델과 RTL의 구조를 함께 구성합니다.

Python에서는 실수 파라미터를 학습한 뒤 Conv·Linear 출력에 Q3.4 범위 제한과 1/16 단위 반올림을 적용하여 평가합니다. RTL에서는 signed Q3.4 입력과 계수를 곱하고 소수점 위치를 맞춰 누적한 뒤 8비트 범위로 포화합니다. RTL의 가중치와 bias는 입력 포트로 제공합니다.

## 개인 구현

- 모델 구조를 JSON으로 정의하고 MNIST 전처리·Adam 학습·정확도 평가·가중치 저장을 구현했습니다.
- Q3.4 변환 계층과 평가 모드를 구성했습니다.
- Conv, ReLU, Max Pool, FC 모듈과 동작 검증 테스트벤치를 구현했습니다.
- 계층별 형상 계산, top RTL 생성, sv2v 변환, Yosys 합성, 통계 추출을 자동화했습니다.

## 저장된 학습 평가 기록

| 설정 | 구조 | Q3.4 출력 평가 정확도 |
|---|---|---:|
| baseline | Conv(1→2) · ReLU · Pool · FC(72→10) | 85.99% |
| test_config | Conv(1→56→56) · Pool · Conv(56→116→116) · FC(116→10) | 97.48% |

가중치와 평가 기록은 [data/weights](data/weights)에 있습니다. `two_convs.json`은 두 합성곱 계층 구성을 비교하기 위한 설정입니다.

## 저장 가중치와 RTL의 정수 연산 일치

[정수 기준 모델](python/fixed_point.py)은 입력·가중치·bias를 signed Q3.4로 변환하고, 곱셈마다 산술 우측 shift 후 누적·포화하는 RTL 규칙을 구현합니다. 저장된 `baseline.pth`의 SHA-256과 양자화 계수를 검사한 뒤, MNIST test 입력 10개의 클래스 점수 100개를 [테스트벤치](testbench/tb_fixed_point.v)와 비교합니다.

**결과: 100/100 byte 일치.** [검사 코드](tests/check_fixed_point.py) · [입력·계수·정답 벡터](tests/vectors/baseline.mem) · [전처리·출처](tests/vectors/baseline.json)

위 표의 97.48%는 실수 가중치를 사용한 계층 출력 양자화 평가 기록입니다. 정수 가중치 RTL 검사는 baseline 모델의 연산 일치 여부를 확인합니다.

## 파일 안내

| 파일·폴더 | 역할 |
|---|---|
| [python/build_model.py](python/build_model.py) | JSON 모델 구성·Q3.4 출력 변환 |
| [python/train_cnn.py](python/train_cnn.py) | MNIST 학습·평가·가중치 저장 |
| [python/config_utils.py](python/config_utils.py) | 입력 크기·채널·특징 수 검증 |
| [python/generate_cnn_top.py](python/generate_cnn_top.py) | 데이터 배열과 시작·완료 신호 연결 생성 |
| [rtl](rtl) | Conv·ReLU·Pool·FC 및 baseline top |
| [testbench](testbench) | 연산별 시뮬레이션 |
| [python/evaluate_design.py](python/evaluate_design.py) | 학습·변환·합성 실행 조정 |
| [python/analyze_synthesis.py](python/analyze_synthesis.py) | 최종 셀 통계와 가중치 기반 비교 지표 |

## 실행

Python 환경에 `pip install -r requirements.txt`로 의존성을 설치하고, Icarus Verilog·sv2v·Yosys를 준비합니다. 이 폴더에서 실행합니다.

```sh
make test
make generate CONFIG=configs/baseline.json
make train CONFIG=configs/baseline.json
python3 python/evaluate_design.py configs/baseline.json --skip-training
```

`make test`는 설정 검증, 모델·가중치 로딩, 순전파·역전파, Q3.4 변환, RTL 연산과 top 연결, 저장 가중치 기반 정수·RTL 일치를 확인합니다. `--skip-training`은 저장된 학습 기록을 사용합니다. 합성 분석의 면적·전력 수치는 셀별 고정 가중치를 합산한 설계 비교 지표입니다.

[코드 검토와 검증](docs/CODE_REVIEW.md) · [코드 읽는 순서](docs/CODE_GUIDE.md)
