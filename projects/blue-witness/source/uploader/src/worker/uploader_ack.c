#define _POSIX_C_SOURCE 200809L

#include "uploader_ack.h"
#include "uploader_metadata.h"

#include <ctype.h>
#include <stdio.h>
#include <string.h>

/* =========================================================================
 * #### 제한된 최상위 JSON 필드 탐색
 * ========================================================================= */

/**
 * 최상위 JSON object에서 지정한 필드의 값 시작 주소를 찾음.
 * 키 누락과 중복을 모두 오류로 처리하여 서로 다른 parser 해석을 악용하는 일을
 * 막음. heap을 쓰지 않고 서버 ACK에 필요한 평면 필드만 해석함.
 */
static int find_top_level_value(
    const char *json,
    const char *field_name,
    const char **value,
    char *error,
    size_t error_size)
{
    const char *cursor = json;
    size_t field_length = strlen(field_name);
    unsigned int depth = 0U;
    int found = 0;

    if (json == NULL || field_name == NULL || value == NULL ||
        !metadata_json_is_valid(json)) {
        snprintf(error, error_size, "invalid ACK field argument");
        return -1;
    }

    while (isspace((unsigned char)*cursor)) {
        cursor++;
    }

    if (*cursor != '{') {
        snprintf(error, error_size, "server ACK must be a JSON object");
        return -1;
    }

    while (*cursor != '\0') {
        const char *token;
        const char *after;
        size_t token_length;

        if (*cursor == '{') {
            depth++;
            cursor++;
            continue;
        }

        if (*cursor == '}') {
            if (depth == 0U) {
                snprintf(error, error_size, "server ACK object is unbalanced");
                return -1;
            }
            depth--;
            cursor++;
            continue;
        }

        if (*cursor != '"') {
            cursor++;
            continue;
        }

        token = ++cursor;

        while (*cursor != '\0' && *cursor != '"') {
            if (*cursor == '\\') {
                cursor++;
                if (*cursor == '\0') {
                    snprintf(error, error_size, "invalid ACK JSON escape");
                    return -1;
                }
            }
            cursor++;
        }

        if (*cursor != '"') {
            snprintf(error, error_size, "unterminated ACK JSON string");
            return -1;
        }

        token_length = (size_t)(cursor - token);
        after = cursor + 1;

        while (isspace((unsigned char)*after)) {
            after++;
        }

        if (depth == 1U && *after == ':' &&
            token_length == field_length &&
            memcmp(token, field_name, field_length) == 0) {
            if (found != 0) {
                snprintf(error, error_size, "server ACK field %s is duplicated", field_name);
                return -1;
            }

            after++;
            while (isspace((unsigned char)*after)) {
                after++;
            }

            *value = after;
            found = 1;
        }

        cursor++;
    }

    if (depth != 0U) {
        snprintf(error, error_size, "server ACK object is unbalanced");
        return -1;
    }

    if (found == 0) {
        snprintf(error, error_size, "server ACK field %s is missing", field_name);
        return 1;
    }

    return 0;
}

/** 새 storage_ack와 기존 saved 응답을 한동안 함께 받을 수 있도록 선택 필드를 읽음. */
/**
 * @brief 선택 boolean 필드를 읽고 없을 때 기본 false로 처리함.
 * @return 유효한 boolean 또는 필드 없음이면 0, 형식/중복 오류면 -1.
 */
static int read_optional_boolean_field(
    const char *json,
    const char *field_name,
    int *output,
    int *present,
    char *error,
    size_t error_size)
{
    const char *value;
    const char *after;
    int result = find_top_level_value(
        json, field_name, &value, error, error_size);

    *present = 0;
    *output = 0;

    if (result == 1) {
        return 0;
    }
    if (result != 0) {
        return -1;
    }

    if (strncmp(value, "true", 4U) == 0) {
        *output = 1;
        after = value + 4U;
    } else if (strncmp(value, "false", 5U) != 0) {
        snprintf(error, error_size, "server ACK field %s must be boolean", field_name);
        return -1;
    } else {
        after = value + 5U;
    }

    while (isspace((unsigned char)*after)) {
        after++;
    }
    if (*after != ',' && *after != '}') {
        snprintf(error, error_size, "server ACK field %s has invalid delimiter", field_name);
        return -1;
    }

    *present = 1;
    return 0;
}

/** JSON string 필드를 고정 buffer로 복사하며 escape와 제어문자를 거부함. */
/**
 * @brief 필수 JSON string을 고정 buffer로 읽고 escape와 경계를 검사함.
 * @return 유일한 유효 string이면 0, 누락/길이/형식 오류 시 -1.
 */
