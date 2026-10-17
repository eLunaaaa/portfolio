# 프로젝트별 기술적 과제와 해결

각 프로젝트에서 해결한 문제를 **제약 → 설계 선택 → 구현·검증**으로 연결했습니다. LOC는 코드 규모이고, 기술적 깊이는 상태 관리·동시성·수치 정합성·자원 제약·측정 설계에서 확인할 수 있습니다.

[프로젝트별 LOC 비교](LOC.md) · [포트폴리오](../README.md)

<a id="blue-witness"></a>

## Blue Witness

**45,105 LOC** · 단절·재시작·지연 도착 속에서 영상 보존과 시간 정합성 유지.

**개인 담당:** AI 학습·배포, 서버·웹·시간 동기화 상태 서비스, 전체 Fail-safe 설계·Uploader 전체 개발, CI/CD. Uploader는 이후 CD 연동 과정에서 팀 수정이 반영됐고 장치 통합은 공동 작업.

| 해결해야 할 문제 | 기술적 제약 | 설계·구현 | 확인 근거 |
|---|---|---|---|
| 전송 도중 끊겨도 영상을 잃거나 중복 완료 처리하지 않아야 함 | 로컬 접수와 서버 저장은 서로 다른 단계이고 응답이 유실될 수 있음 | MP4·메타데이터·SQLite 큐 보존, 사고·차량·SHA-256·STORED 확인, 불명확한 응답은 저장 상태 재조회 | [업로더 코드](../projects/blue-witness/source/uploader/README.md) · [단절 복구 12건](../projects/blue-witness/source/docs/measurements/20261008-scenarios/README.md) |
| 여러 차량의 영상이 서로 다른 시각·순서로 도착함 | 한 차량을 계속 기다리면 결과 확인이 늦어지고 늦은 영상을 버리면 증거가 빠짐 | Chrony 상태·촬영 시각 전달, 기본 12초 수집 창, 별도 FFmpeg Worker, 늦은 영상은 다음 revision으로 반영 | [시간 정렬·서버 설계](../projects/blue-witness/docs/design/SERVER.md) · [실제 병합 결과](../projects/blue-witness/docs/IMPLEMENTATION_GALLERY.md) |
| 차종 검출의 오탐과 불필요한 업로드를 줄이면서 차량·사용자별 권한을 지켜야 함 | 프레임 검출·영상 선택·장치 인증·사용자 조회의 기준이 서로 다름 | 세션별 데이터 분리·재검수·재학습, Confidence·2-of-3 판단, TLS·노드 키·사용자/사고별 접근 제어 | [모델 비교 조건·수치](../projects/blue-witness/analysis/model-results/README.md) · [서버·보안](../projects/blue-witness/docs/SERVER_SECURITY.md) |

[프로젝트 코드·담당 범위](../projects/blue-witness/README.md) · [언어별 LOC](../projects/blue-witness/docs/LOC.md)

<a id="configurable-cnn-rtl"></a>

## CNN 학습·RTL 자동화

**1,726 LOC** · 학습 모델의 형상·정수 계산을 Verilog 회로와 일치시키기.

**개인 담당:** 모델 구조·학습, 정수 평가, Verilog 모듈, 생성·합성 자동화 전체 개인 구현.

| 해결해야 할 문제 | 기술적 제약 | 설계·구현 | 확인 근거 |
|---|---|---|---|
| 같은 CNN이라도 flatten 순서·계층 크기가 다르면 전혀 다른 결과가 나옴 | Python 텐서와 RTL 다차원 포트·배선의 데이터 배치가 다름 | 공통 JSON의 형상 검증과 채널 우선 flatten, 가중치·bias 입력 포트를 연결 | [설정·모델 구성](../projects/configurable-cnn-rtl/python/config_utils.py) · [RTL 생성](../projects/configurable-cnn-rtl/python/generate_cnn_top.py) |
| 음수·누적·반올림·포화가 소프트웨어와 하드웨어에서 달라질 수 있음 | Q3.4의 제한된 비트 폭과 signed 연산을 계층마다 유지해야 함 | 곱셈 후 소수점 정렬, 누산 폭·포화 규칙을 정수 기준 계산과 RTL에 맞춤 | [정수 기준 계산](../projects/configurable-cnn-rtl/python/fixed_point.py) · [100개 점수 대조](../projects/configurable-cnn-rtl/tests/check_fixed_point.py) |
| 모델 변경이 RTL 생성·시뮬레이션·합성까지 이어져야 함 | 외부 도구 실패나 연결 오류를 자동화가 성공으로 처리하면 안 됨 | 모듈별 테스트벤치, sv2v 전체 모듈 변환, Yosys 최종 통계 추출과 종료 코드 확인 | [검증 범위·합성 흐름](../projects/configurable-cnn-rtl/docs/CODE_REVIEW.md) |

