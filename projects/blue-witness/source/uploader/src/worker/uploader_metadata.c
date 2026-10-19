#define _POSIX_C_SOURCE 200809L

#include "uploader_metadata.h"

#include <ctype.h>
#include <inttypes.h>
#include <errno.h>
#include <stdio.h>
#include <string.h>
#include <sys/stat.h>

/* 기능: JSON에서 허용하는 연속 공백을 건너뛴다.
 * 입력: NUL로 끝나는 문자열의 현재 위치 p.
 * 출력·구조: 공백 다음 첫 문자 포인터. 버퍼를 수정하지 않아 구문 검사 함수들이 같은 입력을 순회할 수 있다.
 */
static const char *json_space(const char *p)
{
    while (*p == ' ' || *p == '\t' || *p == '\r' || *p == '\n') p++;
    return p;
}

/* 기능: JSON 문자열의 종료 위치와 escape 형식을 검사한다.
 * 입력: 시작 따옴표를 가리키는 p.
 * 출력·구조: 닫는 따옴표 다음 포인터 또는 잘못된 형식이면 NULL. 제어 문자·escape·4자리 유니코드 escape를 검사한다.
 */
static const char *json_string_end(const char *p)
{
    unsigned int index;
    if (*p++ != '"') return NULL;
    while (*p != '\0' && *p != '"') {
        if ((unsigned char)*p < 0x20U) return NULL;
        if (*p++ == '\\') {
            if (*p == 'u') {
                p++;
                for (index = 0; index < 4U; index++) {
                    if (!isxdigit((unsigned char)*p)) return NULL;
                    p++;
                }
            } else {
                if (*p == '\0' || strchr("\"\\/bfnrt", *p) == NULL) return NULL;
                p++;
            }
        }
    }
    return *p == '"' ? p + 1 : NULL;
}

/* 기능: JSON 값 하나를 재귀적으로 검사하고 끝을 찾는다.
 * 입력: 값 시작 p와 현재 중첩 깊이 depth.
 * 출력·구조: 값 다음 포인터 또는 구문 오류·32단계 초과 시 NULL. 객체·배열·문자열·상수·숫자를 구분해 처리한다.
 */
static const char *json_value_end(const char *p, unsigned int depth)
{
    char closing;
    int object;
    if (depth > 32U) return NULL;
    p = json_space(p);
    if (*p == '"') return json_string_end(p);
    if (*p == '{' || *p == '[') {
        object = *p == '{';
        closing = object ? '}' : ']';
        p = json_space(p + 1);
        if (*p == closing) return p + 1;
        for (;;) {
            if (object) {
                p = json_string_end(p);
                if (p == NULL || *(p = json_space(p)) != ':') return NULL;
                p++;
            }
            p = json_value_end(p, depth + 1U);
            if (p == NULL) return NULL;
            p = json_space(p);
            if (*p == closing) return p + 1;
            if (*p != ',') return NULL;
            p = json_space(p + 1);
        }
    }
    if (strncmp(p, "true", 4U) == 0) return p + 4;
    if (strncmp(p, "false", 5U) == 0) return p + 5;
    if (strncmp(p, "null", 4U) == 0) return p + 4;
    if (*p == '-') p++;
    if (*p == '0') p++;
    else {
        if (*p < '1' || *p > '9') return NULL;
        do { p++; } while (isdigit((unsigned char)*p));
    }
    if (*p == '.') {
        p++;
        if (!isdigit((unsigned char)*p)) return NULL;
        do { p++; } while (isdigit((unsigned char)*p));
    }
    if (*p == 'e' || *p == 'E') {
        p++;
        if (*p == '+' || *p == '-') p++;
        if (!isdigit((unsigned char)*p)) return NULL;
        do { p++; } while (isdigit((unsigned char)*p));
    }
    return p;
}

/* 기능: 메타데이터가 완결된 JSON 객체인지 검사한다.
 * 입력: JSON 문자열 json(NULL 가능).
 * 출력·구조: 유효하면 1, 아니면 0. 최상위 객체와 값 뒤의 남은 문자를 검사해 잘린 데이터·덧붙은 데이터를 거른다.
 */
