/* STM32F103 adapter. Add this file to the CubeIDE Master project. */
#include "main.h"
#include "stm32_transport.h"

static bool elapsed(uint32_t start, uint32_t timeout_ms)
{
    return (uint32_t)(HAL_GetTick() - start) >= timeout_ms;
}

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

