# 이민석 · 임베디드 시스템 포트폴리오

**C/C++·Python을 사용해 Linux 기반 임베디드 시스템을 개발합니다.** 센서·통신·실시간 제어부터 AI 학습, 서버·웹, 빌드·배포 자동화까지 연결해 구현했습니다.

팀 프로젝트에서는 애자일 방식으로 기능을 나누어 개발하고, 통합·시험 결과를 다음 개선에 반영했습니다. 각 프로젝트에서 **개인 담당 → 설계 선택 → 구현 코드 → 검증 결과**를 확인할 수 있도록 정리했습니다.

[![Portfolio verification](https://github.com/eLunaaaa/portfolio/actions/workflows/portfolio-ci.yml/badge.svg)](https://github.com/eLunaaaa/portfolio/actions/workflows/portfolio-ci.yml)

## 개발 역량과 협업 방식

| 영역 | 실제 개발에서의 활용 |
|---|---|
| **주 사용 언어 · C/C++** | MCU 레지스터·인터럽트·타이머 제어, 통신 프로토콜, 작업 큐·스레드·IPC, Linux 서비스 구현 |
| **주 사용 언어 · Python** | 데이터 준비·AI 학습·평가, 서버 API, 실험·검증 도구, 코드 생성과 개발 자동화 |
| **주 개발 환경 · Linux** | Raspberry Pi 서비스·프로세스 운영, POSIX socket·메모리 처리, Chrony·systemd 연동, PREEMPT_RT 실험 환경 구성 |
| **개발 방식 · Agile** | 기능을 구현·검증 가능한 단위로 나누고, 동작하는 결과를 통합·시험하면서 요구사항과 구현을 반복 개선 |
| **팀 소통 · Slack** | 진행 상황·연동 이슈·실험 결과를 공유하고 모듈 간 인터페이스와 변경 사항을 협의 |
| **팀 개발 · Git/GitHub** | 기능·수정 내용을 커밋으로 기록하고 팀 코드를 병합·통합. 개인 담당 범위와 협업 변경을 기여 이력으로 구분 |
| **일정 관리 · Trello** | 개발 작업의 우선순위·일정·진행 상태를 관리하고, 구현·통합 과정에서 발견한 후속 작업을 추적 |
| **CI/CD · GitHub Actions** | 자동 검사·빌드와 검증 산출물을 연결. Blue Witness에서는 변경 영향별 검사·ARM64 빌드와 수동 Pi 배포 흐름을 설계·구축 |

팀 개발의 흐름은 **기능 분할·일정 관리 → 협의·구현 → Git 통합 → 자동 검사 → 시스템 시험 → 다음 개선**으로 연결했습니다. 협업 도구를 사용하는 데서 끝내지 않고, 모듈의 책임·통신 계약·오류 복구와 검사 기준을 코드에 반영했습니다.

구체적인 예로 Blue Witness에서는 [모듈 경계와 설계 선택](projects/blue-witness/docs/design/DECISIONS.md), [영상 보존·장애 복구](projects/blue-witness/docs/FAILSAFE.md), [실제 팀 CI/CD 코드](projects/blue-witness/source/automation/README.md)를 연결했습니다. 이 포트폴리오 자체도 [GitHub Actions](https://github.com/eLunaaaa/portfolio/actions/workflows/portfolio-ci.yml)에서 포함된 코드를 검사합니다.

## 프로젝트

| 순서 | 프로젝트 | 개인 담당과 핵심 구현 | 확인 자료 |
|---:|---|---|---|
| **1** | [Blue Witness · 다중 시점 사고 영상 수집](projects/blue-witness/README.md) | AI 학습·배포, 서버·웹, 시간 동기화 상태 서비스, 전체 Fail-safe·Uploader 설계, CI/CD 설계·구축 | [설계 선택](projects/blue-witness/docs/design/DECISIONS.md) · [전체 소스 안내](projects/blue-witness/source/README.md) · [측정 자료](projects/blue-witness/docs/EVIDENCE.md) |
| **2** | [CNN 학습·RTL 자동화](projects/configurable-cnn-rtl/README.md) | **전체 개인 구현.** JSON 모델 구성, PyTorch 학습, Q3.4 평가, Verilog 생성·시뮬레이션·합성 자동화 | [저장 가중치·RTL 비교](projects/configurable-cnn-rtl/tests/check_fixed_point.py) · [코드 안내](projects/configurable-cnn-rtl/docs/CODE_GUIDE.md) |
| **3** | [엣지 AI 쓰레기 분류](projects/edge-ai-trash-sorter/README.md) | 시스템 설계·통합, Zybo 카메라·AI·FreeRTOS, 적재량·Wi-Fi, Flask 서버·대시보드 | [시스템 구조](projects/edge-ai-trash-sorter/docs/ARCHITECTURE.md) · [검수 기록](projects/edge-ai-trash-sorter/docs/CODE_REVIEW.md) |
| **4** | [Arduino 동적 부하 분산](projects/arduino-load-balancer/README.md) | Master 스케줄링, I²C·완료 인터럽트, Slave 합성곱·큐, PWM·UART·타이머 | [통신 규격](projects/arduino-load-balancer/docs/PROTOCOL.md) · [검수 기록](projects/arduino-load-balancer/docs/CODE_REVIEW.md) |
| **5** | [실시간 Linux 실험 환경](projects/embedded-linux-system-programming/README.md) | UDP 기준 송수신 코드, PREEMPT_RT 도구 설정, CPU 3 고정, 논문 리딩·정리 | [C 코드](projects/embedded-linux-system-programming/source/README.md) · [측정 설계](projects/embedded-linux-system-programming/docs/DESIGN.md) |
| **6** | [뉴스·물리 버튼 기반 주식 주문](projects/stockauto-news-trader/README.md) | 뉴스·RSI 판단, 키움 연동, 주문·체결 원장, UNO LCD·승인 버튼·부저 | [구조와 상태 관리](projects/stockauto-news-trader/docs/ARCHITECTURE.md) · [검수 기록](projects/stockauto-news-trader/docs/CODE_REVIEW.md) |
| **7** | [스마트 우산꽂이](projects/smart-umbrella-stand/README.md) | 아이디어, STM32 Master·SPI, Slave2 날씨·LCD·상태 표시 | [구조·시연](projects/smart-umbrella-stand/README.md) · [검수 기록](projects/smart-umbrella-stand/docs/CODE_REVIEW.md) |

## 대표 구현과 검증

### 1. Blue Witness · 임베디드·AI·서버·자동화 통합

Raspberry Pi 차량들이 사고 전후 영상을 확보하고, AI로 목격 영상을 선별해 서버에서 시간에 맞춰 병합하는 팀 프로젝트입니다. 2026 임베디드 소프트웨어 경진대회 본선 참가 프로젝트입니다.

- **설계·구현:** 촬영 세션별 데이터 분리, YOLOX-Tiny 학습·ONNX 계약 검증, 영속 업로드 큐·저장 ACK, 지연 영상 재병합, 시간 동기화 IPC·스레드 진행 감시.
- **협업·자동화:** Slack·Git·Trello를 활용한 팀 개발과 CI/CD 설계·구축. 검증한 산출물을 수동 장치 배포로 연결했습니다.
- **확인 자료:** [담당 모듈과 협업 기여](projects/blue-witness/docs/CODE_GUIDE.md) · [설계 결정](projects/blue-witness/docs/design/DECISIONS.md) · [평가 CSV·분석](projects/blue-witness/analysis/README.md).

업로더는 개인 설계·팀 구현으로, 카메라·추론 런타임과 공통 IPC는 팀 구현 및 개인 학습·배포 연동으로 표시했습니다. 담당 모듈·공통 의존성·테스트를 이 저장소에 함께 포함했습니다.

### 2. CNN·RTL · 모델에서 정수 연산 회로까지 전체 구현

JSON으로 정의한 CNN을 PyTorch 모델과 Verilog로 구성하고 학습·양자화·시뮬레이션·합성을 연결했습니다. 모델 구성부터 자동화까지 전체를 직접 구현했습니다.

- **설계·구현:** 계층별 형상 검증, Conv·ReLU·Pool·FC, signed Q3.4 연산, RTL 생성과 합성 통계 분석.
- **검증:** 저장된 baseline 가중치와 MNIST 입력 10개로 Python 정수 계산과 RTL의 클래스 점수 **100개 일치**를 확인합니다.
- **확인 자료:** [프로젝트 상세](projects/configurable-cnn-rtl/README.md) · [가중치·정수·RTL 검사](projects/configurable-cnn-rtl/tests/check_fixed_point.py).

### 3. 엣지 AI 쓰레기 분류 · RTOS·카메라·통신·웹 연동

Zybo의 카메라 촬영·AI 분류와 Arduino 제어, 적재량 전송·웹 대시보드를 연결한 팀 프로젝트입니다.

- **설계·구현:** 촬영·추론·UART의 작업 흐름, FreeRTOS 태스크·큐·세마포어, MobileNetV2 학습·TFLite 변환, 초음파·ESP8266·Flask 연동.
- **검증:** 실보드 시스템 연동·시연 자료와 Arduino 컴파일, C 코어·서버 API 검사를 연결했습니다.
- **확인 자료:** [개인 담당·구현·시연](projects/edge-ai-trash-sorter/README.md) · [시스템 구조](projects/edge-ai-trash-sorter/docs/ARCHITECTURE.md).

## 코드 확인과 자동 검사

각 프로젝트의 README에서 담당 범위·파일 역할·실행 순서를 안내합니다. 주요 함수에는 입력·출력·처리 구조와 설계 이유를 한글 주석으로 설명했습니다.

[포트폴리오 GitHub Actions](https://github.com/eLunaaaa/portfolio/actions/workflows/portfolio-ci.yml)는 서버·웹 회귀, Linux C 서비스 빌드, 모델 파이프라인, CNN 정수·RTL 일치, 제어 코어·문서 링크를 검사합니다. 주식 주문 프로젝트는 Windows·Linux의 Python **89개**, Qt 통합 **3개**, 펌웨어·통신 검사와 UNO 빌드를 포함합니다. [검사 정의](.github/workflows/portfolio-ci.yml)
