#define _POSIX_C_SOURCE 200809L

#include "uploader_config.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

/* =========================================================================
 * #### HTTP Header에 사용할 실행 장치 신원 검증
 * ========================================================================= */

/**
 * 인증 Header에 들어갈 환경변수가 한 줄짜리 ASCII 값인지 확인한다.
 *
 * 개행이나 제어문자를 허용하면 공격자가 추가 HTTP Header를 삽입할 수 있으므로
 * 공백과 제어문자를 거부한다. API Key 자체는 다양한 기호를 사용할 수 있어
 * 출력 가능한 ASCII 범위 안에서는 별도의 문자 종류 제한을 두지 않는다.
 */
static int is_header_safe_value(const char *value)
{
    const unsigned char *cursor = (const unsigned char *)value;

    while (*cursor != '\0') {
        if (*cursor < 0x21U || *cursor > 0x7EU) {
            return 0;
        }

        cursor++;
    }

    return 1;
}

/**
 * 필수 환경변수 하나를 읽어 목적지 buffer에 복사한다.
 *
 * name은 오류 메시지에 사용할 환경변수 이름이며 secret이 0이 아니면 실제 값을
 * 오류 메시지에 절대 포함하지 않는다. 성공 시 destination은 항상 NUL 문자로
 * 끝난다.
 */
static int copy_required_environment(
    const char *name,
    char *destination,
    size_t destination_size,
    int secret,
    char *error,
    size_t error_size)
{
    const char *value;
    size_t length;

    value = getenv(name);

    if (value == NULL || value[0] == '\0') {
        snprintf(error, error_size, "%s is not set", name);
        return -1;
    }

    length = strlen(value);

    if (length >= destination_size) {
        snprintf(
            error,
            error_size,
            "%s exceeds maximum length",
            name);
        return -1;
    }

    if (!is_header_safe_value(value)) {
        snprintf(
            error,
            error_size,
            "%s contains an unsafe character",
            name);
        return -1;
    }

    memcpy(destination, value, length + 1U);

    /* secret은 값이 메시지나 디버그 출력에 포함되지 않았음을 명시하기 위한
     * 인자다. 현재 모든 오류는 이름만 기록하지만, 향후 분기 추가 시 컴파일러가
     * 이 보안 의도를 보존하도록 함수 계약에 남겨 둔다. */
    (void)secret;

    return 0;
}

/* =========================================================================
 * #### Worker 인증 설정 수명주기
 * ========================================================================= */

/**
 * @brief Node ID, Vehicle ID, API Key를 환경에서 읽어 Worker 설정을 완성함.
 * @param config 성공 시 검증된 세 값을 보관할 고정 크기 구조체.
 * @param error 실패 이유를 기록할 buffer. API Key 원문은 기록하지 않음.
 * @return 세 값이 모두 유효하면 0, 하나라도 누락되거나 안전하지 않으면 -1.
 */
int worker_config_load(
    worker_config_t *config,
    char *error,
    size_t error_size)
{
    if (config == NULL || error == NULL || error_size == 0U) {
        return -1;
    }

    memset(config, 0, sizeof(*config));
    error[0] = '\0';

    if (copy_required_environment(
            "UPLOADER_NODE_ID",
            config->node_id,
            sizeof(config->node_id),
            0,
            error,
            error_size) != 0 ||
        copy_required_environment(
            "UPLOADER_VEHICLE_ID",
            config->vehicle_id,
            sizeof(config->vehicle_id),
            0,
            error,
            error_size) != 0 ||
        copy_required_environment(
            "UPLOADER_API_KEY",
            config->api_key,
            sizeof(config->api_key),
            1,
            error,
            error_size) != 0) {
        worker_config_clear(config);
        return -1;
    }

    return 0;
}

/**
 * @brief Worker가 복사해 둔 인증정보를 volatile write로 덮어씀.
 * @param config 사용이 끝난 인증 설정 구조체.
 * @return 반환값 없음.
 * @note 프로세스 환경변수 원본까지 삭제하는 함수는 아님.
 */
void worker_config_clear(worker_config_t *config)
{
    volatile unsigned char *cursor;
    size_t index;

    if (config == NULL) {
        return;
    }

    /* 단순 memset은 최적화 과정에서 제거될 수 있으므로 volatile 포인터로
     * 한 바이트씩 덮어쓴다. */
    cursor = (volatile unsigned char *)config;

    for (index = 0U; index < sizeof(*config); index++) {
        cursor[index] = 0U;
    }
}
