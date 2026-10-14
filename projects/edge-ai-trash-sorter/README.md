# Edge AI Trash Sorter

**저전력 엣지 AI 기반 쓰레기 분류 시스템** — Zybo Z7-20의 ARM Cortex-A9에서 카메라 영상을 분류하고, Arduino가 해당 쓰레기통 뚜껑을 여는 임베디드 팀 프로젝트입니다. 별도 Arduino와 ESP8266은 적재 상태를 Flask 서버로 보내 웹 대시보드에 표시합니다.

> 임베디드시스템설계 Project 3 · 전자공학과 10조

![스마트 쓰레기 분류 시제품](media/prototype.png)

## 제출용 프로젝트 요약

| 항목 | 내용 |
|---|---|
| 분야 | 엣지 AI · 실시간 임베디드 제어 · IoT 관제 |
| 사용 기술 | C/C++, FreeRTOS, Zybo Z7-20, Vivado/Vitis, TensorFlow Lite Micro, Arduino UNO, ESP8266, Python/Flask, HTML/CSS/JavaScript |
| 분류 대상 | 캔 · 일반 쓰레기 · 유리 · PET · Empty |
| 모델 구조 | MobileNetV2 α=0.5, RGB 128×128 입력, 5개 클래스 |
| 핵심 기여 | 시스템 설계·통합, Zybo 카메라·AI·RTOS, AI 학습·변환, Arduino 적재량·통신, 서버·대시보드 |
| 공개본 검증 | Arduino 1/2 컴파일, C 호스트 테스트·ASan/UBSan, Flask API 테스트 5개 |
| 공개 범위 | 보고서 부록을 기반으로 정리·수정한 코드, 시스템 설명, 사진과 시연 링크 |

이 저장소는 팀 시제품의 보고서 코드를 공개용으로 정리한 자료입니다. **Vivado/Vitis 전체 프로젝트, 학습 데이터, 모델 가중치와 일부 외부 라이브러리는 포함하지 않습니다. Zybo 전체 빌드·실보드 동작은 이번 점검에서 검증하지 않았습니다.**

## 개인 기여 범위

저장소 작성자 이민석의 담당 영역은 다음과 같습니다. 개인 담당 범위는 작성자의 확인을 기준으로 구분했습니다.

- **아이디어·시스템 설계·통합**: 전체 동작 흐름, 보드 역할 분담, 배선 및 통합 시연
- **Zybo 하드웨어·카메라**: Vivado 핀 설정, SPI/I2C 통신, ArduCAM 초기화와 JPEG 촬영
- **AI 학습·모델 변환**: 데이터셋 구성, MobileNetV2 학습, TFLite 모델 생성
- **Zybo AI 실행·FreeRTOS**: 이미지 전처리와 추론, 태스크·큐·세마포어, Arduino UART 통신
- **Arduino 2 — 적재량·Wi-Fi**: 초음파 센서 4개, UART 원형 버퍼, ESP8266과 HTTP 전송
- **Flask 서버·웹 대시보드**: 상태 API, 위치별 쓰레기통 표시, 상세 화면과 주기적 갱신

**Arduino 1의 물체 감지·뚜껑 서보·LCD 제어는 공동 작업 영역**입니다. 공개본에서는 전체 통신 흐름을 검토하기 위해 해당 코드도 함께 정리했습니다.

## 주요 기능

- 물체가 10 cm 이내에서 2초간 감지되면 분류 요청
- ArduCAM OV5642 카메라에서 JPEG 프레임 획득
- RGB 변환·128×128 리사이즈·[-1, 1] 정규화 후 TFLite Micro 추론
- UART로 분류 결과를 전달하고 대응하는 서보와 LCD 제어
- 초음파 센서 4개로 종류별 쓰레기통 적재 상태 확인
- ESP8266 AT 명령으로 적재 상태를 서버에 전송
- 위치별 포화 상태와 종류별 상세 상태를 1초 간격으로 표시

Arduino 1 코드는 뚜껑을 **5초 동안 연 뒤 닫는 방식**입니다. 보고서 본문의 “물체가 사라질 때까지 열림” 설명과 달라 코드 기준으로 정리했습니다. 수정된 공개본에서 센서 미수신 항목은 “확인 중”으로 표시합니다.

## 시스템 구조

```text
물체 감지 초음파 센서
        │
        ▼
Arduino 1 ── UART '1': 분류 요청 ──▶ Zybo Z7-20 PS
서보 4개·LCD ◀─ UART '1'~'6' ────── ARM Cortex-A9 / FreeRTOS
                                           │ SPI + I2C
                                           ▼
                                    ArduCAM OV5642
                                           │ JPEG → RGB
                                           ▼
                                   TensorFlow Lite Micro
                                   MobileNetV2 / 5 classes

적재량 초음파 센서 4개
        │
        ▼
Arduino 2 ── UART / AT commands ──▶ ESP8266
                                        │ HTTP
                                        ▼
                                 Flask 상태 API
                                        │ /status, 1초 갱신
                                        ▼
                                  관제 웹 대시보드
```

