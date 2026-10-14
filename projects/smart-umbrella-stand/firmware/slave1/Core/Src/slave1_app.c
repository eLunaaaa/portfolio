/*
 * 스마트 우산꽂이 Slave1의 센싱·팬·승강 서보 코드입니다.
 * ADC DMA snapshot의 이동평균을 유지하고 SPI로 센서값과 Master 명령을 교환합니다.
 * 센서 상태 관리와 실제 TIM/GPIO 구동을 한 보드 어댑터에 묶었습니다.
 */

/* STM32F103 adapter. ADC1 DMA circular mode is configured by CubeMX/startup. */
#include <string.h>
#include "main.h"
#include "slave1_app.h"

/**
 * 기능: 주변장치 대기의 제한 시간이 지났는지 판단합니다.
 * 입력: start: 대기 시작 tick, timeout_ms: 허용 시간(ms).
 * 출력: 제한 시간이 지나면 true, 아직 남았으면 false.
 * 처리 구조: HAL_GetTick()과 시작 tick의 unsigned 차이를 사용해 tick rollover에도 같은 방식으로 비교합니다.
 */
static bool elapsed(uint32_t start, uint32_t timeout_ms)
{
    return (uint32_t)(HAL_GetTick() - start) >= timeout_ms;
}

/**
 * 기능: Slave1 센서 필터 상태를 초기화합니다.
 * 입력: app: Slave1App 저장 공간.
 * 출력: 필터 이력·합계·인덱스·센서값을 0으로 초기화합니다.
 * 처리 구조: memset으로 누적 상태를 비워 첫 ADC 표본부터 이동평균을 계산할 준비를 합니다.
 */
void slave1_app_init(Slave1App *app)
{
    memset(app, 0, sizeof(*app));
}

/**
 * 기능: ADC DMA 표본으로 습도·수위 이동평균과 우산 상태를 갱신합니다.
 * 입력: app: 이전 필터 상태, water_dma/humidity_dma: 현재 ADC 표본, umbrella_present: IR 감지 결과.
 * 출력: app의 평균 센서값과 원형 필터 상태가 갱신됩니다.
 * 처리 구조: 원형 버퍼의 이전 값을 합계에서 빼고 새 값을 더합니다. 초기에는 실제 수신 표본 수로 나눠 평균을 구합니다.
 */
void slave1_app_update(Slave1App *app, uint16_t water_dma,
                       uint16_t humidity_dma, bool umbrella_present)
{
    const uint8_t i = app->index;
    app->humidity_sum -= app->humidity_history[i];
    app->water_sum -= app->water_history[i];
    app->humidity_history[i] = humidity_dma;
    app->water_history[i] = water_dma;
    app->humidity_sum += humidity_dma;
    app->water_sum += water_dma;
    if (app->samples < ADC_FILTER_TAPS) ++app->samples;
    app->index = (uint8_t)((i + 1u) % ADC_FILTER_TAPS);

    app->sensor.humidity_raw = (uint16_t)(app->humidity_sum / app->samples);
    app->sensor.water_raw = (uint16_t)(app->water_sum / app->samples);
    app->sensor.umbrella_present = umbrella_present;
}

/**
 * 기능: Master의 팬·서보 명령을 실제 출력 레지스터에 반영합니다.
 * 입력: servo_level: 승강 위치 0~2, fan_command: 중풍/강풍/정지.
 * 출력: TIM3 서보 compare, TIM2 팬 duty, 모터 방향 GPIO를 변경합니다.
 * 처리 구조: 승강 위치를 1000/1500/2000 us 펄스로 바꾸고 팬 명령에 따라 duty 500/900/0을 선택합니다.
 */
void slave1_apply_commands(uint8_t servo_level, FanCommand fan_command)
{
    static const uint16_t servo_us[] = {1000u, 1500u, 2000u};
    if (servo_level > 2u) servo_level = 0u;
    TIM3->CCR1 = servo_us[servo_level];
    TIM3->CCR3 = servo_us[servo_level];

    if (fan_command == FAN_CMD_MEDIUM) {
        GPIOC->BSRR = GPIO_BSRR_BS1;
        GPIOC->BRR = GPIO_BRR_BR0;
        TIM2->CCR2 = 500u;
    } else if (fan_command == FAN_CMD_HIGH) {
        GPIOC->BSRR = GPIO_BSRR_BS1;
        GPIOC->BRR = GPIO_BRR_BR0;
        TIM2->CCR2 = 900u;
    } else {
        GPIOC->BRR = GPIO_BRR_BR1 | GPIO_BRR_BR0;
        TIM2->CCR2 = 0u;
    }
}

/**
 * 기능: Slave1 센서 프레임을 보내면서 Master의 구동 명령을 받습니다.
 * 입력: app: 전송할 센서값, timeout_ms: 선택·바이트 대기 제한 시간.
 * 출력: 정상 6바이트 교환 시 수신한 팬·서보 명령을 적용합니다.
 * 처리 구조: 첫 헤더를 미리 DR에 넣고 NSS·RXNE를 기다립니다. 첫 두 RX 바이트를 명령으로 해석하고 다음 센서 바이트를 적재합니다.
 */
void slave1_spi_exchange_polling(const Slave1App *app, uint32_t timeout_ms)
{
    uint8_t tx[SENSOR_FRAME_SIZE];
    uint8_t servo = 0u;
    FanCommand fan = FAN_CMD_OFF;
    uint32_t start = HAL_GetTick();
    sensor_frame_encode(tx, &app->sensor);

    /* Preload before NSS falls so the first byte cannot be shifted by a race. */
    *(__IO uint8_t *)&SPI1->DR = tx[0];
    while ((GPIOA->IDR & GPIO_IDR_IDR4) != 0u) {
        if (elapsed(start, timeout_ms)) return;
    }
    for (uint8_t i = 0u; i < SENSOR_FRAME_SIZE; ++i) {
        while ((SPI1->SR & SPI_SR_RXNE) == 0u) {
            if (elapsed(start, timeout_ms)) return;
        }
        const uint8_t rx = *(__IO uint8_t *)&SPI1->DR;
        if (i == 0u && rx <= 2u) servo = rx;
        if (i == 1u && (rx == 3u || rx == 4u || rx == 5u)) fan = (FanCommand)rx;
        if (i + 1u < SENSOR_FRAME_SIZE) {
            while ((SPI1->SR & SPI_SR_TXE) == 0u) {
                if (elapsed(start, timeout_ms)) return;
            }
            *(__IO uint8_t *)&SPI1->DR = tx[i + 1u];
        }
    }
    slave1_apply_commands(servo, fan);
}

/**
 * 기능: 64 MHz 타이머 입력에 맞춰 팬·서보 PWM 주기를 설정합니다.
 * 입력: 없음. MCU의 clock/GPIO 설정이 적용된 상태에서 호출합니다.
 * 출력: TIM3은 50 Hz 서보, TIM2는 1 kHz 팬 PWM의 prescaler/ARR를 설정합니다.
 * 처리 구조: PSC=63으로 1 MHz tick을 만들고 ARR=19999/999를 각각 적용합니다.
 */
void slave1_timers_init_64mhz(void)
{
    /* SYSCLK=64 MHz, APB1=/2: timer clock is still 64 MHz (x2 rule). */
    TIM3->PSC = 63u;       /* 1 MHz tick */
    TIM3->ARR = 19999u;    /* 50 Hz servo PWM */
    TIM2->PSC = 63u;       /* 1 MHz tick */
    TIM2->ARR = 999u;      /* 1 kHz fan PWM */
}