int metadata_json_is_valid(const char *json)
{
    const char *end;
    if (json == NULL || *json_space(json) != '{') return 0;
    end = json_value_end(json, 0U);
    return end != NULL && *json_space(end) == '\0';
}

/* 기능: 메타데이터를 검증하고 이전 버전 action_id 필드를 제거한다.
 * 입력: 수정 가능한 json, 길이·제거 여부 출력 포인터, 오류 버퍼·크기.
 * 출력·구조: 성공 0과 수정된 길이·removed, 실패 -1. 최상위 action_id만 제거하며 중복 필드·잘못된 값 형식은 거부한다.
 */
int metadata_prepare_http(char *json, size_t *size, int *removed,
                          char *error, size_t error_size)
{
    const char *p, *key, *key_end, *value, *end, *comma = NULL;
    const char *remove_begin = NULL, *remove_end = NULL;
    *removed = 0;
    if (!metadata_json_is_valid(json)) {
        snprintf(error, error_size, "metadata JSON is incomplete or invalid");
        return -1;
    }
    p = json_space(json_space(json) + 1);
    while (*p != '}') {
        key = p;
        key_end = json_string_end(p);
        value = json_space(json_space(key_end) + 1);
        end = json_space(json_value_end(value, 1U));
        if ((size_t)(key_end - key) == 11U &&
            memcmp(key, "\"action_id\"", 11U) == 0) {
            if (remove_begin != NULL || *value != '"') {
                snprintf(error, error_size, "legacy action_id is invalid or duplicated");
                return -1;
            }
            remove_begin = (*end == ',' || comma == NULL) ? key : comma;
            remove_end = *end == ',' ? end + 1 : end;
        }
        if (*end == '}') break;
        comma = end;
        p = json_space(end + 1);
    }
    if (remove_begin != NULL) {
        memmove(json + (remove_begin - json), remove_end, strlen(remove_end) + 1U);
        *removed = 1;
    }
    *size = strlen(json);
    return 0;
}

/* =========================================================================
 * #### Metadata 식별자 문자 정책
 * ========================================================================= */

/**
 * @brief ID 한 글자가 서버 계약의 허용 문자에 속하는지 검사함.
 * @return 영문/숫자/밑줄/하이픈이면 1, 아니면 0.
 */
static int valid_identifier_character(unsigned char character)
{
    return (character >= 'A' && character <= 'Z') ||
        (character >= 'a' && character <= 'z') ||
        (character >= '0' && character <= '9') ||
        character == '_' ||
        character == '-';
}

/**
 * JSON 문자열 안에서 사용할 수 없는 제어문자를 거부한다.
 *
 * vehicle_id는 서버가 발급한 내부 식별자와 그대로 비교해야 하므로 event_id처럼
 * 문자 종류를 임의로 좁히지 않는다. 다만 JSON escape와 제어문자는 이 소형
 * parser가 모호하게 해석하지 않도록 거부한다.
 */
static int valid_identity_character(unsigned char character)
{
    return character >= 0x20U &&
        character != '"' &&
        character != '\\';
}

typedef int (*character_validator_t)(unsigned char character);

/**
 * 최상위 metadata에서 이름이 정확히 일치하는 JSON 문자열 필드를 찾는다.
 *
 * 이 프로젝트는 64 KiB로 제한된 고정 schema metadata를 사용한다. 전체 범용 JSON
 * parser를 프로세스마다 링크해 RAM을 늘리지 않으면서도, 기존 strstr 방식과 달리
 * 키 이름 일부 일치와 중복 키를 거부한다. 식별자에는 JSON escape가 필요하지
 * 않으므로 backslash가 포함된 값은 형식 오류로 처리한다.
 */
/* =========================================================================
 * #### 제한된 최상위 JSON 문자열 추출
 * ========================================================================= */