static int read_string_field(
    const char *json,
    const char *field_name,
    char *output,
    size_t output_size,
    char *error,
    size_t error_size)
{
    const char *value;
    size_t length = 0U;

    if (find_top_level_value(
            json, field_name, &value, error, error_size) != 0) {
        return -1;
    }

    if (*value != '"') {
        snprintf(error, error_size, "server ACK field %s must be a string", field_name);
        return -1;
    }

    value++;
    while (*value != '\0' && *value != '"') {
        unsigned char character = (unsigned char)*value;

        if (character < 0x20U || character > 0x7EU || character == '\\') {
            snprintf(error, error_size, "server ACK field %s contains invalid character", field_name);
            return -1;
        }

        if (length + 1U >= output_size) {
            snprintf(error, error_size, "server ACK field %s is too long", field_name);
            return -1;
        }

        output[length++] = *value++;
    }

    if (*value != '"' || length == 0U) {
        snprintf(error, error_size, "server ACK field %s is invalid", field_name);
        return -1;
    }

    output[length] = '\0';
    value++;

    while (isspace((unsigned char)*value)) {
        value++;
    }

    if (*value != ',' && *value != '}') {
        snprintf(error, error_size, "server ACK field %s has invalid delimiter", field_name);
        return -1;
    }

    return 0;
}

/** JSON boolean은 문자열 "true"가 아니라 실제 true/false token만 허용함. */
/**
 * @brief 필수 JSON boolean을 읽음.
 * @return true/false를 정상 판독하면 0, 누락 또는 자료형 오류 시 -1.
 */
static int read_boolean_field(
    const char *json,
    const char *field_name,
    int *output,
    char *error,
    size_t error_size)
{
    const char *value;

    if (find_top_level_value(
            json, field_name, &value, error, error_size) != 0) {
        return -1;
    }

    if (strncmp(value, "true", 4U) == 0) {
        const char *after = value + 4U;

        while (isspace((unsigned char)*after)) {
            after++;
        }

        if (*after != ',' && *after != '}') {
            snprintf(error, error_size, "server ACK field %s has invalid delimiter", field_name);
            return -1;
        }

        *output = 1;
        return 0;
    }

    if (strncmp(value, "false", 5U) == 0) {
        const char *after = value + 5U;

        while (isspace((unsigned char)*after)) {
            after++;
        }

        if (*after != ',' && *after != '}') {
            snprintf(error, error_size, "server ACK field %s has invalid delimiter", field_name);
            return -1;
        }

        *output = 0;
        return 0;
    }

    snprintf(error, error_size, "server ACK field %s must be boolean", field_name);
    return -1;
}

/* =========================================================================
 * #### FastAPI 응답 계약 검증
 * ========================================================================= */

/**
 * @brief health JSON의 status와 database가 모두 "ok"인지 확인함.
 * @return 서버가 실제 준비 상태이면 0, 필드/값 오류 시 -1.
 */
int server_health_ack_validate(
    const char *json,
    char *error,
    size_t error_size)
{
    char status[16];
    char database[16];

    if (read_string_field(json, "status", status, sizeof(status), error, error_size) != 0 ||
        read_string_field(json, "database", database, sizeof(database), error, error_size) != 0) {
        return -1;
    }

    if (strcmp(status, "ok") != 0 || strcmp(database, "ok") != 0) {
        snprintf(error, error_size, "health ACK requires status=ok and database=ok");
        return -1;
    }

    return 0;
}

/**
 * @brief 업로드 ACK의 저장 여부, ID, 상태, SHA가 로컬 기대값과 같은지 검사함.
 * @param ack 성공 시 검증을 통과한 서버 응답 필드를 돌려받음.
 * @return 모든 DONE 조건이 충족되면 0, 하나라도 불확실하면 -1.
 * @note HTTP 200/201만으로는 이 함수가 성공하지 않음.
 */
