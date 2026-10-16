#ifndef P2P_PROTOCOL_H
#define P2P_PROTOCOL_H

#include <stdint.h>

typedef struct
{
    uint32_t station_id;
    uint16_t sequence_number;

    int64_t detection_time;
    int64_t reference_time;

    int32_t latitude;
    int32_t longitude;

    uint16_t speed_value;
    uint16_t heading_value;

    int32_t delta_latitude;
    int32_t delta_longitude;
    int32_t delta_altitude;

    int32_t local_x_mm;
    int32_t local_y_mm;

} accident_event_payload_t;

#endif
