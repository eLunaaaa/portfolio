#ifndef SMART_UMBRELLA_PROTOCOL_H
#define SMART_UMBRELLA_PROTOCOL_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#define SENSOR_FRAME_HEADER 0xA5u
#define SENSOR_FRAME_SIZE   6u
#define WEATHER_CMD_HEADER  0xB6u
#define WEATHER_RSP_HEADER  0xC7u
#define WEATHER_FRAME_SIZE  4u

typedef struct {
    uint16_t humidity_raw;
    uint16_t water_raw;
    bool umbrella_present;
} SensorData;

typedef enum {
    FAN_CMD_MEDIUM = 3u,
    FAN_CMD_HIGH = 4u,
    FAN_CMD_OFF = 5u,
} FanCommand;

typedef enum {
    PRECIP_NONE = 0u,
    PRECIP_RAIN = 1u,
    PRECIP_RAIN_SNOW = 2u,
    PRECIP_SNOW = 3u,
    PRECIP_SHOWER = 4u,
} PrecipitationType;

void sensor_frame_encode(uint8_t out[SENSOR_FRAME_SIZE], const SensorData *data);
bool sensor_frame_decode(SensorData *out, const uint8_t frame[SENSOR_FRAME_SIZE]);
bool weather_response_decode(uint8_t *rain_probability,
                             PrecipitationType *type,
                             const uint8_t frame[WEATHER_FRAME_SIZE]);
const char *precipitation_label(PrecipitationType type);

#endif

