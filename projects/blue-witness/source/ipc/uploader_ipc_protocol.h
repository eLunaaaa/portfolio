#ifndef UPLOADER_IPC_PROTOCOL_H
#define UPLOADER_IPC_PROTOCOL_H

/**
 * @file uploader_ipc_protocol.h
 * @brief Camera와 단일 uploader 프로세스 사이의 외부 IPC 규약.
 *
 * 스레드 내부 생산자-소비자 자료구조는 이 헤더에 공개하지 않음. 이 파일에는
 * Unix Domain Socket을 실제로 통과하는 CAMERA_READY와 응답만 정의함.
 */

#include <stdint.h>

#define UPLOADER_PROTOCOL_VERSION 7U
#define UPLOADER_PATH_SIZE 512U
#define UPLOADER_ERROR_SIZE 256U
#define UPLOADER_ACTION_ID_SIZE 65U
#define UPLOADER_VEHICLE_ID_SIZE 65U
#define UPLOADER_TIME_SYNC_STATUS_SIZE 16U
#define UPLOADER_PAUSE_REASON_SIZE 96U

/*
 * Camera/AI/V2V 연동 코드가 업로더 내부 헤더를 include하지 않아도 되도록
 * 외부 IPC endpoint를 공용 wire 규약과 같은 헤더에 둠.
 */
#define UPLOADER_SOCKET_PATH "/tmp/uploader.sock"

enum uploader_message_type {
    UPLOADER_MSG_RESPONSE = 0x7001,
    UPLOADER_MSG_PAUSE = 0x7002,
    UPLOADER_MSG_RESUME = 0x7003,
    UPLOADER_MSG_CONTROL_QUERY = 0x7004,
    UPLOADER_MSG_CONTROL_STATE = 0x7005
};

enum uploader_response_code {
    UPLOADER_RESPONSE_OK = 0,
    UPLOADER_RESPONSE_INVALID_REQUEST = 1,
    UPLOADER_RESPONSE_INTERNAL_ERROR = 2,
    UPLOADER_RESPONSE_SOURCE_NOT_READY = 3,
    UPLOADER_RESPONSE_INSUFFICIENT_SPACE = 4,
    UPLOADER_RESPONSE_STORAGE_TEMPORARY_ERROR = 5,
    UPLOADER_RESPONSE_QUEUE_DEFERRED = 6
};

/**
 * Camera가 close/fsync/rename을 끝낸 최종 MP4 경로와 사고 정보를 전달함.
 * action_id는 V2V에서 생성한 사고 공통 식별자를 변형하지 않고 넣음.
 * 영상 본문은 IPC에 넣지 않아 2GB RAM 환경에서도 payload 크기를 고정함.
 */
typedef struct {
    uint32_t version;
    uint32_t reserved;
    int64_t event_time_ns;
    int64_t clip_start_time_ns;
    int64_t clip_end_time_ns;
    double chrony_offset_ms;
    char action_id[UPLOADER_ACTION_ID_SIZE];
    char accident_vehicle_id[UPLOADER_VEHICLE_ID_SIZE];
    char vehicle_id[UPLOADER_VEHICLE_ID_SIZE];
    char video_path[UPLOADER_PATH_SIZE];
    char time_sync_status[UPLOADER_TIME_SYNC_STATUS_SIZE];
} uploader_evidence_ready_t;

typedef struct {
    uint32_t version;
    int32_t code;
    char message[UPLOADER_ERROR_SIZE];
} uploader_response_t;

typedef struct {
    uint32_t version;
    uint32_t requester_pid;
    uint32_t lease_ms;
    uint32_t reserved;
    char reason[UPLOADER_PAUSE_REASON_SIZE];
} uploader_control_request_t;

typedef struct {
    uint32_t version;
    int32_t code;
    uint32_t paused;
    uint32_t remaining_ms;
    uint64_t generation;
    char reason[UPLOADER_PAUSE_REASON_SIZE];
} uploader_control_state_t;

_Static_assert(sizeof(uploader_evidence_ready_t) == 768U,
               "uploader_evidence_ready_t wire size changed");
_Static_assert(sizeof(uploader_response_t) == 264U,
               "uploader_response_t wire size changed");
_Static_assert(sizeof(uploader_control_request_t) == 112U,
               "uploader_control_request_t wire size changed");
_Static_assert(sizeof(uploader_control_state_t) == 120U,
               "uploader_control_state_t wire size changed");

#endif
