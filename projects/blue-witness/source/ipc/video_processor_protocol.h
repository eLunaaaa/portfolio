#ifndef VIDEO_PROCESSOR_PROTOCOL_H
#define VIDEO_PROCESSOR_PROTOCOL_H

#include <stdint.h>

#define VIDEO_PROCESSOR_SOCKET_PATH "/tmp/video_processor_event.sock"

/* 사고차량 Sensor가 보내는 원본 사고 Event다.
 * detection_time은 DENM과 같은 ITS epoch millisecond 단위다. */
typedef struct {
    uint32_t station_id;
    uint16_t sequence_number;
    int64_t detection_time;
} video_processor_event_t;

_Static_assert(sizeof(video_processor_event_t) == 16U,
               "video_processor_event_t wire size changed");

#endif
