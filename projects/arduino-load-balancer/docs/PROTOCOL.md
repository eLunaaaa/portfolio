# Protocol

공개본 Master와 Slave는 아래 규격을 같이 사용한다. 다중 바이트 정수는 little-endian이다.

## PC → Master UART

115200 bps, 8N1. 총 38바이트.

```text
'<' 'M' [input int8 × 25] 'F' [filter int8 × 9] '>'
```

시작·종류·종료 문자는 정해진 위치에서 확인한다. payload 내부의 같은 문자 값은 데이터로 취급한다. 입력은 행 우선 배열이다.

Master 출력:

- `ARDUINO_READY`: 시작 준비, 첫 frame 전송 허용
- `ACCEPTED <sequence>`: 배치 수신 및 스케줄링 완료
- `RDY_FOR_NEXT`: 다음 frame 한 개를 보낼 credit
- `RESULT <sequence> <9 signed decimal values>`: 결과 행렬
- `BUSY`: 현재 입력 buffer credit 없음

## Master → Slave TWI

| 명령 | 크기 | frame |
|---|---:|---|
| 필터 broadcast | 10 bytes | `0x01`, filter int8 × 9 |
| 작업 | 22 bytes | `0x10`, sequence uint16, patch uint8, input int8 × 9, filter int8 × 9 |
| 결과 ACK | 4 bytes | `0x20`, sequence uint16, patch uint8 |

작업 번호 patch는 0~8이다. 같은 sequence+patch의 중복 수신은 기존 slot을 유지한다. 작업은 완전한 frame이 STOP까지 수신된 뒤에만 publish한다. 수신 주소는 1~4, 필터 broadcast는 general call 0이다.

## Slave → Master TWI

결과는 8바이트다.

```text
[0]     0xA5 (결과 없음: 0x00)
[1..2]  batch sequence uint16
[3]     patch index 0..8
[4..7]  convolution result int32
```

Master는 worker별 pending 작업과 결과 번호를 확인한다. read만으로 결과를 지우지 않으며 명시적 ACK에 맞춰 slot을 해제한다. signed int8 곱의 9항 합계가 16비트 범위를 넘을 수 있어 공개본은 int32 결과를 사용한다.