int server_upload_ack_validate(
    const char *json,
    const char *expected_event_id,
    const char *expected_vehicle_id,
    const char *expected_sha256,
    server_upload_ack_t *ack,
    char *error,
    size_t error_size)
{
    int saved_present;
    int storage_ack_present;

    if (json == NULL || expected_event_id == NULL ||
        expected_vehicle_id == NULL || expected_sha256 == NULL || ack == NULL) {
        snprintf(error, error_size, "invalid upload ACK argument");
        return -1;
    }

    memset(ack, 0, sizeof(*ack));

    if (read_optional_boolean_field(json, "saved", &ack->saved, &saved_present, error, error_size) != 0 ||
        read_optional_boolean_field(json, "storage_ack", &ack->storage_ack, &storage_ack_present, error, error_size) != 0 ||
        read_boolean_field(json, "idempotent_replay", &ack->idempotent_replay, error, error_size) != 0 ||
        read_string_field(json, "event_id", ack->event_id, sizeof(ack->event_id), error, error_size) != 0 ||
        read_string_field(json, "vehicle_id", ack->vehicle_id, sizeof(ack->vehicle_id), error, error_size) != 0 ||
        read_string_field(json, "upload_status", ack->upload_status, sizeof(ack->upload_status), error, error_size) != 0 ||
        read_string_field(json, "sha256", ack->sha256, sizeof(ack->sha256), error, error_size) != 0) {
        return -1;
    }

    if ((storage_ack_present != 0 && ack->storage_ack == 0) ||
        (storage_ack_present == 0 &&
         (saved_present == 0 || ack->saved == 0)) ||
        strcmp(ack->upload_status, "STORED") != 0) {
        snprintf(error, error_size, "server did not confirm stored evidence");
        return -1;
    }

    if (strcmp(ack->event_id, expected_event_id) != 0) {
        snprintf(error, error_size, "server ACK event_id mismatch");
        return -1;
    }

    if (strcmp(ack->vehicle_id, expected_vehicle_id) != 0) {
        snprintf(error, error_size, "server ACK vehicle_id mismatch");
        return -1;
    }

    if (strcmp(ack->sha256, expected_sha256) != 0) {
        snprintf(error, error_size, "server ACK sha256 mismatch");
        return -1;
    }

    return 0;
}

/**
 * @brief 상태 조회 200 body가 현재 로컬 evidence와 정확히 일치하는지 검사함.
 * @return 서버 저장과 식별자/SHA가 모두 확인되면 0, 아니면 -1.
 */
int server_evidence_status_validate(
    const char *json,
    const char *expected_event_id,
    const char *expected_vehicle_id,
    const char *expected_sha256,
    server_evidence_status_t *status,
    char *error,
    size_t error_size)
{
    if (json == NULL || expected_event_id == NULL ||
        expected_vehicle_id == NULL || expected_sha256 == NULL ||
        status == NULL) {
        snprintf(error, error_size, "invalid evidence status argument");
        return -1;
    }

    memset(status, 0, sizeof(*status));

    if (read_boolean_field(json, "stored", &status->stored, error, error_size) != 0 ||
        read_boolean_field(json, "storage_ack", &status->storage_ack, error, error_size) != 0 ||
        read_string_field(json, "event_id", status->event_id, sizeof(status->event_id), error, error_size) != 0 ||
        read_string_field(json, "vehicle_id", status->vehicle_id, sizeof(status->vehicle_id), error, error_size) != 0 ||
        read_string_field(json, "upload_status", status->upload_status, sizeof(status->upload_status), error, error_size) != 0 ||
        read_string_field(json, "sha256", status->sha256, sizeof(status->sha256), error, error_size) != 0) {
        return -1;
    }

    if (status->stored == 0 || status->storage_ack == 0 ||
        strcmp(status->upload_status, "STORED") != 0) {
        snprintf(error, error_size, "server did not confirm stored evidence");
        return -1;
    }

    if (strcmp(status->event_id, expected_event_id) != 0) {
        snprintf(error, error_size, "evidence status event_id mismatch");
        return -1;
    }

    if (strcmp(status->vehicle_id, expected_vehicle_id) != 0) {
        snprintf(error, error_size, "evidence status vehicle_id mismatch");
        return -1;
    }

    if (strlen(status->sha256) != SHA256_HEX_LENGTH ||
        strcmp(status->sha256, expected_sha256) != 0) {
        snprintf(error, error_size, "evidence status sha256 mismatch");
        return -1;
    }

    return 0;
}

/* =========================================================================
 * #### ACK 유실 복구 정책
 * ========================================================================= */

/**
 * @brief 상태 조회 HTTP 결과만으로 다음 복구 행동을 결정하는 순수 함수임.
 * @return MARK_DONE, UPLOAD_REQUIRED, RETRY_LATER 또는 PERMANENT_FAILURE.
 * @note DB, 파일, 네트워크를 직접 변경하지 않음.
 */
recovery_action_t decide_recovery_action(
    int http_status,
    int response_valid)
{
    if (http_status == 200) {
        return response_valid != 0
            ? RECOVERY_MARK_DONE
            : RECOVERY_RETRY_LATER;
    }

    if (http_status == 404) {
        return RECOVERY_UPLOAD_REQUIRED;
    }

    if (http_status == 0 || http_status == 408 || http_status == 409 ||
        http_status == 422 ||
        http_status == 425 || http_status == 429 ||
        (http_status >= 500 && http_status <= 599)) {
        return RECOVERY_RETRY_LATER;
    }

    return RECOVERY_PERMANENT_FAILURE;
}
