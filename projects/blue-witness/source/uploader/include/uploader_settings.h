#ifndef UPLOADER_SETTINGS_H
#define UPLOADER_SETTINGS_H

/**
 * @file uploader_settings.h
 * @brief 업로더 내부 경로, 제한값, timeout 및 재시도 정책.
 * @scope 단일 uploader 프로세스 내부에서만 사용.
 */

#include "uploader_ipc_protocol.h"

/* 사용자 이름을 바이너리에 넣지 않는다. 환경변수가 없을 때만 실행 계정의
 * HOME 아래에 이 상대 경로를 붙여 기본 저장 위치를 만든다. */
#define UPLOADER_DEFAULT_DB_RELATIVE_PATH \
    "uploader/data/queue.db"

#define UPLOADER_DEFAULT_SPOOL_RELATIVE_PATH \
    "uploader/data/jobs"

#define UPLOADER_HEALTH_PATH \
    "/api/v1/health"

#define UPLOADER_JOB_ID_SIZE 96U
#define UPLOADER_EVENT_ID_SIZE 65U
#define UPLOADER_METADATA_MAX_SIZE (64U * 1024U)
#define UPLOADER_HTTP_RESPONSE_MAX_SIZE (64U * 1024U)
#define UPLOADER_HTTP_CHUNK_SIZE (64U * 1024U)
#define UPLOADER_SPOOL_COPY_CHUNK_SIZE (64U * 1024U)

#define UPLOADER_CONNECT_TIMEOUT_MS 3000
#define UPLOADER_HEALTH_TIMEOUT_MS 5000
#define UPLOADER_TRANSFER_TIMEOUT_MS 300000
#define UPLOADER_TLS_STALL_TIMEOUT_MS 30000
#define UPLOADER_DEFAULT_CA_FILE "/etc/ace/certs/ace-ca.crt"

#define UPLOADER_NO_JOB_RETRY_MS 3000U
#define UPLOADER_HEALTH_RETRY_MS 3000U
#define UPLOADER_LOOP_MIN_WAIT_MS 200U

#define UPLOADER_PAUSE_DEFAULT_LEASE_MS 5000U
#define UPLOADER_PAUSE_MAX_LEASE_MS 60000U

/*
 * 네트워크 장애는 횟수만으로 영구 실패가 되지 않는다. attempts는 관측용 누적
 * 값이며, 실제 재시도 간격만 아래 상한까지 증가한 뒤 그 간격으로 계속 반복한다.
 */
#define UPLOADER_RETRY_BASE_MS 5000U
#define UPLOADER_RETRY_MAX_MS 60000U

/* 실행 중 Queue 등록 누락과 오래된 UPLOAD를 스스로 복구하는 주기/lease임. */
#define UPLOADER_SPOOL_RECONCILE_MS 10000U
#define UPLOADER_LEASE_MS 600000U
#define UPLOADER_STALE_INCOMING_SECONDS 300U

/* 같은 프로세스 안의 Queue 결과 보고가 일시 실패할 때 사용하는 짧은 재시도임. */
#define UPLOADER_RESULT_REPORT_ATTEMPTS 4U
#define UPLOADER_RESULT_REPORT_BASE_MS 100U

/* 새 spool 복사본을 commit하기 전에 최소 512 MiB의 여유 공간을 남긴다. */
#define UPLOADER_DEFAULT_MIN_FREE_BYTES \
    (512ULL * 1024ULL * 1024ULL)

#endif
