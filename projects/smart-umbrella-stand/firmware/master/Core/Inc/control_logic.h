#ifndef SMART_UMBRELLA_CONTROL_LOGIC_H
#define SMART_UMBRELLA_CONTROL_LOGIC_H

#include <stdbool.h>
#include <stdint.h>
#include "protocol.h"

typedef struct {
    float humidity_mean;
    bool mean_initialized;
    bool wet_latched;
    bool rainy_latched;
} ControlState;

typedef struct {
    uint8_t servo_level;
    FanCommand fan_command;
    bool wet;
    bool rainy;
} ControlOutput;

float humidity_raw_to_percent(uint16_t raw);
uint8_t water_raw_to_percent(uint16_t raw);
void control_update(ControlState *state, const SensorData *sensor,
                    uint8_t rain_probability, PrecipitationType precipitation,
                    ControlOutput *out);

#endif
