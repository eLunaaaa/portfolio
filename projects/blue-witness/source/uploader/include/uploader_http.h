#ifndef UPLOADER_HTTP_H
#define UPLOADER_HTTP_H

/**
 * @file uploader_http.h
 * @brief FastAPI health 확인 및 multipart 업로드 기능.
 * @scope 단일 uploader의 Worker 스레드 전용.
 *
 * Queue, Ingest, Camera, AI, P2P 코드는 이 헤더를 포함하면 안 된다. 네트워크
 * I/O를 Worker 스레드 안에만 두어 다른 역할이 영상을 buffer에 담거나 업로드
 * 재시도 책임을 갖지 않도록 한다.
 */

#include <stddef.h>

typedef enum {
    UPLOADER_PHASE_NOT_CONNECTED = 0,
    UPLOADER_PHASE_CONNECTED,
    UPLOADER_PHASE_BODY_SENDING,
    UPLOADER_PHASE_WAITING_ACK
} upload_phase_t;

/* Worker thread가 시작/재시도 시 호출하며 SSL_CTX를 Worker 종료까지 재사용한다. */
int http_client_prepare(const char *server_url, const char *ca_file,
                        char *error, size_t error_size);
void http_client_cleanup(void);

int http_health_check(
    const char *server_url,
    int *http_status,
    char *error,
    size_t error_size);

/**
 * 서버가 해당 event와 현재 Node 차량의 영상을 이미 안전하게 저장했는지 조회한다.
 * 성공적으로 HTTP 응답을 받으면 상태코드와 JSON body를 반환하며 API Key는
 * 호출자 메모리에서만 사용하고 로그나 Queue DB에는 저장하지 않는다.
 */
int http_query_evidence_status(
    const char *server_url,
    const char *event_id,
    const char *node_id,
    const char *api_key,
    const char *content_sha256,
    int *http_status,
    char *response_body,
    size_t response_body_size,
    char *error,
    size_t error_size);

int http_upload_evidence(
    const char *server_url,
    const char *event_id,
    const char *metadata,
    size_t metadata_size,
    const char *video_path,
    const char *node_id,
    const char *api_key,
    const char *content_sha256,
    upload_phase_t *phase,
    int *http_status,
    char *ack_body,
    size_t ack_body_size,
    char *error,
    size_t error_size);

#endif