[프로젝트 코드·담당 범위](../projects/configurable-cnn-rtl/README.md) · [언어별 LOC](../projects/configurable-cnn-rtl/docs/LOC.md)

<a id="edge-ai-trash-sorter"></a>

## 엣지 AI 쓰레기 분류

**1,541 LOC** · 촬영·추론·UART가 공유 버퍼와 완료 순서를 지키도록 동기화.

**개인 담당:** 시스템 설계·통합, Zybo 카메라·AI·FreeRTOS, Arduino 2 적재량·Wi-Fi, Flask·웹. Arduino 1 제어는 공동 작업.

| 해결해야 할 문제 | 기술적 제약 | 설계·구현 | 확인 근거 |
|---|---|---|---|
| 카메라가 다음 프레임을 쓰는 동안 AI가 이전 버퍼를 읽을 수 있음 | 촬영·전처리·추론·UART는 처리 시간이 다르고 실패도 발생함 | 촬영 요청·프레임 준비·AI 완료를 세마포어로 분리하고 AI 완료 후 공유 버퍼 재사용 | [태스크와 버퍼 수명](../projects/edge-ai-trash-sorter/docs/ARCHITECTURE.md) · [Zybo 코드](../projects/edge-ai-trash-sorter/firmware/zybo) |
| JPEG 촬영 데이터가 학습 모델 입력과 맞아야 함 | 색상·크기·클래스 순서가 다르면 추론 결과를 잘못 해석함 | 정상 JPEG 검사, bilinear 전처리, 모델 출력 순서와 UART 분류 명령을 연결 | [전처리 코어](../projects/edge-ai-trash-sorter/firmware/common/vision_core.c) · [C 검사](../projects/edge-ai-trash-sorter/tests/test_vision.c) |
| 센서 노이즈·HTTP 실패가 적재 상태를 뒤집거나 서버에 잘못 확정될 수 있음 | 초음파 4개·UART 수신·ESP8266 전송을 함께 처리함 | 2/4cm 히스테리시스, HTTP 200 뒤 보고 상태 확정, 재시도·재연결·주기적 재전송 | [Arduino 2 상태·전송](../projects/edge-ai-trash-sorter/firmware/arduino_monitor/main.c) · [서버 API 검사](../projects/edge-ai-trash-sorter/tests/test_server.py) |

[프로젝트 코드·담당 범위](../projects/edge-ai-trash-sorter/README.md) · [언어별 LOC](../projects/edge-ai-trash-sorter/docs/LOC.md)

<a id="arduino-load-balancer"></a>

## Arduino 동적 부하 분산

**771 LOC** · 제한된 MCU에서 비동기 완료·큐·배치별 결과를 일관되게 관리.

**개인 담당:** Master 스케줄링, I²C·완료 인터럽트, Slave 합성곱·큐, PWM·UART·타이머. 시스템 통합·실험·시연은 공동 작업.

