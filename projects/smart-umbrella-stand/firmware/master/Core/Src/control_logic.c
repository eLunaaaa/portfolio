#include "control_logic.h"
#include "wet_model_params.h"

#define HUMIDITY_RAW_DRY 10u
#define HUMIDITY_RAW_WET 260u
#define WATER_RAW_EMPTY 15u
#define WATER_RAW_FULL 260u
/* sigmoid(z) >= 0.60 and <= 0.40 expressed in logit space. */
#define WET_Z_ON  0.4054651f
#define WET_Z_OFF (-0.4054651f)

float humidity_raw_to_percent(uint16_t raw)
{
    if (raw <= HUMIDITY_RAW_DRY) return 0.0f;
    if (raw >= HUMIDITY_RAW_WET) return 100.0f;
    return ((float)(raw - HUMIDITY_RAW_DRY) * 100.0f) /
           (float)(HUMIDITY_RAW_WET - HUMIDITY_RAW_DRY);
}

uint8_t water_raw_to_percent(uint16_t raw)
{
    if (raw <= WATER_RAW_EMPTY) return 0u;
    if (raw >= WATER_RAW_FULL) return 100u;
    return (uint8_t)(((uint32_t)(raw - WATER_RAW_EMPTY) * 100u) /
                     (WATER_RAW_FULL - WATER_RAW_EMPTY));
}

static float model_score(float humidity, bool umbrella, float mean)
{
    const float h_norm = (humidity / 100.0f - 0.5f) * 2.0f;
    const float mean_norm = (mean / 100.0f - 0.5f) * 2.0f;
    const float dh_norm = (humidity - mean) / 50.0f;
    return WET_W0 * h_norm + WET_W1 * (umbrella ? 1.0f : 0.0f) +
           WET_W2 * mean_norm + WET_W3 * dh_norm + WET_BIAS;
}

void control_update(ControlState *state, const SensorData *sensor,
                    uint8_t rain_probability, PrecipitationType precipitation,
                    ControlOutput *out)
{
    const float humidity = humidity_raw_to_percent(sensor->humidity_raw);
    if (!state->mean_initialized) {
        state->humidity_mean = humidity;
        state->mean_initialized = true;
    } else {
        state->humidity_mean = 0.9f * state->humidity_mean + 0.1f * humidity;
    }

    if (!state->rainy_latched && rain_probability >= 60u) state->rainy_latched = true;
    else if (state->rainy_latched && rain_probability <= 40u) state->rainy_latched = false;

    out->rainy = state->rainy_latched || precipitation != PRECIP_NONE;

    /* Sensor-based wet/dry judgment comes only from the trained model score.
       40-60% is a hold band that preserves the previous state. */
    const float wet_score = model_score(humidity, sensor->umbrella_present,
                                        state->humidity_mean);
    if (!state->wet_latched && wet_score >= WET_Z_ON) {
        state->wet_latched = true;
    } else if (state->wet_latched && wet_score <= WET_Z_OFF) {
        state->wet_latched = false;
    }
    out->wet = state->wet_latched;

    if (!sensor->umbrella_present) {
        out->servo_level = 0u;
        out->fan_command = out->wet ? FAN_CMD_MEDIUM : FAN_CMD_OFF;
    } else if (out->wet) {
        out->servo_level = 0u;
        out->fan_command = out->rainy ? FAN_CMD_HIGH : FAN_CMD_MEDIUM;
    } else {
        out->servo_level = out->rainy ? 2u : 1u;
        out->fan_command = FAN_CMD_OFF;
    }
}
