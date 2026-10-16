#ifndef SENSOR_PROTOCOL_H
#define SENSOR_PROTOCOL_H

#include <stdint.h>

typedef struct
{
    int64_t detection_time;
    float x_mm;
    float y_mm;
    float heading_deg;
} vehicle_state_t;

#endif