| 해결해야 할 문제 | 기술적 제약 | 설계·구현 | 확인 근거 |
|---|---|---|---|
| 순서대로 분배하면 패치별 유효 연산량 차이로 Slave가 불균형해짐 | 0인 항은 계산을 생략하므로 작업 개수와 실제 부하가 다름 | 입력·필터의 유효 항에 같은 부하 식을 적용하고 실행 중+대기 작업 부하가 가장 적은 Slave 선택 | [공통 스케줄러](../projects/arduino-load-balancer/firmware/libraries/LoadBalancerCore/src) · [동작 구조](../projects/arduino-load-balancer/README.md) |
| 다음 행렬 입력과 연속 완료 이벤트가 기존 결과를 덮거나 합쳐질 수 있음 | UNO 메모리·작업 버퍼 수가 제한되고 완료 순서는 일정하지 않음 | 배치별 snapshot·작업 ID, 2-slot 결과 보존, 명시적 ACK 이후 해제, 완료 신호 재확인 | [무작위 스케줄·버퍼 포화·번호 wrap 검사](../projects/arduino-load-balancer/docs/CODE_REVIEW.md) |
| TWI 실패·signed 자료형·ISR 지연이 분산 결과 정합성을 깨뜨릴 수 있음 | 레지스터 통신·UART·Timer PWM·완료 인터럽트가 공존함 | TWI ACK·deadline 확인 뒤 큐 commit, int8 입력·int32 누산, ISR 단축·timer atomic snapshot | [통신 규격](../projects/arduino-load-balancer/docs/PROTOCOL.md) · [검수·검증](../projects/arduino-load-balancer/docs/CODE_REVIEW.md) |

[프로젝트 코드·담당 범위](../projects/arduino-load-balancer/README.md) · [언어별 LOC](../projects/arduino-load-balancer/docs/LOC.md)

<a id="embedded-linux-system-programming"></a>

## 실시간 Linux 실험 환경

**591 LOC** · 측정 코드의 저장·스케줄링 비용이 실험을 교란하지 않게 구성.

**개인 담당:** UDP 기준 송수신 코드, 실험 세팅·PREEMPT_RT 도구 설정·CPU 3 고정·논문 리딩과 정리.

| 해결해야 할 문제 | 기술적 제약 | 설계·구현 | 확인 근거 |
|---|---|---|---|
| 송신 작업 시간이 누적되면 실제 송신 주기가 기준에서 밀림 | 작업 실행 주기와 호스트 간 시각 비교의 목적이 다름 | MONOTONIC 절대 deadline으로 송신 주기를 관리하고 패킷에 sequence·REALTIME TX 시각 기록 | [sender.c](../projects/embedded-linux-system-programming/source/udp_socket/src/sender.c) |
| 수신할 때마다 파일·문자열 처리를 하면 측정 지연에 기록 비용이 섞임 | 할당·페이지 fault·디스크 쓰기와 수신 측정 경로를 분리해야 함 | tmpfs·mmap·mlockall·pre-touch 후 수신 중 binary record만 저장, 종료 후 파일 저장 | [receiver.c](../projects/embedded-linux-system-programming/source/udp_socket/src/receiver.c) |
| 커널·CPU 조건이 달라지면 결과를 같은 기준으로 비교하기 어려움 | CPU affinity·scheduling latency·UDP TX/RX 측정은 서로 다른 확인 항목 | 동일 송수신기를 유지하고 PREEMPT_RT 도구·CPU 3 affinity·시계와 측정점을 명시 | [측정 설계](../projects/embedded-linux-system-programming/docs/DESIGN.md) · [개인 실행 설정](../projects/embedded-linux-system-programming/source/README.md) |

[프로젝트 코드·담당 범위](../projects/embedded-linux-system-programming/README.md) · [언어별 LOC](../projects/embedded-linux-system-programming/docs/LOC.md)

<a id="stockauto-news-trader"></a>

## 뉴스·물리 버튼 주식 주문

**2,601 LOC** · 승인·취소·부분 체결·재시작 이벤트가 뒤섞여도 주문 원장 유지.

**개인 담당:** 개인 주식 프로그램의 키움·RSI·DB 구조를 뉴스 분석·UNO 승인 장치로 확장하고 주문 상태·위험 관리 정리.

