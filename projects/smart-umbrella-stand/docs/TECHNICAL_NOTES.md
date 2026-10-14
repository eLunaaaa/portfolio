# Technical notes and corrections

이 문서는 보고서 부록 코드를 공개본으로 정리하면서 수정한 기술적 불일치를 기록한다.

## 수정 사항

1. **비밀정보**: SSID, Wi-Fi 암호, API 키를 제거하고 `secrets.example.h`만 공개한다.
2. **ML 경계와 히스테리시스**: 단일 판정에서 Python의 `sigmoid(z) > 0.5`는 `z > 0.0`과 같다. 런타임에서는 센서 임계값을 추가하지 않고 모델 점수에 60%/40% 히스테리시스를 적용한다. sigmoid를 계산하지 않기 위해 동등한 logit 경계 `z ≥ 0.4054651`/`z ≤ -0.4054651`을 사용한다.
3. **센서 프레임**: 필드 합은 6바이트다. C 구조체는 정렬 때문에 8바이트가 될 수 있으므로 wire format을 수동 직렬화한다. 기존 7번째 우산 중복 바이트와 header 위치 보정 코드는 제거했다.
4. **타이머**: SYSCLK=64 MHz, APB1=/2일 때 APB1 timer clock은 x2 규칙으로 64 MHz다. 1 MHz tick은 PSC=63이며, PSC=71은 72 MHz 설정에만 맞는다.
5. **SPI1 설명**: Master–Slave1은 DMA1 CH2(RX)/CH3(TX)를 사용하지만 완료 플래그를 기다리는 동기식 DMA다. Slave1 쪽 frame exchange는 polling이다. “완전 비차단 DMA”로 표현하지 않는다.
6. **Slave2 첫 바이트**: NSS 선택 직후 `0xC7`을 DR에 preload하고 이후 RXNE마다 다음 바이트를 적재한다. 첫 clock 뒤에 header를 넣으면 응답이 한 바이트 밀린다.
7. **timeout**: CPU loop 횟수 5,000회를 시간으로 간주하던 코드를 `HAL_GetTick()`의 unsigned 차이로 변경해 clock/optimization 의존성과 tick wrap 문제를 줄였다.
8. **날짜**: GMT→KST 변환과 예보 base time 보정에 월말·연말 rollover 및 Gregorian leap year 규칙을 적용했다.
9. **강수 상태**: `G_Rain_Type`의 bool 취급을 없애고 PTY 코드 0~4를 enum으로 명시했다. LCD는 NONE/RAIN/RAIN-SNOW/SNOW/SHOWER를 구분할 수 있다.
10. **초기화**: 동일 peripheral을 HAL과 register 코드로 두 번 초기화하지 않는다. CubeMX가 clock/GPIO를 맡고, 사용자 모듈은 runtime 동작을 맡는 경계를 권장한다.

## 권장 CubeMX 설정

아래 설정을 각 보드의 CubeMX 프로젝트에 적용한다.

### Slave1

- SYSCLK 64 MHz, APB1 32 MHz
- ADC1: scan continuous, CH0 water / CH1 humidity, DMA circular, halfword
- SPI1 slave: Mode 0, 8-bit, MSB first, hardware NSS PA4
- TIM2 CH2 fan PWM: 1 MHz tick, ARR=999
- TIM3 CH1/PB4 + CH3/PB0 partial remap: 1 MHz tick, ARR=19999
- PA10 input pull-up: active-low umbrella IR

### Master

- SPI1 master: Mode 0, software NSS GPIO PA4, DMA1 CH2/CH3
- SPI2 master: Mode 0, software NSS GPIO PB12
- PC0: Slave2 water-trigger pulse
- UART2 115200: debug and ML log

### Slave2

- ESP8266 UART and debug UART는 실제 배선에 맞춰 지정
- SPI1 slave Mode 0; NSS falling/RXNE에서 response state machine 구동
- I2C1: PCF8574 LCD backpack 주소 확인
- TIM2 PWM: water-level dial servo

## Slave2 ISR 연결 예

```c
void EXTI4_IRQHandler(void) {
    if (EXTI->PR & EXTI_PR_PR4) {
        EXTI->PR = EXTI_PR_PR4;
        *(__IO uint8_t *)&SPI1->DR = slave2_spi_on_select(&g_spi_state);
    }
}

void SPI1_IRQHandler(void) {
    if (SPI1->SR & SPI_SR_RXNE) {
        uint8_t rx = *(__IO uint8_t *)&SPI1->DR;
        *(__IO uint8_t *)&SPI1->DR = slave2_spi_on_byte(&g_spi_state, rx);
    }
}
```

IRQ 우선순위와 NSS/EXTI 핀은 실제 보드 설정에 따라 바꿔야 한다.

## 측정값 해석

2,264 cycles / 64 MHz = 35.375 µs, 1,171 cycles / 64 MHz = 18.297 µs다. 감소율은 `(2264-1171)/2264 ≈ 48.3%`다. 원 보고서의 `35.4s`, `18.3s`는 단위 오기이며 µs가 맞다.