PL은 주변장치 연결과 핀 라우팅을 맡습니다. **AI 추론은 PS의 ARM 프로세서에서 수행하며, FPGA에서 CNN을 가속한 구현은 아닙니다.**

### 보드 역할

| 구성 요소 | 역할 | 핵심 인터페이스 |
|---|---|---|
| Zybo Z7-20 PS | 촬영·이미지 전처리·AI 추론·태스크 스케줄링 | SPI, I2C, UART, FreeRTOS |
| Arduino 1 | 물체 감지·분류 요청·뚜껑·LCD | 초음파, SoftwareSerial, Servo, I2C |
| Arduino 2 | 적재 상태·Wi-Fi 전송 | 초음파 4개, USART RX 인터럽트, Timer1 |
| ESP8266 | 로컬 네트워크 연결 | AT 명령, TCP/HTTP |
| Flask 서버 | 위치·종류별 상태 저장·조회 | HTTP API, 메모리 내 상태 |
| 웹 대시보드 | 상태 시각화·상세 화면 | HTML/CSS/JavaScript, 주기적 fetch |

## 공개본에서 반영한 개선 사항

- Wi-Fi 접속 정보를 `secrets.h`로 분리하고 예시 설정만 공개
- `true || IsValidJPEG(...)`로 우회되던 JPEG 검사를 복구
- 카메라 레지스터 목록 순회에서 미초기화 변수와 종료 sentinel 처리 수정
- 촬영 요청·프레임 준비·AI 완료·AI 초기화 완료 신호를 각각 분리
- 실패 경로에서도 버퍼 반환과 오류 응답을 보장하도록 AI 흐름 수정
- FLOAT32 입력 크기·자료형과 출력 크기를 검사하고 잘못된 모델 거부
- nearest-neighbor 전처리를 보고서의 학습 방식에 맞춘 bilinear로 변경
- UART 수신 루프 제한 카운터, 주변장치 초기화, C/C++ 연결 선언 수정
- 적재량 조건을 가까운 거리 기준 히스테리시스로 변경하고 시간 측정을 Timer1으로 통일
- 전송 성공 응답을 확인한 뒤 상태를 반영하고 실패 시 재시도·주기적 재전송
- 서버 오류 응답 코드, 초기 미확인 상태, 리셋 방식, 대시보드 누락 이미지와 연결 실패 표시 수정

수정 근거와 남은 제약은 [코드 검증 기록](docs/CODE_REVIEW.md), 세부 설계는 [시스템 구조](docs/ARCHITECTURE.md)에 정리했습니다.

## AI 모델과 성능 기록

보고서에는 ImageNet 기반 MobileNetV2 전이학습, Global Average Pooling, Dense 분류기, 단계별 fine-tuning을 사용한 것으로 설명되어 있습니다. 클래스 순서는 `can, general, glass, pet, empty`이며 런타임 명령도 같은 순서입니다.

| 항목 | 보고서 기록 / 공개본 상태 |
|---|---|
| 입력 | FLOAT32, [1, 128, 128, 3], [-1, 1] 정규화 |
| 분류 출력 | FLOAT32, 5개 softmax 점수 |
| 모델 파라미터 | 보고서 기준 876,469개 |
| 최종 accuracy | 보고서 기록 0.7229 (약 72.29%); 이번 점검에서 재현하지 않음 |
| Tensor arena | 코드 설정 4 MiB; 실제 모델 필요 용량은 미검증 |
| 학습 데이터 규모 | 보고서의 통합 데이터 표와 실제 로딩 설명이 달라 확정하지 않음 |
| 전력 절감 | 경량 모델·이벤트 대기 설계; 전력 계측 수치와 절감률 없음 |

입력·출력은 FLOAT32입니다. 원 코드에서 디버그 전송을 위해 float를 int8로 변환했던 부분은 **INT8 양자화 모델 구현을 의미하지 않습니다.** 다양한 현장 조건의 분류 정확도는 추가 평가가 필요합니다.

## 빌드 및 설정

### 관제 서버 실행

```bash
cd server
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python app.py
```

브라우저에서 `http://127.0.0.1:5000`을 엽니다. 하드웨어 없이 `http://127.0.0.1:5000/t1/fill/ca`에 접속하면 탕비실 캔 상태를 “수거 필요”로 바꿔 확인할 수 있습니다. 비우기는 `/t1/empty/ca`, 전체 종류는 `al`입니다. 센서값은 연속 적재율이 아니라 **포화 여부**입니다.

ESP8266 연결 시 같은 LAN에서 `flask --app app run --host 0.0.0.0 --port 5000`으로 실행하고 서버 IP를 로컬 설정에 입력합니다. 인증·영구 저장소가 없는 시제품 서버이며, 공개 인터넷 서비스 배포는 이 프로젝트 범위에 포함하지 않습니다.

