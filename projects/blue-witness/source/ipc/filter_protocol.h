#ifndef FILTER_PROTOCOL_H
#define FILTER_PROTOCOL_H

#include <stdint.h>

typedef struct
{
    uint32_t station_id;
    uint16_t sequence_number;
    int64_t detection_time;
    float x_mm;
    float y_mm;

} accident_event_t;

#endif
