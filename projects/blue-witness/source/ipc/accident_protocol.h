#ifndef ACCIDENT_PROTOCOL_H
#define ACCIDENT_PROTOCOL_H

#include <stdint.h>

/* 사고차량 Sensor가 Broadcast Sender에 전달하는 내부 사고 Event다.
 * station_id는 1, sequence_number는 사고 한 건마다 1..65535 순환한다.
 * Video Processor에도 같은 ID와 detection_time을 보낸다.
 * detection_time: ITS epoch ms, heading_value: 0.1도, X/Y: mm. */
typedef struct
{
    uint32_t station_id;
    uint16_t sequence_number;

    int64_t detection_time;
    uint16_t heading_value;
    int32_t local_x_mm;
    int32_t local_y_mm;
} accident_ipc_event_t;

#ifdef __cplusplus
static_assert(sizeof(accident_ipc_event_t) == 32U,
              "accident_ipc_event_t wire size changed");
#else
_Static_assert(sizeof(accident_ipc_event_t) == 32U,
               "accident_ipc_event_t wire size changed");
#endif

#endif
