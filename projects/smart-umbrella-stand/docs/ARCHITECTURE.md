# Architecture

## 제어 주기

Master는 500 ms 주기로 다음 순서를 수행한다.

1. Slave1 센서 프레임 수신 및 이전 주기에 계산한 서보/팬 명령 송신
2. 센서 프레임 헤더와 범위 검증
3. 수위 임계치 히스테리시스 처리
4. Slave2에 다이얼/LED 명령 송신 및 강수확률/강수형태 수신
5. 습도 이동평균과 wet/dry 분류 갱신
6. 다음 주기의 Slave1 명령 결정

명령이 한 주기 뒤 반영되는 구조는 의도된 파이프라인이다. 즉시 반영이 필요하면 센서 수신과 명령 송신을 두 트랜잭션으로 나누거나 제어 주기를 단축할 수 있다.

## 상태와 히스테리시스

- 강수확률은 60% 이상에서 rainy로 진입하고 40% 이하에서 해제한다.
- wet/dry는 센서 임계값을 별도로 섞지 않고 학습 모델 점수로만 판정한다. 모델 확률 60%에서 wet으로 진입하고 40%에서 dry로 해제하며 중간 구간은 이전 상태를 유지한다.
- API의 강수형태가 `NONE`이 아니면 확률과 별개로 rainy로 본다.
- 수위 트리거도 ON/OFF 임계치를 분리해 경계에서 반복 작동하지 않게 한다.
- 우산이 젖으면 바닥 위치에서 건조하고, 마르면 승강한다. rainy 상태에서는 젖은 우산의 팬을 강풍으로 높인다.

## 책임 경계

- `firmware/common`: MCU와 무관한 wire protocol 및 날짜 계산
- `slave1_app`: ADC DMA snapshot, 이동평균, actuator command 적용
- `control_logic`: 보정, ML 추론, 기상 히스테리시스, command 결정
- `stm32_transport`: 레지스터/DMA/NSS/timeout 같은 STM32 의존 코드
- `slave2_spi`: 첫 response header preload를 포함한 byte state machine