### Arduino 1

Arduino AVR Boards(UNO), Servo, LiquidCrystal I2C 라이브러리를 설치합니다. Wire와 SoftwareSerial은 AVR core에 포함됩니다.

```bash
arduino-cli compile --fqbn arduino:avr:uno firmware/arduino_lid
```

### Arduino 2

`secrets.example.h`를 같은 폴더의 `secrets.h`로 복사하고 Wi-Fi·서버·위치 ID를 입력합니다. `secrets.h`는 커밋하지 않습니다.

```bash
avr-gcc -mmcu=atmega328p -DF_CPU=16000000UL -Os -std=c11   -Wall -Wextra -Werror firmware/arduino_monitor/main.c -o monitor.elf
```

이는 Arduino framework를 사용하는 스케치가 아닌 **독립 AVR C 펌웨어**입니다. Arduino core와 동시에 링크하지 않습니다. Timer1은 시간 측정용으로 사용합니다.

### Zybo

Vivado/Vitis에서 원 배선에 맞는 하드웨어 플랫폼과 FreeRTOS BSP를 생성하고 `firmware/zybo`와 `firmware/common/vision_core.c`를 추가합니다. 모델·TensorFlow Lite Micro·카메라 레지스터 테이블·stb_image 구현이 추가로 필요합니다. [펌웨어 통합 안내](firmware/README.md)와 [하드웨어 설정](hardware/README.md)을 참고하세요.

### 테스트

프로젝트 폴더에서 실행합니다.

```bash
make -C tests test
python3 -m unittest discover -s tests -p 'test_*.py' -v
node tests/test_dashboard.cjs
```

Python 테스트에는 `server/requirements.txt`의 Flask가 필요합니다.

## 저장소 구조

```text
firmware/
├── common/               # JPEG 검증, bilinear 전처리, 클래스 명령
├── zybo/                 # FreeRTOS tasks, ArduCAM, PS peripherals
├── arduino_lid/          # 물체 감지·서보·LCD 공동 작업 영역
└── arduino_monitor/      # 초음파·USART ring buffer·ESP8266
server/                   # Flask API, templates, 시연용 배치도
ml/                       # 보고서의 학습 구조와 모델 준비 안내
docs/                     # architecture, technical notes, code review
hardware/                 # 보고서에서 추출한 XDC와 배선 안내
media/                    # 원 시제품 사진과 보고서 화면
tests/                    # 이미지 로직·서버 회귀 테스트
```

## 시연 영상 및 결과

[전체 동작 시연 영상](https://drive.google.com/file/d/1xSyefF0ejK1Y60eP9jwrPD4I7R57ZHVT/view?usp=sharing)

| 일반 쓰레기 분류 | 캔 분류 |
|---|---|
| ![일반 쓰레기 시연](media/general-demo.png) | ![캔 시연](media/can-demo.png) |

![원 시제품 관제 화면](media/dashboard-report.png)

사진과 관제 화면은 원 보고서의 시제품 기록입니다. 공개본에서 수정한 코드로 재시연한 결과는 아닙니다. 서버의 배치도는 누락된 원본 `new_map.png`를 대신해 만든 시연용 그림입니다.

## 한계와 개선 방향

- Zybo 전체 빌드·플래시, 실제 모델 추론, 카메라·통신·서보의 현장 동작은 추가 검증이 필요합니다.
- SoftwareSerial 115200 bps와 서보 인터럽트의 타이밍은 실보드에서 확인해야 합니다.
- UART 명령에 요청 번호가 없어 10초 타임아웃 이후 도착한 과거 결과를 구분하지 못합니다.
- ESP8266 상태 파서는 단순 문자열 검색이며, IPD 프레임의 완전한 파싱은 구현하지 않았습니다.
- 센서 임계값은 거리·장착 위치에 맞게 보정해야 합니다. 2/4 cm는 공개본의 조정 가능한 기본값입니다.
- 서버는 메모리 내 상태를 사용해 재시작 시 미확인 상태가 됩니다. 인증·DB·센서 stale 판정이 필요합니다.
- RTOS 이벤트 대기는 애플리케이션의 불필요한 연산을 줄이지만, tickless idle이나 전원 차단을 구현한 것은 아닙니다.

## 참고 자료

- [FreeRTOS binary semaphores](https://sources.freertos.org/Documentation/02-Kernel/02-Kernel-features/02-Queues-mutexes-and-semaphores/02-Binary-semaphores)
- [AMD/Xilinx embedded software drivers](https://github.com/Xilinx/embeddedsw)
- [Arduino SoftwareSerial](https://docs.arduino.cc/learn/built-in-libraries/software-serial)
- [ESP8266 TCP/IP AT commands](https://docs.espressif.com/projects/esp-at/en/release-v2.1.0.0_esp8266/AT_Command_Set/TCP-IP_AT_Commands.html)
