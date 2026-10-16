#ifndef UPLOADER_QUEUE_H
#define UPLOADER_QUEUE_H

/**
 * @file uploader_queue.h
 * @brief SQLite 기반 영속 FIFO와 작업 상태 전이 기능.
 * @scope 단일 uploader의 Queue 스레드 전용.
 *
 * Queue 스레드만 queue.db를 소유한다. Ingest/Worker 스레드는 생산자-소비자
 * 명령으로 요청하며 SQLite API를 직접 호출하지 않는다.
 */

#include <stddef.h>
#include <stdint.h>

#include <sqlite3.h>

#include "uploader_settings.h"

/** Ingest 스레드가 Queue 스레드에 생산하는 내부 명령 데이터임. */
typedef struct {
    uint32_t version;
    uint32_t reserved;
    char job_id[UPLOADER_JOB_ID_SIZE];
    char video_path[UPLOADER_PATH_SIZE];
    char metadata_path[UPLOADER_PATH_SIZE];
} queue_enqueue_t;

/** Queue 스레드가 Worker 스레드에 넘기는 내부 작업 데이터임. */
typedef struct {
    uint32_t version;
    uint32_t attempts;
    uint32_t verification_required;
    uint32_t reserved;
    int64_t queue_id;
    char job_id[UPLOADER_JOB_ID_SIZE];
    char video_path[UPLOADER_PATH_SIZE];
    char metadata_path[UPLOADER_PATH_SIZE];
} queue_job_t;

typedef struct {
    sqlite3 *handle;
} queue_db_t;

enum queue_failure_transition {
    QUEUE_FAILURE_RETRY = 1,
    QUEUE_FAILURE_TERMINAL = 2
};

int queue_db_open(
    queue_db_t *db,
    const char *database_path,
    char *error,
    size_t error_size);

void queue_db_close(queue_db_t *db);

int queue_db_recover_uploading(
    queue_db_t *db,
    int *recovered_count,
    char *error,
    size_t error_size);

int queue_db_recover_expired_uploads(
    queue_db_t *db,
    uint32_t lease_ms,
    int *recovered_count,
    char *error,
    size_t error_size);

/* 작업을 UPLOAD로 바꾸지 않고 준비된 PEND 존재 여부만 확인함. */
int queue_db_peek_ready(
    queue_db_t *db,
    int *found,
    uint32_t *retry_after_ms,
    char *error,
    size_t error_size);

int queue_db_enqueue(
    queue_db_t *db,
    const queue_enqueue_t *request,
    int64_t *queue_id,
    int *inserted,
    char *error,
    size_t error_size);

int queue_db_claim_next(
    queue_db_t *db,
    queue_job_t *job,
    int *found,
    uint32_t *retry_after_ms,
    char *error,
    size_t error_size);

int queue_db_mark_done(
    queue_db_t *db,
    int64_t queue_id,
    char *error,
    size_t error_size);

int queue_db_mark_failure(
    queue_db_t *db,
    int64_t queue_id,
    int retriable,
    int verification_required,
    const char *last_error,
    int *transition,
    uint32_t *attempts,
    uint32_t *retry_after_ms,
    char *error,
    size_t error_size);

#endif
