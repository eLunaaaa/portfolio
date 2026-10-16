#ifndef UPLOADER_EVENT_ID_H
#define UPLOADER_EVENT_ID_H

#include <stddef.h>
#include <stdint.h>

/*
 * Unix epoch nanosecond 사고 시각과 사고 차량 ID로 서버 event_id를 생성함.
 * 표준 형식: EVT_YYYYMMDD_HHMMSSmmm_CARXX (시간대: Asia/Seoul, UTC+9)
 */
int event_id_build(
    char *output,
    size_t output_size,
    int64_t event_time_ns,
    const char *accident_vehicle_id,
    char *error,
    size_t error_size);

#endif
