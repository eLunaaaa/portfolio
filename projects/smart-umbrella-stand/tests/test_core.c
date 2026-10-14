#include <assert.h>
#include <stdio.h>
#include "date_utils.h"
#include "protocol.h"
#include "control_logic.h"
#include "slave2_spi.h"

static void test_sensor_frame(void)
{
    SensorData source = {1234u, 4095u, true}, decoded = {0};
    uint8_t frame[SENSOR_FRAME_SIZE];
    sensor_frame_encode(frame, &source);
    assert(sizeof(frame) == 6u);
    assert(sensor_frame_decode(&decoded, frame));
    assert(decoded.humidity_raw == 1234u);
    assert(decoded.water_raw == 4095u);
    assert(decoded.umbrella_present);
    frame[5] = 2u;
    assert(!sensor_frame_decode(&decoded, frame));
    assert(decoded.humidity_raw == 1234u && decoded.umbrella_present);
    frame[5] = 0u;
    frame[2] = 0xffu;
    assert(!sensor_frame_decode(&decoded, frame));
    assert(decoded.humidity_raw == 1234u && decoded.umbrella_present);
    frame[0] = 0u;
    assert(!sensor_frame_decode(&decoded, frame));
}

static void test_dates(void)
{
    DateTime leap = {2024u, 2u, 28u, 23u, 0u, 0u};
    assert(datetime_add_hours(&leap, 1));
    assert(leap.month == 2u && leap.day == 29u && leap.hour == 0u);
    assert(datetime_add_hours(&leap, 24));
    assert(leap.month == 3u && leap.day == 1u);
    DateTime year = {2025u, 12u, 31u, 20u, 0u, 0u};
    assert(datetime_add_hours(&year, 9));
    assert(year.year == 2026u && year.month == 1u && year.day == 1u && year.hour == 5u);
}

static void test_control(void)
{
    ControlState state = {0};
    ControlOutput output;
    SensorData wet = {260u, 20u, true};
    control_update(&state, &wet, 70u, PRECIP_RAIN, &output);
    assert(output.wet && output.rainy);
    assert(output.fan_command == FAN_CMD_HIGH && output.servo_level == 0u);

    /* Example model score is zero at 50%: remain wet inside the hold band. */
    SensorData hold = {135u, 20u, true};
    control_update(&state, &hold, 50u, PRECIP_NONE, &output);
    assert(output.wet);

    /* A sufficiently low model score crosses the dry release threshold. */
    SensorData dry = {10u, 20u, true};
    control_update(&state, &dry, 30u, PRECIP_NONE, &output);
    assert(!output.wet);
}

static void test_slave2_preload(void)
{
    Slave2Spi state;
    slave2_spi_init(&state);
    state.rain_probability = 75u;
    state.precipitation = PRECIP_RAIN;
    assert(slave2_spi_on_select(&state) == WEATHER_RSP_HEADER);
    assert(slave2_spi_on_byte(&state, WEATHER_CMD_HEADER) == 75u);
    assert(slave2_spi_on_byte(&state, 3u) == (uint8_t)PRECIP_RAIN);
    assert(slave2_spi_on_byte(&state, 1u) == 0u);
    assert(slave2_spi_on_byte(&state, 1u) == 0xffu);
    assert(state.servo_level == 3u && state.led_water_full && state.led_rainy);
    state.servo_level = 2u;
    assert(slave2_spi_on_byte(&state, 0u) == 0xffu);
    assert(state.servo_level == 2u);
}

int main(void)
{
    test_sensor_frame();
    test_dates();
    test_control();
    test_slave2_preload();
    puts("all core tests passed");
    return 0;
}
