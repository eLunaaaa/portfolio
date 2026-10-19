# Code review and validation

Zybo·Arduino 센싱/구동·Flask 서버·관제 화면 코드의 점검 및 정리 내용이다.

## 오류 수정과 동작 검증

| 영역 | 확인한 문제 | 수정 |
|---|---|---|
| 카메라 | `true || IsValidJPEG(...)`가 JPEG 검사를 우회 | SOI/EOI, 길이·버퍼 범위, trailing zero 검사 |
| 센서 초기화 | 미초기화 reg_addr/reg_val, sentinel도 전송, 오류 무시 | sentinel을 읽기 전에 종료 판단, 실패 반환 |
| RTOS | 요청이 AI 완료 semaphore를 해제해 버퍼를 덮어쓸 수 있음 | capture request와 AI done 분리 |
| RTOS | 부팅 직후 촬영, WakeUpEnable에 따라 완료 신호 누락 | 요청 대기·AI 초기화 결과 전달·완료 신호 통일 |
| 초기화 | semaphore/task 생성 및 peripheral lookup 실패 처리 부족 | 핸들·생성 결과·config pointer·초기화 결과 확인 |
| 카메라 | capture/burst 실패가 호출자에게 전달되지 않음, malloc 한쪽 실패 시 누수 | 상태 반환, 두 임시 버퍼 모두 해제 |
| I2C/SPI | 버스 busy 무한 대기와 전송 실패 후 미초기화 RX 사용 | tick timeout, 오류 반환, RX 초기화 |
| AI | 잘못된 tensor type·크기로 메모리 접근, NaN 결과 index -1 위험 | 입력/출력 검증, 유효 softmax 범위·finite 검사 |
| 전처리 | 학습 설명은 bilinear, 실행은 nearest-neighbor | bilinear 공통 모듈, RGB 이미지 크기 검사 |
| UART | loop_count가 증가하지 않아 제한 무효, 로그가 분류 UART로 전송 | iteration 제한 복구, 클래스 UART의 텍스트 로그 제거 |
| 연결 | C 함수 header에 C++ linkage 구분 없음 | extern C guard 추가 |
| Arduino 1 | 추론 오류 `6`을 무시하고 timeout까지 대기 | 즉시 오류 표시·서보 복귀 |
| Arduino 2 | `USART0_RX_vect`가 UNO의 ATmega328P에 맞지 않음 | `USART_RX_vect` 사용 |
| Arduino 2 | F_CPU가 delay header 뒤에 정의됨 | header 이전에 clock 정의 |
| 적재량 | `(dist < 2 || dist > 3)`로 먼 거리도 full 판정 | 2/4 cm 기본 히스테리시스, 보정 필요 명시 |
| 거리/timeout | loop 횟수·지연 누적으로 시간을 추정 | Timer1 overflow ISR와 atomic snapshot 사용 |
| HTTP | 서버 반영 실패 전에 센서 상태 변경, 실패 후 재시도 없음 | HTTP 200 확인 후 반영, 재시도·refresh·재연결 |
| ESP8266 | 성공 확인이 부족한 초기화, debug 문자열을 AT UART로 출력 | 단계별 응답 확인, AT UART의 debug 출력 제거 |
| 공개 설정 | Wi-Fi 비밀번호와 사설 주소가 하드코딩 | secrets template로 분리, 원 값 제외 |
| 서버 | 잘못된 요청에도 HTTP 200, debug=True, 미수신=empty | 400/404, debug 비활성화, None 상태 |
| 웹 | 누락된 new_map.png, fetch 오류가 콘솔에만 표시 | 시연용 SVG, 연결 실패·미수신 상태 표시 |

## 실보드 실행

프로젝트 개발 과정에서 카메라 촬영, AI 추론, Arduino 통신·서보 제어, 적재량 전송과 관제 화면을 연동해 실보드에서 검증하고 실행했다. 컴파일·회귀 검사 항목은 다음과 같다.

## 컴파일·회귀 검사

- Arduino 1: Arduino CLI, `arduino:avr:uno`, AVR core 1.8.6, Servo 및 LiquidCrystal I2C로 컴파일 성공. Flash 8,436 bytes, 전역 RAM 729 bytes. 컴파일 시점 라이브러리 기준이다.
- Arduino 2: avr-gcc 7.3.0, `-mmcu=atmega328p -DF_CPU=16000000UL -Os -std=c11 -Wall -Wextra -Werror`로 ELF 생성 성공.
- C host: JPEG trimming의 정상/손상/범위 오류, bilinear 평균·정규화·입출력 크기, 5개 클래스 mapping과 NaN/Inf/음수 거부 테스트 통과.
- 같은 C 테스트를 AddressSanitizer·UndefinedBehaviorSanitizer로 실행해 오류 없이 통과.
- Flask: 초기 미확인 상태, 템플릿·정적 asset, 위치·종류별 갱신, 전체 변경·POST reset, 잘못된 API 요청의 상태 보존, 독립 app 상태를 포함한 테스트 5개 통과.

- 웹 JavaScript: Node VM의 DOM/fetch 대역으로 미확인·포화·여유 표시, 모달과 오프라인 안내 실행 검사 통과.
- Zybo C/C++: 임시 외부 API 대역 header로 SDT/legacy 두 경로의 syntax check 통과.
