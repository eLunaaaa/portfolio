#ifndef UPLOADER_ACK_H
#define UPLOADER_ACK_H

/**
 * @file uploader_ack.h
 * @brief FastAPI health 및 영상 저장 ACK를 엄격하게 검증하는 Worker 전용 기능.
 * @scope 단일 uploader의 Worker 스레드 전용.
 *
 * HTTP 200/201만으로 DONE을 결정하지 않음. 서버가 반환한 식별자와 SHA-256이
 * Pi가 전송한 값과 모두 같을 때만 호출자가 Queue에 SUCCESS를 보고할 수 있음.
 */

#include <stddef.h>

#include "uploader_sha256.h"
#include "uploader_settings.h"

#define SERVER_UPLOAD_STATUS_SIZE 32U

typedef struct {
    int saved;
    int storage_ack;
    int idempotent_replay;
    char event_id[UPLOADER_EVENT_ID_SIZE];
    char vehicle_id[UPLOADER_VEHICLE_ID_SIZE];
    char upload_status[SERVER_UPLOAD_STATUS_SIZE];
    char sha256[SHA256_HEX_SIZE];
} server_upload_ack_t;

typedef struct {
    int stored;
    int storage_ack;
    char event_id[UPLOADER_EVENT_ID_SIZE];
    char vehicle_id[UPLOADER_VEHICLE_ID_SIZE];
    char upload_status[SERVER_UPLOAD_STATUS_SIZE];
    char sha256[SHA256_HEX_SIZE];
} server_evidence_status_t;

typedef enum {
    RECOVERY_MARK_DONE = 1,
    RECOVERY_UPLOAD_REQUIRED = 2,
    RECOVERY_RETRY_LATER = 3,
    RECOVERY_PERMANENT_FAILURE = 4
} recovery_action_t;

/** health 응답의 status와 database가 모두 "ok"인지 검사함. */
int server_health_ack_validate(
    const char *json,
    char *error,
    size_t error_size);

/**
 * 업로드 ACK의 필수 필드 존재·자료형·값을 검사함.
 * 성공하면 ACK 구조체에 검증된 값만 남기며, 실패하면 DONE 처리하면 안 됨.
 */
int server_upload_ack_validate(
    const char *json,
    const char *expected_event_id,
    const char *expected_vehicle_id,
    const char *expected_sha256,
    server_upload_ack_t *ack,
    char *error,
    size_t error_size);

/**
 * 상태 조회 HTTP 200 body를 검증한다. 저장 여부, 식별자, 차량, SHA-256이 모두
 * 로컬 기대값과 같을 때만 0을 반환하므로 호출자는 그때만 DONE을 보고한다.
 */
int server_evidence_status_validate(
    const char *json,
    const char *expected_event_id,
    const char *expected_vehicle_id,
    const char *expected_sha256,
    server_evidence_status_t *status,
    char *error,
    size_t error_size);

/** HTTP 상태와 ACK 검증 결과만으로 복구 동작을 결정하는 순수 함수임. */
recovery_action_t decide_recovery_action(
    int http_status,
    int response_valid);

#endif
