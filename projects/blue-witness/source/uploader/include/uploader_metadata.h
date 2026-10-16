#ifndef UPLOADER_METADATA_H
#define UPLOADER_METADATA_H

/**
 * @file uploader_metadata.h
 * @brief 크기를 제한한 metadata.json 읽기와 업로드 신원 필드 추출 기능.
 * @scope 단일 uploader의 Ingest/Worker 스레드에서 사용.
 *
 * 이 헤더는 JSON 파일을 읽기 위한 것이며 시스템 전체 사고 action schema가
 * 아니다. AI와 Camera는 action 생성 시 합의된 IPC/schema 규약을 사용해야 하며
 * 이 parser 구현에 의존하면 안 된다.
 */

#include <stddef.h>

#define METADATA_ID_MAX_LENGTH 64U

int metadata_json_is_valid(const char *json);
/* 전송 buffer에서만 레거시 action_id를 제거함. spool 원본은 변경하지 않음. */
int metadata_prepare_http(char *json, size_t *size, int *removed,
                          char *error, size_t error_size);

int metadata_load(
    const char *path,
    char *buffer,
    size_t buffer_capacity,
    size_t *metadata_size,
    char *error,
    size_t error_size);

int metadata_get_event_id(
    const char *metadata,
    char *event_id,
    size_t event_id_capacity,
    char *error,
    size_t error_size);

/**
 * metadata.json의 vehicle_id 문자열을 추출한다.
 *
 * 반환값:
 *  0: vehicle_id가 한 번만 존재하고 1~64 byte 범위임.
 * -1: 누락, 중복, 형식 오류 또는 출력 buffer 초과임.
 */
int metadata_get_vehicle_id(
    const char *metadata,
    char *vehicle_id,
    size_t vehicle_id_capacity,
    char *error,
    size_t error_size);

/**
 * metadata의 vehicle_id와 이 Pi에 설정된 UPLOADER_VEHICLE_ID를 비교한다.
 *
 * 불일치는 서버에서 403이 발생할 영구적인 데이터/설정 오류다. 이 함수는 HTTP
 * 요청 전에 불일치를 차단하며 Queue 상태 변경은 호출자인 Worker가 담당한다.
 */
int metadata_validate_vehicle_id(
    const char *metadata_vehicle_id,
    const char *configured_vehicle_id,
    char *error,
    size_t error_size);

#endif
