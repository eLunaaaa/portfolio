#ifndef SMART_UMBRELLA_SLAVE2_SPI_H
#define SMART_UMBRELLA_SLAVE2_SPI_H

#include <stdint.h>
#include "protocol.h"

typedef struct {
    uint8_t tx[WEATHER_FRAME_SIZE];
    uint8_t rx[WEATHER_FRAME_SIZE];
    uint8_t index;
    uint8_t rain_probability;
    PrecipitationType precipitation;
    uint8_t servo_level;
    bool led_water_full;
    bool led_rainy;
} Slave2Spi;

void slave2_spi_init(Slave2Spi *state);
uint8_t slave2_spi_on_select(Slave2Spi *state);
uint8_t slave2_spi_on_byte(Slave2Spi *state, uint8_t received);

#endif
