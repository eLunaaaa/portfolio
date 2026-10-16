#ifndef TIME_SYNC_PROTOCOL_H
#define TIME_SYNC_PROTOCOL_H

/**
 * @file time_sync_protocol.h
 * @brief 다른 프로세스가 시간 동기화 상태를 조회할 때 사용하는 공용 IPC 규약.
 *
 * 내부 스레드 큐는 외부 API가 아니므로 이 파일에 공개하지 않음. 이 헤더에는
 * 팀 IPC 라이브러리의 Header 다음에 실제로 전송되는 Payload만 정의함.
 */

#include <stdint.h>

#define TIME_SYNC_PROTOCOL_VERSION 1U
#define TIME_SYNC_SOURCE_SIZE 64U
#define TIME_SYNC_LEAP_SIZE 24U
#define TIME_SYNC_DETAIL_SIZE 128U

typedef enum {
    TIME_SYNC_UNKNOWN = 0,
    TIME_SYNC_SYNCING = 1,
    TIME_SYNC_SYNCED = 2,
    TIME_SYNC_UNSYNCED = 3,
    TIME_SYNC_ERROR = 4
} time_sync_state_t;

/** 상태 조회 요청. 현재는 버전만 확인하고 최신 측정값을 즉시 반환함. */
typedef struct {
    uint32_t version;
    uint32_t reserved;
} time_sync_request_t;

/**
 * 시간 동기화 상태 응답.
 * 문자열은 항상 NUL 종료되며 offset/delay/dispersion 단위는 밀리초임.
 */
typedef struct {
    uint32_t version;
    uint32_t state;
    uint32_t stratum;
    uint32_t generation;

    int64_t sampled_realtime_ns;

    double system_offset_ms;
    double last_offset_ms;
    double rms_offset_ms;
    double root_delay_ms;
    double root_dispersion_ms;
    double update_interval_s;

    char source[TIME_SYNC_SOURCE_SIZE];
    char leap_status[TIME_SYNC_LEAP_SIZE];
    char detail[TIME_SYNC_DETAIL_SIZE];
} time_sync_status_t;

_Static_assert(sizeof(time_sync_request_t) == 8U,
               "time_sync_request_t wire size changed");
_Static_assert(sizeof(time_sync_status_t) == 288U,
               "time_sync_status_t wire size changed");

#endif
