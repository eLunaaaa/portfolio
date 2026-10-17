# 이민석 · 임베디드 시스템 포트폴리오

센서·통신·실시간 제어부터 AI 학습, Linux 서비스와 웹 서버까지 구현했습니다. 프로젝트별로 **담당 역할 → 설계 선택 → 코드 → 검증 자료**를 연결했습니다.

[![Portfolio verification](https://github.com/eLunaaaa/portfolio/actions/workflows/portfolio-ci.yml/badge.svg)](https://github.com/eLunaaaa/portfolio/actions/workflows/portfolio-ci.yml)

## 대표 프로젝트

### [Blue Witness · 다중 시점 사고 영상 수집](projects/blue-witness/README.md)

Raspberry Pi 차량들이 사고 전후 영상을 확보하고, AI로 목격 영상을 선별해 서버에서 시간에 맞춰 병합하는 팀 프로젝트입니다. 2026 임베디드 소프트웨어 경진대회 본선 참가 프로젝트입니다.

- **개인 담당:** 차량 검출 모델 학습·배포, FastAPI 서버·웹, Chrony 상태 IPC, 전체 Fail-safe·Uploader 설계, CI/CD 설계·구축.
- **주요 구현:** 촬영 세션별 데이터 분리, YOLOX-Tiny 학습·ONNX 계약 검증, 영속 업로드 큐와 저장 ACK, 지연 영상 재병합, 스레드 진행 감시·재시작 복구.
- **확인 자료:** [담당 모듈 전체 소스](projects/blue-witness/source/README.md) · [설계 선택](projects/blue-witness/docs/design/DECISIONS.md) · [평가 CSV·분석](projects/blue-witness/analysis/README.md).

원본 팀 저장소에 접속하지 않아도 이 저장소 안에서 코드·테스트·측정 자료를 읽을 수 있습니다. 업로더와 AI·공통 IPC의 협업 구현도 포함하며, 개인 설계와 팀 구현 역할은 [기여 안내](projects/blue-witness/docs/CODE_GUIDE.md)에 표시했습니다.

### [CNN 학습·RTL 자동화](projects/configurable-cnn-rtl/README.md)

JSON으로 정의한 CNN을 PyTorch 모델과 Verilog로 구성하고 학습·양자화·시뮬레이션·합성을 연결했습니다. **전체 개인 구현**입니다.

- **기술:** Python · PyTorch · signed Q3.4 · Verilog · Icarus Verilog · Yosys.
- **검증:** 저장된 baseline 가중치와 MNIST 입력 10개를 사용해 Python 정수 계산과 RTL의 클래스 점수 **100개 일치**를 확인합니다.
- **학습 기록:** Conv·Linear 출력에 Q3.4 변환을 적용한 소프트웨어 평가 85.99% → 97.48%. [가중치·RTL 검증](projects/configurable-cnn-rtl/tests/check_fixed_point.py).

## 추가 프로젝트

| 프로젝트 | 개인 담당 | 구현·검증 자료 |
|---|---|---|
| [뉴스·물리 버튼 기반 주식 주문](projects/stockauto-news-trader/README.md) | 키움 연동, 뉴스·RSI 분석, 주문 상태·청산, UNO LCD·버튼·부저 | [코드 안내](projects/stockauto-news-trader/docs/CODE_GUIDE.md) · Python 88개·Qt 3개 · 실제 펌웨어·UNO 빌드 |
| [실시간 Linux 실험 환경](projects/embedded-linux-system-programming/README.md) | UDP 기준 송수신 구현, PREEMPT_RT 도구 설정, CPU 3 고정, 논문 리딩 | [C 코드](projects/embedded-linux-system-programming/source/README.md) · [측정 설계](projects/embedded-linux-system-programming/docs/DESIGN.md) |
| [Arduino 동적 부하 분산](projects/arduino-load-balancer/README.md) | Master 스케줄링, I²C·인터럽트, Slave 합성곱·큐, PWM·UART·타이머 | 시연 지연 포함 측정 234초 → 80.5초 · [통신 규격](projects/arduino-load-balancer/docs/PROTOCOL.md) |
| [엣지 AI 쓰레기 분류](projects/edge-ai-trash-sorter/README.md) | 시스템·카메라·AI·FreeRTOS, 적재량·Wi-Fi, Flask·웹 | [시스템 구조](projects/edge-ai-trash-sorter/docs/ARCHITECTURE.md) · Arduino·C·API 검사 |
| [스마트 우산꽂이](projects/smart-umbrella-stand/README.md) | 아이디어, STM32 Master·SPI, Slave2 날씨·LCD·상태 표시 | [구조·시연](projects/smart-umbrella-stand/README.md) · 센서 처리 2,264 → 1,171 cycles |

## 코드 확인과 자동 검사

각 프로젝트의 README에서 파일 역할과 실행 순서를 안내합니다. 주요 함수에는 입력·출력·처리 구조와 설계 이유를 한글 주석으로 설명했습니다.

[GitHub Actions](https://github.com/eLunaaaa/portfolio/actions/workflows/portfolio-ci.yml)는 서버·웹 회귀, Uploader·Time Sync Linux 빌드, 모델 파이프라인, CNN 정수·RTL 일치, 하드웨어 독립 제어 코어와 문서 링크를 검사하고, 주식 주문 프로젝트의 Python·펌웨어·UNO 빌드도 확인합니다. [검사 정의](.github/workflows/portfolio-ci.yml)