/**
 * @brief metadata 최상위에서 필수 문자열 key를 정확히 한 번 찾아 복사함.
 * @param validator 각 추출 문자의 허용 여부를 판정하는 함수.
 * @return 누락·중복·escape·길이·문자 오류가 없으면 0, 아니면 -1.
 * @note 범용 JSON parser가 아니라 현재 서버 계약에 맞춘 제한 parser임.
 */
static int extract_required_string(
    const char *metadata,
    const char *field_name,
    char *output,
    size_t output_capacity,
    character_validator_t validator,
    char *error,
    size_t error_size)
{
    const char *cursor = metadata;
    size_t field_length = strlen(field_name);
    unsigned int object_depth = 0U;
    int found = 0;

    if (metadata == NULL ||
        field_name == NULL ||
        output == NULL ||
        output_capacity < 2U) {
        snprintf(error, error_size, "invalid metadata field argument");
        return -1;
    }

    while (isspace((unsigned char)*cursor)) {
        cursor++;
    }

    if (*cursor != '{') {
        snprintf(error, error_size, "metadata must be a JSON object");
        return -1;
    }

    while (*cursor != '\0') {
        const char *token_start;
        const char *after_token;
        size_t token_length;

        if (*cursor == '{') {
            object_depth++;
            cursor++;
            continue;
        }

        if (*cursor == '}') {
            if (object_depth == 0U) {
                snprintf(error, error_size, "metadata object is unbalanced");
                return -1;
            }

            object_depth--;
            cursor++;
            continue;
        }

        if (*cursor != '"') {
            cursor++;
            continue;
        }

        cursor++;
        token_start = cursor;

        while (*cursor != '\0' && *cursor != '"') {
            if (*cursor == '\\') {
                cursor++;

                if (*cursor == '\0') {
                    snprintf(error, error_size, "invalid JSON escape");
                    return -1;
                }
            }

            cursor++;
        }

        if (*cursor != '"') {
            snprintf(error, error_size, "unterminated JSON string");
            return -1;
        }

        token_length = (size_t)(cursor - token_start);
        after_token = cursor + 1;

        while (isspace((unsigned char)*after_token)) {
            after_token++;
        }

        if (object_depth == 1U &&
            *after_token == ':' &&
            token_length == field_length &&
            memcmp(token_start, field_name, field_length) == 0) {
            const char *value;
            size_t output_length = 0U;

            if (found != 0) {
                snprintf(error, error_size, "%s is duplicated", field_name);
                return -1;
            }

            after_token++;

            while (isspace((unsigned char)*after_token)) {
                after_token++;
            }

            if (*after_token != '"') {
                snprintf(error, error_size, "%s must be a JSON string", field_name);
                return -1;
            }

            value = after_token + 1;

            while (*value != '\0' && *value != '"') {
                unsigned char character = (unsigned char)*value;

                if (!validator(character)) {
                    snprintf(error, error_size, "%s contains invalid character", field_name);
                    return -1;
                }

                if (output_length >= METADATA_ID_MAX_LENGTH ||
                    output_length + 1U >= output_capacity) {
                    snprintf(error, error_size, "%s is too long", field_name);
                    return -1;
                }

                output[output_length++] = *value;
                value++;
            }

            if (*value != '"' || output_length == 0U) {
                snprintf(error, error_size, "%s is invalid", field_name);
                return -1;
            }

            output[output_length] = '\0';
            found = 1;
            cursor = value + 1;
            continue;
        }

        cursor++;
    }

    if (found == 0) {
        snprintf(error, error_size, "%s is missing", field_name);
        return -1;
    }

    if (object_depth != 0U) {
        snprintf(error, error_size, "metadata object is unbalanced");
        return -1;
    }

    return 0;
}

/* =========================================================================
 * #### Metadata 파일 로드와 신원 일치 검증
 * ========================================================================= */

/**
 * @brief 일반 metadata 파일을 크기 제한 안에서 읽고 NUL 종료 문자열로 만듦.
 * @param metadata_size 실제 JSON byte 수를 돌려받음.
 * @return 정상 파일을 모두 읽으면 0, stat/open/read/크기 오류 시 -1.
 */
