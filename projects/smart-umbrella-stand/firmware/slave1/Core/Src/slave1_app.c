/* STM32F103 adapter. ADC1 DMA circular mode is configured by CubeMX/startup. */
#include <string.h>
#include "main.h"
#include "slave1_app.h"

static bool elapsed(uint32_t start, uint32_t timeout_ms)
{
    return (uint32_t)(HAL_GetTick() - start) >= timeout_ms;
}

void slave1_app_init(Slave1App *app)
{
    memset(app, 0, sizeof(*app));
}

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

void slave1_timers_init_64mhz(void)
{
    /* SYSCLK=64 MHz, APB1=/2: timer clock is still 64 MHz (x2 rule). */
    TIM3->PSC = 63u;       /* 1 MHz tick */
    TIM3->ARR = 19999u;    /* 50 Hz servo PWM */
    TIM2->PSC = 63u;       /* 1 MHz tick */
    TIM2->ARR = 999u;      /* 1 kHz fan PWM */
}
