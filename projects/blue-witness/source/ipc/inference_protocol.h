#ifndef INFERENCE_PROTOCOL_H
#define INFERENCE_PROTOCOL_H

#include <stdint.h>

#define INFERENCE_SOCKET_PATH "/tmp/inference_event.sock"

/* Witness Filter가 AI Inference에 전달하는 사고 후보 Event다.
 * detection_time은 Unix epoch nanoseconds다. */
typedef struct
{
    uint32_t station_id;
    uint16_t sequence_number;
    int64_t detection_time;
} inference_event_t;

#endif
