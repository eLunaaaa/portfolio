#ifndef UPLOADER_CONFIG_H
#define UPLOADER_CONFIG_H

/**
 * @file uploader_config.h
 * @brief Worker가 사용하는 Pi 신원과 API 인증 설정.
 * @scope 단일 uploader의 Worker 스레드 전용.
 *
 * API Key는 Queue DB나 metadata.json에 저장하지 않고 프로세스 환경에서만
 * 읽는다. 이 구조체를 로그에 통째로 출력하면 비밀값이 노출되므로 각 필드는
 * 필요한 위치에서 개별적으로 사용해야 한다.
 */

#include <stddef.h>

#define WORKER_NODE_ID_SIZE 65U
#define WORKER_VEHICLE_ID_SIZE 65U
#define WORKER_API_KEY_SIZE 513U

typedef struct {
    char node_id[WORKER_NODE_ID_SIZE];
    char vehicle_id[WORKER_VEHICLE_ID_SIZE];
    char api_key[WORKER_API_KEY_SIZE];
} worker_config_t;

/**
 * UPLOADER_NODE_ID, UPLOADER_VEHICLE_ID, UPLOADER_API_KEY를 읽고 안전한 고정 크기 구조체로
 * 복사한다.
 *
 * 반환값:
 *  0: 세 환경변수가 모두 존재하고 길이와 문자 검증을 통과함.
 * -1: 필수 환경변수가 없거나 HTTP Header에 넣을 수 없는 문자가 포함됨.
 *
 * 실패 시 Worker는 아직 Queue 작업을 claim하지 않은 상태이므로 Queue 상태에는
 * 영향을 주지 않는다. error에는 API Key 원문을 넣지 않는다.
 */
int worker_config_load(
    worker_config_t *config,
    char *error,
    size_t error_size);

/**
 * Worker 종료 전에 구조체의 인증정보를 덮어쓴다.
 *
 * 환경변수 자체까지 지우는 기능은 아니지만, Worker가 따로 복사해 둔 API Key가
 * 사용이 끝난 stack 영역에 그대로 남는 시간을 줄인다.
 */
void worker_config_clear(worker_config_t *config);

#endif
