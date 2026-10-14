# Code review and validation

검토일: 2026-10-14. 보고서 부록의 Zybo, Arduino 1/2, Flask, HTML 코드를 추출해 공개본으로 정리했다. 원본 Word 문서는 변경하지 않았다. 아래 변경은 원 시제품의 실험 당시 코드와 구분해야 한다.

## 확인한 오류와 공개본 수정

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

## 실행한 검증

- Arduino 1: Arduino CLI, `arduino:avr:uno`, AVR core 1.8.6, Servo 및 LiquidCrystal I2C로 컴파일 성공. Flash 8,436 bytes, 전역 RAM 729 bytes. 컴파일 시점 라이브러리 기준이다.
- Arduino 2: avr-gcc 7.3.0, `-mmcu=atmega328p -DF_CPU=16000000UL -Os -std=c11 -Wall -Wextra -Werror`로 ELF 생성 성공.
- C host: JPEG trimming의 정상/손상/범위 오류, bilinear 평균·정규화·입출력 크기, 5개 클래스 mapping과 NaN/Inf/음수 거부 테스트 통과.
- 같은 C 테스트를 AddressSanitizer·UndefinedBehaviorSanitizer로 실행해 오류 없이 통과.
- Flask: 초기 미확인 상태, 템플릿·정적 asset, 위치·종류별 갱신, 전체 변경·POST reset, 잘못된 API 요청의 상태 보존, 독립 app 상태를 포함한 테스트 5개 통과.

- 웹 JavaScript: Node VM의 DOM/fetch 대역으로 미확인·포화·여유 표시, 모달과 오프라인 안내 실행 검사 통과. 실제 브라우저 layout 검증은 아니다.
- Zybo C/C++: 임시 외부 API 대역 header로 SDT/legacy 두 경로의 syntax check 통과. 실제 BSP API 호환·링크·실행 검증은 아니다. 대역 header는 공개본에 포함하지 않는다.

## 검증하지 못한 항목

- Zybo용 BSP·TensorFlow Lite Micro·실제 모델·카메라 레지스터 header가 없어 전체 Zybo 빌드 및 실행 미검증.
- 실제 카메라 캡처, 학습된 모델의 정확도·지연, AI 초기화/완료 semaphore의 실보드 스케줄링, ESP8266 통신과 서보 동작은 미검증.
- SPI/I2C driver 내부 polled wait까지 포괄하는 시간 제한은 적용하지 않았다. 앱 수준 timeout만으로 모든 보드 장애를 복구한다고 주장하지 않는다.
- 원 AI 모델의 재학습, 양자화, 전력 측정은 수행하지 않았다.
- 캔/유리/PET 사진은 보고서의 동작 기록이며 공개본 수정 후 시험 결과는 아니다.

## 남은 개선 항목

1. 요청 ID·checksum·timeout을 포함한 UART 프레임과 늦은 응답 폐기
2. 모델과 firmware의 동일 이미지 입력 비교, confidence 기반 unknown 처리
3. SoftwareSerial/Servo의 interrupt timing과 초음파 간섭 확인
4. ESP8266 +IPD 파싱, ring buffer overflow 계측과 비차단 통신
5. DB·인증·POST 센서 갱신, heartbeat와 stale 표시
6. 실제 전력 계측과 idle/tickless 설정

현재 결론은 “Arduino 코드 컴파일과 하드웨어 독립 로직·서버 테스트 통과”다. 시스템 전체에 오류가 없다는 보장은 아니다.
