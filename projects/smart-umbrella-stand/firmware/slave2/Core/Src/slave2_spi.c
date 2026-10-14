#include <string.h>
#include "slave2_spi.h"

void slave2_spi_init(Slave2Spi *state)
{
    memset(state, 0, sizeof(*state));
}

uint8_t slave2_spi_on_select(Slave2Spi *state)
{
    state->index = 0u;
    memset(state->rx, 0, sizeof(state->rx));
    state->tx[0] = WEATHER_RSP_HEADER;
    state->tx[1] = state->rain_probability <= 100u ? state->rain_probability : 100u;
    state->tx[2] = (uint8_t)state->precipitation;
    state->tx[3] = 0u;
    return state->tx[0]; /* Write this to SPI->DR before the first SCK edge. */
}

uint8_t slave2_spi_on_byte(Slave2Spi *state, uint8_t received)
{
    const uint8_t i = state->index;
    if (i >= WEATHER_FRAME_SIZE) return 0xffu;
    if (i < WEATHER_FRAME_SIZE) state->rx[i] = received;
    if (state->index < WEATHER_FRAME_SIZE) ++state->index;
    if (state->index == WEATHER_FRAME_SIZE && state->rx[0] == WEATHER_CMD_HEADER) {
        state->servo_level = state->rx[1] <= 4u ? state->rx[1] : 0u;
        state->led_water_full = state->rx[2] != 0u;
        state->led_rainy = state->rx[3] != 0u;
    }
    return state->index < WEATHER_FRAME_SIZE ? state->tx[state->index] : 0xffu;
}