int metadata_load(
    const char *path,
    char *buffer,
    size_t buffer_capacity,
    size_t *metadata_size,
    char *error,
    size_t error_size)
{
    struct stat info;
    FILE *file;
    size_t expected_size;
    size_t read_size;

    if (path == NULL ||
        buffer == NULL ||
        buffer_capacity < 2U ||
        metadata_size == NULL) {
        snprintf(error, error_size, "invalid metadata argument");
        return -1;
    }

    if (stat(path, &info) != 0) {
        snprintf(
            error,
            error_size,
            "stat metadata: %s",
            strerror(errno));

        return -1;
    }

    if (!S_ISREG(info.st_mode)) {
        snprintf(error, error_size, "metadata is not a regular file");
        return -1;
    }

    if (info.st_size <= 0) {
        snprintf(error, error_size, "metadata is empty");
        return -1;
    }

    if ((uintmax_t)info.st_size >= (uintmax_t)buffer_capacity) {
        snprintf(
            error,
            error_size,
            "metadata exceeds limit: %jd bytes",
            (intmax_t)info.st_size);

        return -1;
    }

    expected_size = (size_t)info.st_size;

    file = fopen(path, "rb");

    if (file == NULL) {
        snprintf(
            error,
            error_size,
            "open metadata: %s",
            strerror(errno));

        return -1;
    }

    read_size = fread(
        buffer,
        1U,
        expected_size,
        file);

    if (read_size != expected_size) {
        snprintf(error, error_size, "metadata read incomplete");
        fclose(file);
        return -1;
    }

    if (fclose(file) != 0) {
        snprintf(
            error,
            error_size,
            "close metadata: %s",
            strerror(errno));

        return -1;
    }

    if (memchr(buffer, '\0', read_size) != NULL) {
        snprintf(error, error_size, "metadata contains NUL byte");
        return -1;
    }

    buffer[read_size] = '\0';
    *metadata_size = read_size;

    return 0;
}

/**
 * @brief metadata에서 필수 event_id를 추출함.
 * @return 유일하고 유효한 문자열이면 0, 아니면 -1.
 */
int metadata_get_event_id(
    const char *metadata,
    char *event_id,
    size_t event_id_capacity,
    char *error,
    size_t error_size)
{
    return extract_required_string(
        metadata,
        "event_id",
        event_id,
        event_id_capacity,
        valid_identifier_character,
        error,
        error_size);
}

/**
 * @brief metadata에서 필수 vehicle_id를 추출함.
 * @return 유일하고 유효한 문자열이면 0, 아니면 -1.
 */
int metadata_get_vehicle_id(
    const char *metadata,
    char *vehicle_id,
    size_t vehicle_id_capacity,
    char *error,
    size_t error_size)
{
    return extract_required_string(
        metadata,
        "vehicle_id",
        vehicle_id,
        vehicle_id_capacity,
        valid_identity_character,
        error,
        error_size);
}

/**
 * @brief metadata 차량과 이 Pi에 설정된 UPLOADER_VEHICLE_ID가 같은지 확인함.
 * @return 일치하면 0, 불일치 또는 누락이면 -1.
 * @note 불일치는 재시도해도 해결되지 않는 영구 데이터/설정 오류로 취급함.
 */
int metadata_validate_vehicle_id(
    const char *metadata_vehicle_id,
    const char *configured_vehicle_id,
    char *error,
    size_t error_size)
{
    if (metadata_vehicle_id == NULL ||
        configured_vehicle_id == NULL ||
        metadata_vehicle_id[0] == '\0' ||
        configured_vehicle_id[0] == '\0') {
        snprintf(error, error_size, "invalid vehicle identity argument");
        return -1;
    }

    if (strcmp(metadata_vehicle_id, configured_vehicle_id) != 0) {
        snprintf(
            error,
            error_size,
            "metadata vehicle_id does not match UPLOADER_VEHICLE_ID");
        return -1;
    }

    return 0;
}
