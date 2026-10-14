#include "protocol.h"

void sensor_frame_encode(uint8_t out[SENSOR_FRAME_SIZE], const SensorData *data)
{
    out[0] = SENSOR_FRAME_HEADER;
    out[1] = (uint8_t)(data->humidity_raw & 0xffu);
    out[2] = (uint8_t)(data->humidity_raw >> 8);
    out[3] = (uint8_t)(data->water_raw & 0xffu);
    out[4] = (uint8_t)(data->water_raw >> 8);
    out[5] = data->umbrella_present ? 1u : 0u;
}

bool sensor_frame_decode(SensorData *out, const uint8_t frame[SENSOR_FRAME_SIZE])
{
    if (out == NULL || frame == NULL || frame[0] != SENSOR_FRAME_HEADER) {
        return false;
    }

    const uint16_t humidity = (uint16_t)frame[1] | ((uint16_t)frame[2] << 8);
    const uint16_t water = (uint16_t)frame[3] | ((uint16_t)frame[4] << 8);
    if (humidity > 4095u || water > 4095u || frame[5] > 1u) return false;
    out->humidity_raw = humidity;
    out->water_raw = water;
    out->umbrella_present = frame[5] != 0u;
    return true;
}

bool weather_response_decode(uint8_t *rain_probability,
                             PrecipitationType *type,
                             const uint8_t frame[WEATHER_FRAME_SIZE])
{
    if (rain_probability == NULL || type == NULL || frame == NULL ||
        frame[0] != WEATHER_RSP_HEADER || frame[1] > 100u ||
        frame[2] > (uint8_t)PRECIP_SHOWER) {
        return false;
    }
    *rain_probability = frame[1];
    *type = (PrecipitationType)frame[2];
    return true;
}

const char *precipitation_label(PrecipitationType type)
{
    switch (type) {
    case PRECIP_RAIN: return "RAIN";
    case PRECIP_RAIN_SNOW: return "RAIN/SNOW";
    case PRECIP_SNOW: return "SNOW";
    case PRECIP_SHOWER: return "SHOWER";
    case PRECIP_NONE:
    default: return "NONE";
    }
}