| 해결해야 할 문제 | 기술적 제약 | 설계·구현 | 확인 근거 |
|---|---|---|---|
| 주문 전송 성공과 실제 체결·취소 결과는 시점과 의미가 다름 | 부분 체결·중복 통보·늦은 원격 번호·불명확한 응답이 발생함 | SUBMITTING 사전 기록, 주문일·원격 번호·종목·방향 대조, 누계 체결 증가분과 원가 원장 복구 | [OrderBook](../projects/stockauto-news-trader/stockauto/orders.py) · [원장·복구](../projects/stockauto-news-trader/stockauto/store.py) |
| 증권사 응답을 기다리는 동안 거절·만료·새 악재가 늦게 처리될 수 있음 | ActiveX 호출은 GUI 스레드에 두고 중첩 이벤트 루프의 주문 재진입을 막아야 함 | 20ms 제어 타이머와 50ms 주문 타이머 분리, busy 보호, 전송 직전 제안·최신 뉴스·연결 세대 재검사 | [Qt·주문 구조](../projects/stockauto-news-trader/docs/ARCHITECTURE.md) · [Qt 통합 검사](../projects/stockauto-news-trader/tests/check_qt_controls.py) |
| 과거 승인·버튼 흔들림·계좌 기존 보유분을 새 주문과 혼동할 수 있음 | PC와 UNO의 상태·만료 시각, 주문 수량·소유 범위가 일치해야 함 | 제안 ID·양쪽 만료 검사·버튼 debounce, 승인 수량 고정, 체결 원장 소유 수량으로 자동 매도 범위 제한 | [펌웨어](../projects/stockauto-news-trader/firmware/news_lcd/news_lcd.ino) · [자동화 규칙](../projects/stockauto-news-trader/docs/AUTOMATION_RULES.md) |

[프로젝트 코드·담당 범위](../projects/stockauto-news-trader/README.md) · [언어별 LOC](../projects/stockauto-news-trader/docs/LOC.md)

<a id="smart-umbrella-stand"></a>

## Smart Umbrella Stand

**687 LOC** · 3개 보드의 센서·모델·날씨·제어 상태를 일관되게 연결.

**개인 담당:** 아이디어 제안, Master 판단·통신·ML 추론과 Slave2 날씨·LCD·수위 표시. Slave1 센싱·팬·승강부는 팀원 담당.

| 해결해야 할 문제 | 기술적 제약 | 설계·구현 | 확인 근거 |
|---|---|---|---|
| 보드마다 구조체 배치·첫 응답 시점이 다르면 센서와 제어 명령이 어긋남 | 서로 다른 SPI 처리 방식과 제한된 MCU 버퍼를 연결함 | padding 없는 6-byte wire format, Slave2 header preload 상태 머신, HAL_GetTick 기반 timeout | [통신·제어 검수](../projects/smart-umbrella-stand/docs/TECHNICAL_NOTES.md) · [호스트 C 검사](../projects/smart-umbrella-stand/tests) |
| 습도 경계에서 팬·승강 명령이 반복 전환될 수 있음 | 센서 변화·우산 유무·강수 상태를 함께 반영해야 함 | 습도·이동평균·변화량의 경량 모델 추론과 60%/40% 히스테리시스, 날씨 결합 제어 | [Master 코드](../projects/smart-umbrella-stand/firmware/master/Core/Src) · [건조·표시 시연](../projects/smart-umbrella-stand/docs/IMPLEMENTATION_GALLERY.md) |
| API 조회 날짜와 장치 표시 상태가 통신 지연·날짜 변경에서도 맞아야 함 | GMT→KST 변환·월말·윤년, 버튼·LCD·수위 표시를 함께 처리함 | 날짜 rollover·강수형태 enum·상태별 응답 처리로 기상 정보와 표시를 연결 | [날짜·통신 공통 코드](../projects/smart-umbrella-stand/firmware/common/src) · [구현 결과](../projects/smart-umbrella-stand/docs/IMPLEMENTATION_GALLERY.md) |

[프로젝트 코드·담당 범위](../projects/smart-umbrella-stand/README.md) · [언어별 LOC](../projects/smart-umbrella-stand/docs/LOC.md)
