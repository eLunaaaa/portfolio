/*
 * 스마트 우산꽂이 Master의 STM32 SPI 통신 어댑터입니다.
 * SPI1 DMA로 Slave1 센서·구동 명령을, SPI2 polling으로 Slave2 날씨·표시 명령을 교환합니다.
 * 전송 시간과 종료 정리를 관리하고 센서 해석은 common protocol 함수를 사용합니다.
 */

/* STM32F103 adapter. Add this file to the CubeIDE Master project. */
#include "main.h"
#include "stm32_transport.h"

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
 * 기능: Master와 Slave1 사이에서 제어 명령과 센서값을 동시에 교환합니다.
 * 입력: servo_command/fan_command: 이번 명령, sensor: 수신 센서 저장 공간, timeout_ms: 전체 교환 제한 시간.
 * 출력: DMA 교환과 프레임 검증이 성공하면 true, 실패하면 false.
 * 처리 구조: 6바이트 TX/RX DMA를 설정하고 NSS를 내린 뒤 완료·SPI busy 해제를 기다립니다. 모든 종료 경로에서 DMA와 NSS를 정리합니다.
 */
bool spi1_exchange_sensor_dma(uint8_t servo_command, FanCommand fan_command,
                              SensorData *sensor, uint32_t timeout_ms)
{
    uint8_t tx[SENSOR_FRAME_SIZE] = {servo_command, (uint8_t)fan_command,
                                     0xffu, 0xffu, 0xffu, 0xffu};
    uint8_t rx[SENSOR_FRAME_SIZE] = {0};
    uint32_t start = HAL_GetTick();
    bool ok = false;

    DMA1_Channel2->CCR &= ~DMA_CCR_EN; /* SPI1 RX */
    DMA1_Channel3->CCR &= ~DMA_CCR_EN; /* SPI1 TX */
    DMA1->IFCR = DMA_IFCR_CGIF2 | DMA_IFCR_CGIF3;

    DMA1_Channel2->CPAR = (uint32_t)&SPI1->DR;
    DMA1_Channel2->CMAR = (uint32_t)rx;
    DMA1_Channel2->CNDTR = SENSOR_FRAME_SIZE;
    DMA1_Channel2->CCR = DMA_CCR_MINC;

    DMA1_Channel3->CPAR = (uint32_t)&SPI1->DR;
    DMA1_Channel3->CMAR = (uint32_t)tx;
    DMA1_Channel3->CNDTR = SENSOR_FRAME_SIZE;
    DMA1_Channel3->CCR = DMA_CCR_MINC | DMA_CCR_DIR;

    GPIOA->BRR = GPIO_BRR_BR4; /* Slave1 NSS low */
    SPI1->CR2 |= SPI_CR2_RXDMAEN | SPI_CR2_TXDMAEN;
    DMA1_Channel2->CCR |= DMA_CCR_EN;
    DMA1_Channel3->CCR |= DMA_CCR_EN;

    while ((DMA1->ISR & DMA_ISR_TCIF2) == 0u && !elapsed(start, timeout_ms)) {}
    if ((DMA1->ISR & DMA_ISR_TCIF2) == 0u) goto done;
    while ((DMA1->ISR & DMA_ISR_TCIF3) == 0u && !elapsed(start, timeout_ms)) {}
    if ((DMA1->ISR & DMA_ISR_TCIF3) == 0u) goto done;
    while ((SPI1->SR & SPI_SR_BSY) != 0u && !elapsed(start, timeout_ms)) {}
    if ((SPI1->SR & SPI_SR_BSY) != 0u) goto done;
    ok = sensor_frame_decode(sensor, rx);

done:
    DMA1_Channel2->CCR &= ~DMA_CCR_EN;
    DMA1_Channel3->CCR &= ~DMA_CCR_EN;
    SPI1->CR2 &= ~(SPI_CR2_RXDMAEN | SPI_CR2_TXDMAEN);
    GPIOA->BSRR = GPIO_BSRR_BS4; /* NSS high on every exit path */
    return ok;
}

/**
 * 기능: Master와 Slave2 사이에서 날씨·표시 명령 4바이트를 polling으로 교환합니다.
 * 입력: command: 송신 4바이트, response: 수신 4바이트 공간, timeout_ms: 제한 시간.
 * 출력: 교환이 완료되면 true, 제한 시간을 넘으면 false.
 * 처리 구조: NSS를 내리고 TXE/RXNE를 바이트마다 확인합니다. 실패 시 NSS 해제와 SPI 수신 상태 정리 후 반환합니다.
 */
bool spi2_exchange_weather(const uint8_t command[WEATHER_FRAME_SIZE],
                           uint8_t response[WEATHER_FRAME_SIZE],
                           uint32_t timeout_ms)
{
    uint32_t start = HAL_GetTick();
    GPIOB->BRR = GPIO_BRR_BR12; /* Slave2 NSS low */

    for (uint8_t i = 0u; i < WEATHER_FRAME_SIZE; ++i) {
        while ((SPI2->SR & SPI_SR_TXE) == 0u) {
            if (elapsed(start, timeout_ms)) goto error;
        }
        *(__IO uint8_t *)&SPI2->DR = command[i];
        while ((SPI2->SR & SPI_SR_RXNE) == 0u) {
            if (elapsed(start, timeout_ms)) goto error;
        }
        response[i] = *(__IO uint8_t *)&SPI2->DR;
    }
    while ((SPI2->SR & SPI_SR_BSY) != 0u) {
        if (elapsed(start, timeout_ms)) goto error;
    }
    GPIOB->BSRR = GPIO_BSRR_BS12;
    return true;

error:
    GPIOB->BSRR = GPIO_BSRR_BS12;
    SPI2->CR1 &= ~SPI_CR1_SPE;
    while ((SPI2->SR & SPI_SR_RXNE) != 0u) (void)SPI2->DR;
    SPI2->CR1 |= SPI_CR1_SPE;
    return false;
}
