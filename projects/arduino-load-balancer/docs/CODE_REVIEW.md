# Code review and validation

Master·Slave·공통 스케줄러·PC 호스트 코드의 점검 및 정리 내용이다.

## 코드 점검 및 정리

| 영역 | 정리한 동작 | 공개본 반영 |
|---|---|---|
| 부하 계산 | Master는 입력 nonzero 수만, Slave는 입력·필터 nonzero 및 곱셈/덧셈 가중치 사용 | 같은 `3n−1` 공통 함수로 통일 |
| 서보 PWM | OCR 하한 -250으로 음수가 uint16에 저장될 수 있음 | Timer1 250~500 tick으로 clamp |
| 입력 자료형 | Master unsigned byte와 Slave signed char 불일치 | 양쪽 int8로 통일 |
| 누산 | int16 결과의 범위를 넘는 9항 합계 가능 | int32 누산과 결과 frame |
| 파이프라인 | 새 입력·필터·부분 패치가 단일 배열에 덮어써짐 | 배치별 입력/필터 snapshot과 명시적 slot 수명주기 |
| 패치 생성 | 완료 이벤트마다 다음 입력 준비 여부 없이 다음 ID 생성 | 실제 수신한 배치만 스케줄링 |
| 작업 번호 | uint8 전역 번호 wrap과 modulo 9/2로 세대 혼동 | uint16 배치 번호와 patch index 분리 |
| Slave 결과 | result_array 하나를 연속 결과가 덮어쓸 수 있음 | 2개 Result slot, explicit ACK 이후 해제 |
| 완료 이벤트 | bool 플래그와 10 ms Slave debounce가 연속 완료를 합침 | Slave 완료는 debounce 제거, level signal·주기적 재확인 |
| TWI ISR | byte 수신마다 UART 출력해 ISR 지연 | 짧은 frame 수신·응답 처리 |
| TWI 전송 | 상태·NACK·timeout 확인 없이 queue에서 먼저 제거 | ACK 상태·deadline·STOP 완료 후 queue commit |
| timer 공유값 | 16비트 counter의 비원자적 read/write | uint32 tick의 atomic snapshot과 elapsed 비교 |
| 원본 중복 | Slave 주소별 거의 같은 4개 파일 | 주소 macro로 빌드하는 공통 Slave |

레지스터 기반 Master·Slave 어댑터와 공통 로직을 구성했다. 공개본은 작업 payload에 입력·필터·배치 번호를 함께 담고, 결과를 int32 frame으로 송수신한다. 같은 공개본 버전의 Master/Slave를 함께 사용한다.

## 동작·성능 기록

보고서에는 실보드 기동, 입력·필터 수신, 스케줄링, 서보 부하 표시, zero-skip 연산, 결과 행렬 출력과 두 개의 시연 영상이 기록되어 있다. 순차 234,000 ms와 4-Slave 80,500 ms를 비교했다. 이 값은 곱셈 2초·덧셈 1초의 시연 지연을 포함한다.

## 공개 코드 검사

- Arduino UNO Master 컴파일 성공.
- SLAVE_ADDRESS=1/2/3/4 각각 Arduino UNO 컴파일 성공.
- 공통 C++ 테스트: zero-skip·부하, signed 32비트 codec, PWM 범위, FIFO 순환·포화 검사 통과.
- 100회에 걸쳐 서로 다른 두 행렬·필터를 동시 스케줄링하고 무작위 worker/완료 순서로 1,800개 패치 결과를 기준 계산과 비교해 통과.
- 작업 중복 결과 거부, 두 buffer 포화, 재사용, 65535→0 sequence wrap 확인.
- Slave adapter를 native register 대역으로 실행해 2-slot 수명주기, 중복 작업, stable result read, explicit ACK, 잘못된 frame 거부 검사 통과.
- AddressSanitizer·UndefinedBehaviorSanitizer 실행 오류 없이 통과.
- Python 테스트 3개: signed UART frame·값 검증, 기준 합성곱, readiness credit와 역순 결과 수신 처리 통과.

테스트 실행 명령은 README에 정리했다. 호스트 스트림 도구는 공개본의 UART 입출력과 결과 검산을 위해 추가했다.
