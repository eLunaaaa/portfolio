#ifndef UPLOADER_SPOOL_H
#define UPLOADER_SPOOL_H

/**
 * @file uploader_spool.h
 * @brief 장애에 안전한 로컬 MP4/metadata commit 및 여유 공간 검사.
 * @scope 단일 uploader의 Ingest 스레드 전용.
 *
 * Camera는 저장을 완료한 원본 파일을 제공한다. Ingest만 이 파일들을 영속
 * spool로 복사하고 공개한 뒤 Queue에 작업 등록을 요청한다.
 */

#include <stddef.h>
#include <stdint.h>

#include "uploader_settings.h"

typedef enum {
    SPOOL_FAILURE_NONE = 0,
    SPOOL_FAILURE_INVALID_INPUT,
    SPOOL_FAILURE_SOURCE_NOT_READY,
    SPOOL_FAILURE_INSUFFICIENT_SPACE,
    SPOOL_FAILURE_TEMPORARY_STORAGE
} spool_failure_t;

typedef struct {
    char job_directory[UPLOADER_PATH_SIZE];
    char video_path[UPLOADER_PATH_SIZE];
    char metadata_path[UPLOADER_PATH_SIZE];

    uint64_t source_bytes;
    uint64_t available_bytes;

    int already_present;
    /* 최종 job 디렉터리가 공개되었으면 이후 오류가 나도 절대 삭제하지 않음. */
    int published;
    int cleanup_performed;
    int cleanup_succeeded;
    spool_failure_t failure;
} spool_result_t;

int spool_validate_job_id(
    const char *job_id,
    char *error,
    size_t error_size);

int spool_commit(
    const char *spool_root,
    const char *job_id,
    const char *source_video,
    const char *source_metadata,
    uint64_t minimum_free_bytes,
    spool_result_t *result,
    char *error,
    size_t error_size);

/* 강제 종료로 남은 오래된 .incoming-*만 안전 조건을 확인한 뒤 정리함. */
int spool_cleanup_stale_incoming(
    const char *spool_root,
    uint32_t minimum_age_seconds,
    int *removed_count,
    int *failed_count,
    char *error,
    size_t error_size);

#endif
