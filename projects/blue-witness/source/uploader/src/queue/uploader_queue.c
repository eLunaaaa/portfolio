#define _POSIX_C_SOURCE 200809L

#include "uploader_queue.h"

#include <limits.h>
#include <stdarg.h>
#include <stdio.h>
#include <string.h>
#include <time.h>

#include "uploader_settings.h"
#include "uploader_metadata.h"

/* =========================================================================
 * #### SQLite 공통 helper와 시간 기준
 * ========================================================================= */

/**
 * @brief DB 기록과 재시도 예약에 사용할 epoch millisecond를 구함.
 * @return CLOCK_REALTIME 기준 ms, clock_gettime 실패 시 0.
 */
static int64_t current_time_ms(void)
{
    struct timespec now;

    if (clock_gettime(CLOCK_REALTIME, &now) != 0) {
        return 0;
    }

    return ((int64_t)now.tv_sec * 1000LL)
        + ((int64_t)now.tv_nsec / 1000000LL);
}

/**
 * @brief 가변 인자 오류 내용을 호출자가 제공한 고정 buffer에 기록함.
 * @return 반환값 없음. NULL 또는 0 크기 buffer이면 아무 작업도 하지 않음.
 */
static void set_error(
    char *buffer,
    size_t buffer_size,
    const char *format,
    ...)
{
    va_list arguments;

    if (buffer == NULL || buffer_size == 0U) {
        return;
    }

    va_start(arguments, format);
    vsnprintf(buffer, buffer_size, format, arguments);
    va_end(arguments);
}

/**
 * @brief 결과 행을 읽지 않는 SQL을 실행하고 SQLite 오류를 일반 error로 변환함.
 * @return SQLITE_OK이면 0, 실행 실패 시 -1.
 */
static int execute_sql(
    sqlite3 *db,
    const char *sql,
    char *error,
    size_t error_size)
{
    char *sqlite_error = NULL;
    int result;

    result = sqlite3_exec(
        db,
        sql,
        NULL,
        NULL,
        &sqlite_error);

    if (result != SQLITE_OK) {
        set_error(
            error,
            error_size,
            "%s",
            sqlite_error != NULL
                ? sqlite_error
                : sqlite3_errmsg(db));

        sqlite3_free(sqlite_error);
        return -1;
    }

    return 0;
}

/**
 * @brief 진행 중인 transaction을 최선 노력으로 되돌림.
 * @return 반환값 없음. 원래 오류를 덮지 않기 위해 rollback 오류는 무시함.
 */
static void rollback_transaction(sqlite3 *db)
{
    sqlite3_exec(db, "ROLLBACK;", NULL, NULL, NULL);
}

/**
 * 기존 queue.db를 삭제하지 않고 새 열의 존재 여부를 확인한다.
 * SQLite의 PRAGMA 결과는 이 함수 안에서만 읽고 statement를 항상 닫는다.
 */
/* =========================================================================
 * #### 기존 queue.db 무중단 schema migration
 * ========================================================================= */

static int column_exists(
    sqlite3 *db,
    const char *wanted_name,
    int *exists,
    char *error,
    size_t error_size)
{
    sqlite3_stmt *statement = NULL;
    int result;

    *exists = 0;

    result = sqlite3_prepare_v2(
        db,
        "PRAGMA table_info(jobs);",
        -1,
        &statement,
        NULL);

    if (result != SQLITE_OK) {
        set_error(error, error_size, "%s", sqlite3_errmsg(db));
        return -1;
    }

    while ((result = sqlite3_step(statement)) == SQLITE_ROW) {
        const unsigned char *column_name =
            sqlite3_column_text(statement, 1);

        if (column_name != NULL &&
            strcmp(
                (const char *)column_name,
                wanted_name) == 0) {
            *exists = 1;
            break;
        }
    }

    sqlite3_finalize(statement);

    if (result != SQLITE_ROW &&
        result != SQLITE_DONE) {
        set_error(error, error_size, "%s", sqlite3_errmsg(db));
        return -1;
    }

    return 0;
}

/**
 * @brief 기존 jobs table에 재시도 예약 열이 없을 때만 추가함.
 * @return 열이 있거나 추가 성공 시 0, PRAGMA/ALTER 실패 시 -1.
 */
static int apply_retry_migration(
    sqlite3 *db,
    char *error,
    size_t error_size)
{
    int exists = 0;

    if (column_exists(
            db,
            "next_attempt_at_ms",
            &exists,
            error,
            error_size) != 0) {
        return -1;
    }

    if (exists != 0) {
        return 0;
    }

    return execute_sql(
        db,
        "ALTER TABLE jobs "
        "ADD COLUMN next_attempt_at_ms "
        "INTEGER NOT NULL DEFAULT 0;",
        error,
        error_size);
}

/**
 * 1~5단계 DB를 보존하면서 ACK 유실 복구 플래그를 추가한다.
 * DEFAULT 0이므로 기존 PEND 작업은 예전과 동일하게 바로 업로드한다.
 */
/**
 * @brief 기존 jobs table에 서버 저장 확인 플래그가 없을 때만 추가함.
 * @return 열이 있거나 추가 성공 시 0, migration 실패 시 -1.
 */
static int apply_verification_migration(
    sqlite3 *db,
    char *error,
    size_t error_size)
{
    int exists = 0;

    if (column_exists(
            db,
            "verification_required",
            &exists,
            error,
            error_size) != 0) {
        return -1;
    }

    if (exists != 0) {
        return 0;
    }

    return execute_sql(
        db,
        "ALTER TABLE jobs "
        "ADD COLUMN verification_required "
        "INTEGER NOT NULL DEFAULT 0 "
        "CHECK(verification_required IN (0,1));",
        error,
        error_size);
}

/* =========================================================================
 * #### 재시도 정책 계산
 * ========================================================================= */

/**
 * @brief 현재 시도 횟수에 맞는 지수 backoff를 계산함.
 * @return 기본 5초부터 증가하고 최대 60초로 제한한 millisecond.
 */
static uint32_t calculate_retry_delay(uint32_t attempts)
{
    uint64_t delay = UPLOADER_RETRY_BASE_MS;
    uint32_t index;

    if (attempts == 0U) {
        attempts = 1U;
    }

    for (index = 1U; index < attempts; index++) {
        delay *= 2U;

        if (delay >= UPLOADER_RETRY_MAX_MS) {
            return UPLOADER_RETRY_MAX_MS;
        }
    }

    if (delay > UPLOADER_RETRY_MAX_MS) {
        delay = UPLOADER_RETRY_MAX_MS;
    }

    return (uint32_t)delay;
}

/* =========================================================================
 * #### Queue DB 수명주기와 프로세스 재시작 복구
 * ========================================================================= */

/**
 * @brief Queue DB를 열고 내구성 설정, schema, migration, index를 준비함.
 * @param db 성공 시 Queue 스레드가 단독 소유할 SQLite handle.
 * @return 모든 초기화가 성공하면 0, 인자/SQLite/schema 실패 시 -1.
 * @note WAL과 synchronous=FULL을 사용하며 busy timeout은 5초임.
 */
static int recover_legacy_metadata_failures(queue_db_t *db)
{
    sqlite3_int64 last_id = 0;
    for (;;) {
        sqlite3_stmt *statement = NULL;
        sqlite3_int64 row_id;
        char path[UPLOADER_PATH_SIZE];
        char metadata[UPLOADER_METADATA_MAX_SIZE + 1U];
        char error[UPLOADER_ERROR_SIZE];
        size_t size = 0U;
        int removed = 0, result;
        const unsigned char *stored_path;
        if (sqlite3_prepare_v2(db->handle,
                "SELECT id,metadata_path FROM jobs WHERE id>? AND status='FAIL' "
                "AND last_error='upload rejected: HTTP 422' ORDER BY id LIMIT 1",
                -1, &statement, NULL) != SQLITE_OK) return -1;
        sqlite3_bind_int64(statement, 1, last_id);
        result = sqlite3_step(statement);
        if (result != SQLITE_ROW) {
            sqlite3_finalize(statement);
            return result == SQLITE_DONE ? 0 : -1;
        }
        row_id = sqlite3_column_int64(statement, 0);
        stored_path = sqlite3_column_text(statement, 1);
        snprintf(path, sizeof(path), "%s", stored_path ? (const char *)stored_path : "");
        sqlite3_finalize(statement);
        last_id = row_id;
        if (metadata_load(path, metadata, sizeof(metadata), &size, error, sizeof(error)) != 0 ||
            metadata_prepare_http(metadata, &size, &removed, error, sizeof(error)) != 0 ||
            !removed) continue;
        if (sqlite3_prepare_v2(db->handle,
                "UPDATE jobs SET status='PEND',next_attempt_at_ms=0,completed_at_ms=NULL,"
                "verification_required=0,last_error='legacy HTTP metadata normalized' "
                "WHERE id=? AND status='FAIL' AND last_error='upload rejected: HTTP 422'",
                -1, &statement, NULL) != SQLITE_OK) return -1;
        sqlite3_bind_int64(statement, 1, row_id);
        result = sqlite3_step(statement);
        sqlite3_finalize(statement);
        if (result != SQLITE_DONE) return -1;
    }
}

int queue_db_open(
    queue_db_t *db,
    const char *database_path,
    char *error,
    size_t error_size)
{
    static const char *schema =
        "CREATE TABLE IF NOT EXISTS jobs ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT,"
        "job_id TEXT NOT NULL UNIQUE,"
        "video_path TEXT NOT NULL,"
        "metadata_path TEXT NOT NULL,"
        "status TEXT NOT NULL "
        "CHECK(status IN ('PEND','UPLOAD','DONE','FAIL')),"
        "attempts INTEGER NOT NULL DEFAULT 0,"
        "last_error TEXT NOT NULL DEFAULT '',"
        "created_at_ms INTEGER NOT NULL,"
        "updated_at_ms INTEGER NOT NULL,"
        "claimed_at_ms INTEGER,"
        "completed_at_ms INTEGER,"
        "next_attempt_at_ms INTEGER NOT NULL DEFAULT 0,"
        "verification_required INTEGER NOT NULL DEFAULT 0 "
        "CHECK(verification_required IN (0,1))"
        ");";

    int result;

    if (db == NULL || database_path == NULL) {
        set_error(error, error_size, "invalid database argument");
        return -1;
    }

    memset(db, 0, sizeof(*db));

    result = sqlite3_open_v2(
        database_path,
        &db->handle,
        SQLITE_OPEN_READWRITE |
        SQLITE_OPEN_CREATE |
        SQLITE_OPEN_FULLMUTEX,
        NULL);

    if (result != SQLITE_OK) {
        set_error(
            error,
            error_size,
            "open %s: %s",
            database_path,
            db->handle != NULL
                ? sqlite3_errmsg(db->handle)
                : "unknown SQLite error");

        queue_db_close(db);
        return -1;
    }

    sqlite3_busy_timeout(db->handle, 5000);

    if (execute_sql(
            db->handle,
            "PRAGMA journal_mode=WAL;"
            "PRAGMA synchronous=FULL;"
            "PRAGMA foreign_keys=ON;",
            error,
            error_size) != 0) {
        queue_db_close(db);
        return -1;
    }

    if (execute_sql(
            db->handle,
            schema,
            error,
            error_size) != 0 ||
        apply_retry_migration(
            db->handle,
            error,
            error_size) != 0 ||
        apply_verification_migration(
            db->handle,
            error,
            error_size) != 0 ||
        execute_sql(
            db->handle,
            "CREATE INDEX IF NOT EXISTS idx_jobs_fifo "
            "ON jobs(status, id);"
            "CREATE INDEX IF NOT EXISTS idx_jobs_retry_ready "
            "ON jobs(status, next_attempt_at_ms, id);",
            error,
            error_size) != 0) {
        queue_db_close(db);
        return -1;
    }

    if (recover_legacy_metadata_failures(db) != 0) {
        set_error(error, error_size, "legacy metadata recovery: %s", sqlite3_errmsg(db->handle));
        queue_db_close(db);
        return -1;
    }
    return 0;
}

/**
 * @brief Queue 스레드가 소유한 SQLite handle을 닫고 NULL로 초기화함.
 * @return 반환값 없음.
 */
void queue_db_close(queue_db_t *db)
{
    if (db == NULL || db->handle == NULL) {
        return;
    }

    sqlite3_wal_checkpoint_v2(
        db->handle,
        NULL,
        SQLITE_CHECKPOINT_PASSIVE,
        NULL,
        NULL);

    sqlite3_close(db->handle);
    db->handle = NULL;
}

/**
 * @brief 비정상 종료 당시 UPLOAD였던 행을 PEND+검증 필요 상태로 복구함.
 * @param recovered_count 실제로 바뀐 행 수를 돌려받음.
 * @return 복구 SQL 성공 시 0, 실패 시 -1.
 * @note ACK가 유실됐을 수 있으므로 재업로드 전에 서버 상태를 먼저 조회하게 함.
 */
int queue_db_recover_uploading(
    queue_db_t *db,
    int *recovered_count,
    char *error,
    size_t error_size)
{
    sqlite3_stmt *statement = NULL;
    const char *sql =
        "UPDATE jobs "
        "SET status='PEND',"
        "last_error='recovered after queue process restart',"
        "updated_at_ms=?,"
        "claimed_at_ms=NULL,"
        "next_attempt_at_ms=0,"
        "verification_required=1 "
        "WHERE status='UPLOAD';";
    int result;

    *recovered_count = 0;

    result = sqlite3_prepare_v2(
        db->handle,
        sql,
        -1,
        &statement,
        NULL);

    if (result != SQLITE_OK) {
        set_error(error, error_size, "%s", sqlite3_errmsg(db->handle));
        return -1;
    }

    sqlite3_bind_int64(statement, 1, current_time_ms());

    result = sqlite3_step(statement);
    sqlite3_finalize(statement);

    if (result != SQLITE_DONE) {
        set_error(error, error_size, "%s", sqlite3_errmsg(db->handle));
        return -1;
    }

    *recovered_count = sqlite3_changes(db->handle);
    return 0;
}

/**
 * @brief lease 시간보다 오래 UPLOAD에 머문 작업을 PEND로 되돌림.
 * @return 복구 SQL 성공 시 0, SQLite 실패 시 -1.
 * @note ACK 유실 가능성이 있으므로 verification_required를 함께 설정함.
 */
int queue_db_recover_expired_uploads(
    queue_db_t *db,
    uint32_t lease_ms,
    int *recovered_count,
    char *error,
    size_t error_size)
{
    sqlite3_stmt *statement = NULL;
    const char *sql =
        "UPDATE jobs "
        "SET status='PEND',"
        "last_error='recovered after upload lease expiration',"
        "updated_at_ms=?,claimed_at_ms=NULL,next_attempt_at_ms=?,"
        "verification_required=1 "
        "WHERE status='UPLOAD' AND claimed_at_ms IS NOT NULL "
        "AND claimed_at_ms<=?;";
    int64_t now = current_time_ms();
    int result;

    *recovered_count = 0;
    result = sqlite3_prepare_v2(db->handle, sql, -1, &statement, NULL);
    if (result != SQLITE_OK) {
        set_error(error, error_size, "%s", sqlite3_errmsg(db->handle));
        return -1;
    }

    sqlite3_bind_int64(statement, 1, now);
    sqlite3_bind_int64(statement, 2, now);
    sqlite3_bind_int64(statement, 3, now - (int64_t)lease_ms);
    result = sqlite3_step(statement);
    sqlite3_finalize(statement);
    if (result != SQLITE_DONE) {
        set_error(error, error_size, "%s", sqlite3_errmsg(db->handle));
        return -1;
    }

    *recovered_count = sqlite3_changes(db->handle);
    return 0;
}

/**
 * @brief 상태를 UPLOAD로 바꾸지 않고 현재 실행 가능한 PEND 작업 존재 여부를 조회함.
 * @return 조회 성공 시 0, SQLite 실패 시 -1.
 * @note Worker가 spool 복구 시점을 판단할 때 불필요한 claim을 막기 위해 사용함.
 */
int queue_db_peek_ready(
    queue_db_t *db,
    int *found,
    uint32_t *retry_after_ms,
    char *error,
    size_t error_size)
{
    sqlite3_stmt *statement = NULL;
    int64_t now = current_time_ms();
    int64_t earliest = 0;
    int has_pending = 0;
    int result;

    *found = 0;
    *retry_after_ms = UPLOADER_NO_JOB_RETRY_MS;
    result = sqlite3_prepare_v2(
        db->handle,
        "SELECT MIN(next_attempt_at_ms) FROM jobs WHERE status='PEND';",
        -1,
        &statement,
        NULL);
    if (result != SQLITE_OK) {
        set_error(error, error_size, "%s", sqlite3_errmsg(db->handle));
        return -1;
    }

    result = sqlite3_step(statement);
    if (result == SQLITE_ROW &&
        sqlite3_column_type(statement, 0) != SQLITE_NULL) {
        earliest = sqlite3_column_int64(statement, 0);
        has_pending = 1;
    }
    sqlite3_finalize(statement);
    if (result != SQLITE_ROW) {
        set_error(error, error_size, "%s", sqlite3_errmsg(db->handle));
        return -1;
    }

    if (has_pending == 0) {
        *retry_after_ms = UINT_MAX;
    } else if (earliest <= now) {
        *found = 1;
        *retry_after_ms = 0U;
    } else {
        int64_t difference = earliest - now;
        *retry_after_ms = difference > UINT_MAX
            ? UINT_MAX
            : (uint32_t)difference;
    }
    return 0;
}

/* =========================================================================
 * #### FIFO 작업 생성과 claim
 * ========================================================================= */

/**
 * @brief 완성 spool 경로를 PEND 작업으로 transaction 안에서 등록함.
 * @param queue_id 새 행 또는 기존 멱등 행의 ID.
 * @param inserted 새 삽입이면 1, 동일 job/path 재등록이면 0.
 * @return 등록 또는 안전한 멱등 확인 성공 시 0, 충돌/DB 실패 시 -1.
 */
int queue_db_enqueue(
    queue_db_t *db,
    const queue_enqueue_t *request,
    int64_t *queue_id,
    int *inserted,
    char *error,
    size_t error_size)
{
    sqlite3_stmt *statement = NULL;
    const char *insert_sql =
        "INSERT OR IGNORE INTO jobs ("
        "job_id, video_path, metadata_path, status,"
        "attempts, last_error, created_at_ms, updated_at_ms,"
        "next_attempt_at_ms"
        ") VALUES (?, ?, ?, 'PEND', 0, '', ?, ?, 0);";
    const char *select_sql =
        "SELECT id, video_path, metadata_path "
        "FROM jobs WHERE job_id=?;";
    int64_t now = current_time_ms();
    int result;

    *queue_id = 0;
    *inserted = 0;

    if (execute_sql(
            db->handle,
            "BEGIN IMMEDIATE;",
            error,
            error_size) != 0) {
        return -1;
    }

    result = sqlite3_prepare_v2(
        db->handle,
        insert_sql,
        -1,
        &statement,
        NULL);

    if (result != SQLITE_OK) {
        set_error(error, error_size, "%s", sqlite3_errmsg(db->handle));
        rollback_transaction(db->handle);
        return -1;
    }

    sqlite3_bind_text(
        statement, 1, request->job_id, -1, SQLITE_TRANSIENT);
    sqlite3_bind_text(
        statement, 2, request->video_path, -1, SQLITE_TRANSIENT);
    sqlite3_bind_text(
        statement, 3, request->metadata_path, -1, SQLITE_TRANSIENT);
    sqlite3_bind_int64(statement, 4, now);
    sqlite3_bind_int64(statement, 5, now);

    result = sqlite3_step(statement);
    sqlite3_finalize(statement);
    statement = NULL;

    if (result != SQLITE_DONE) {
        set_error(error, error_size, "%s", sqlite3_errmsg(db->handle));
        rollback_transaction(db->handle);
        return -1;
    }

    if (sqlite3_changes(db->handle) == 1) {
        *queue_id = sqlite3_last_insert_rowid(db->handle);
        *inserted = 1;
    } else {
        const unsigned char *stored_video;
        const unsigned char *stored_metadata;

        result = sqlite3_prepare_v2(
            db->handle,
            select_sql,
            -1,
            &statement,
            NULL);

        if (result != SQLITE_OK) {
            set_error(error, error_size, "%s", sqlite3_errmsg(db->handle));
            rollback_transaction(db->handle);
            return -1;
        }

        sqlite3_bind_text(
            statement,
            1,
            request->job_id,
            -1,
            SQLITE_TRANSIENT);

        result = sqlite3_step(statement);

        if (result != SQLITE_ROW) {
            set_error(error, error_size, "duplicate lookup failed");
            sqlite3_finalize(statement);
            rollback_transaction(db->handle);
            return -1;
        }

        *queue_id = sqlite3_column_int64(statement, 0);
        stored_video = sqlite3_column_text(statement, 1);
        stored_metadata = sqlite3_column_text(statement, 2);

        if (stored_video == NULL ||
            stored_metadata == NULL ||
            strcmp(
                (const char *)stored_video,
                request->video_path) != 0 ||
            strcmp(
                (const char *)stored_metadata,
                request->metadata_path) != 0) {
            set_error(
                error,
                error_size,
                "job_id already exists with different paths");

            sqlite3_finalize(statement);
            rollback_transaction(db->handle);
            return -1;
        }

        sqlite3_finalize(statement);
    }

    if (execute_sql(
            db->handle,
            "COMMIT;",
            error,
            error_size) != 0) {
        rollback_transaction(db->handle);
        return -1;
    }

    return 0;
}

/**
 * @brief 재시도 시각이 지난 가장 오래된 PEND를 원자적으로 UPLOAD로 claim함.
 * @param job found=1일 때 Worker에 전달할 경로와 시도 정보를 돌려받음.
 * @param found claim한 작업이 있으면 1, 없으면 0.
 * @param retry_after_ms 작업이 없을 때 다음 확인까지의 권장 대기 시간.
 * @return SELECT와 상태 전이가 성공하면 0, DB 실패 시 -1.
 */
int queue_db_claim_next(
    queue_db_t *db,
    queue_job_t *job,
    int *found,
    uint32_t *retry_after_ms,
    char *error,
    size_t error_size)
{
    sqlite3_stmt *statement = NULL;
    const char *select_sql =
        "SELECT id, job_id, video_path, metadata_path, attempts, "
        "verification_required "
        "FROM jobs "
        "WHERE status='PEND' AND next_attempt_at_ms<=? "
        "ORDER BY id ASC "
        "LIMIT 1;";
    const char *update_sql =
        "UPDATE jobs "
        "SET status='UPLOAD',"
        /* 매우 오래 재시도해도 정수 overflow로 Queue가 깨지지 않게 포화시킴. */
        "attempts=CASE WHEN attempts<4294967295 THEN attempts+1 ELSE attempts END,"
        "last_error='',"
        "updated_at_ms=?,"
        "claimed_at_ms=?,"
        "next_attempt_at_ms=0 "
        "WHERE id=? AND status='PEND';";
    int64_t now = current_time_ms();
    int result;

    memset(job, 0, sizeof(*job));
    *found = 0;
    *retry_after_ms = UPLOADER_NO_JOB_RETRY_MS;

    if (execute_sql(
            db->handle,
            "BEGIN IMMEDIATE;",
            error,
            error_size) != 0) {
        return -1;
    }

    result = sqlite3_prepare_v2(
        db->handle,
        select_sql,
        -1,
        &statement,
        NULL);

    if (result != SQLITE_OK) {
        set_error(error, error_size, "%s", sqlite3_errmsg(db->handle));
        rollback_transaction(db->handle);
        return -1;
    }

    sqlite3_bind_int64(statement, 1, now);
    result = sqlite3_step(statement);

    if (result == SQLITE_DONE) {
        int64_t earliest = 0;

        sqlite3_finalize(statement);
        statement = NULL;

        result = sqlite3_prepare_v2(
            db->handle,
            "SELECT MIN(next_attempt_at_ms) "
            "FROM jobs WHERE status='PEND';",
            -1,
            &statement,
            NULL);

        if (result != SQLITE_OK) {
            set_error(error, error_size, "%s", sqlite3_errmsg(db->handle));
            rollback_transaction(db->handle);
            return -1;
        }

        if (sqlite3_step(statement) == SQLITE_ROW &&
            sqlite3_column_type(statement, 0) != SQLITE_NULL) {
            earliest = sqlite3_column_int64(statement, 0);
        }

        sqlite3_finalize(statement);

        if (earliest > now) {
            int64_t difference = earliest - now;

            *retry_after_ms =
                difference > UINT_MAX
                    ? UINT_MAX
                    : (uint32_t)difference;
        } else if (earliest == 0) {
            /* 대기 중인 PEND가 전혀 없음을 Worker에 알림. 새 ENQUEUE가 condition을
             * 깨울 때까지 무기한 잠들 수 있으므로 3초 polling이 필요하지 않음. */
            *retry_after_ms = UINT_MAX;
        }

        if (execute_sql(
                db->handle,
                "COMMIT;",
                error,
                error_size) != 0) {
            rollback_transaction(db->handle);
            return -1;
        }

        return 0;
    }

    if (result != SQLITE_ROW) {
        set_error(error, error_size, "%s", sqlite3_errmsg(db->handle));
        sqlite3_finalize(statement);
        rollback_transaction(db->handle);
        return -1;
    }

    job->version = UPLOADER_PROTOCOL_VERSION;
    job->queue_id = sqlite3_column_int64(statement, 0);

    snprintf(
        job->job_id,
        sizeof(job->job_id),
        "%s",
        sqlite3_column_text(statement, 1));

    snprintf(
        job->video_path,
        sizeof(job->video_path),
        "%s",
        sqlite3_column_text(statement, 2));

    snprintf(
        job->metadata_path,
        sizeof(job->metadata_path),
        "%s",
        sqlite3_column_text(statement, 3));

    {
        sqlite3_int64 stored_attempts = sqlite3_column_int64(statement, 4);
        job->attempts = stored_attempts >= UINT32_MAX
            ? UINT32_MAX
            : (uint32_t)stored_attempts + 1U;
    }
    job->verification_required =
        sqlite3_column_int(statement, 5) != 0 ? 1U : 0U;

    sqlite3_finalize(statement);
    statement = NULL;

    result = sqlite3_prepare_v2(
        db->handle,
        update_sql,
        -1,
        &statement,
        NULL);

    if (result != SQLITE_OK) {
        set_error(error, error_size, "%s", sqlite3_errmsg(db->handle));
        rollback_transaction(db->handle);
        return -1;
    }

    sqlite3_bind_int64(statement, 1, now);
    sqlite3_bind_int64(statement, 2, now);
    sqlite3_bind_int64(statement, 3, job->queue_id);

    result = sqlite3_step(statement);
    sqlite3_finalize(statement);

    if (result != SQLITE_DONE ||
        sqlite3_changes(db->handle) != 1) {
        set_error(error, error_size, "claim state update failed");
        rollback_transaction(db->handle);
        return -1;
    }

    if (execute_sql(
            db->handle,
            "COMMIT;",
            error,
            error_size) != 0) {
        rollback_transaction(db->handle);
        return -1;
    }

    *found = 1;
    *retry_after_ms = 0U;

    return 0;
}

/* =========================================================================
 * #### Worker 결과에 따른 상태 전이
 * ========================================================================= */

/**
 * @brief 서버 저장 ACK 검증을 마친 UPLOAD 작업을 DONE으로 변경함.
 * @return 정확히 한 UPLOAD 행이 변경되면 0, 상태 불일치 또는 DB 실패 시 -1.
 */
int queue_db_mark_done(
    queue_db_t *db,
    int64_t queue_id,
    char *error,
    size_t error_size)
{
    sqlite3_stmt *statement = NULL;
    const char *sql =
        "UPDATE jobs "
        "SET status='DONE',"
        "last_error='',"
        "updated_at_ms=?,"
        "completed_at_ms=?,"
        "next_attempt_at_ms=0,"
        "verification_required=0 "
        "WHERE id=? AND status='UPLOAD';";
    int64_t now = current_time_ms();
    int result;

    result = sqlite3_prepare_v2(
        db->handle,
        sql,
        -1,
        &statement,
        NULL);

    if (result != SQLITE_OK) {
        set_error(error, error_size, "%s", sqlite3_errmsg(db->handle));
        return -1;
    }

    sqlite3_bind_int64(statement, 1, now);
    sqlite3_bind_int64(statement, 2, now);
    sqlite3_bind_int64(statement, 3, queue_id);

    result = sqlite3_step(statement);
    sqlite3_finalize(statement);

    if (result != SQLITE_DONE ||
        sqlite3_changes(db->handle) != 1) {
        set_error(
            error,
            error_size,
            "queue_id is missing or not in UPLOAD state");

        return -1;
    }

    return 0;
}

/**
 * @brief Worker 실패를 backoff PEND 또는 terminal FAIL로 transaction 전이함.
 * @param retriable 일시 오류 여부.
 * @param verification_required 서버 저장 여부를 먼저 조회할지 여부.
 * @param transition 실제 선택된 RETRY/TERMINAL 전이를 돌려받음.
 * @param attempts 현재 누적 시도 횟수를 돌려받음.
 * @param retry_after_ms RETRY일 때 계산된 지연 시간을 돌려받음.
 * @return 상태 전이와 commit 성공 시 0, 현재 상태 불일치 또는 DB 실패 시 -1.
 */
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
    size_t error_size)
{
    sqlite3_stmt *statement = NULL;
    int64_t now = current_time_ms();
    uint32_t delay = 0U;
    sqlite3_int64 stored_attempts;
    int result;

    *transition = QUEUE_FAILURE_TERMINAL;
    *attempts = 0U;
    *retry_after_ms = 0U;

    if (execute_sql(
            db->handle,
            "BEGIN IMMEDIATE;",
            error,
            error_size) != 0) {
        return -1;
    }

    result = sqlite3_prepare_v2(
        db->handle,
        "SELECT attempts FROM jobs "
        "WHERE id=? AND status='UPLOAD';",
        -1,
        &statement,
        NULL);

    if (result != SQLITE_OK) {
        set_error(error, error_size, "%s", sqlite3_errmsg(db->handle));
        rollback_transaction(db->handle);
        return -1;
    }

    sqlite3_bind_int64(statement, 1, queue_id);
    result = sqlite3_step(statement);

    if (result != SQLITE_ROW) {
        set_error(
            error,
            error_size,
            "queue_id is missing or not in UPLOAD state");

        sqlite3_finalize(statement);
        rollback_transaction(db->handle);
        return -1;
    }

    stored_attempts = sqlite3_column_int64(statement, 0);
    sqlite3_finalize(statement);
    statement = NULL;

    *attempts = stored_attempts <= 0
        ? 0U
        : (stored_attempts >= UINT32_MAX
            ? UINT32_MAX
            : (uint32_t)stored_attempts);

    /*
     * 네트워크·서버의 일시 장애는 횟수와 무관하게 영구 재시도한다. FAIL은
     * 호출자가 영상 손상, 잘못된 metadata, 인증/규약 오류처럼 복구 불가능하다고
     * 명시한 경우(retriable=0)에만 허용한다.
     */
    if (retriable != 0) {
        uint32_t jitter;

        delay = calculate_retry_delay(*attempts);
        jitter = (uint32_t)(((uint64_t)queue_id ^ (uint64_t)now) % 1001U);

        if (delay < UPLOADER_RETRY_MAX_MS - jitter) {
            delay += jitter;
        } else {
            delay = UPLOADER_RETRY_MAX_MS;
        }

        result = sqlite3_prepare_v2(
            db->handle,
            "UPDATE jobs "
            "SET status='PEND',"
            "last_error=?,"
            "updated_at_ms=?,"
            "claimed_at_ms=NULL,"
            "completed_at_ms=NULL,"
            "next_attempt_at_ms=?,"
            "verification_required=? "
            "WHERE id=? AND status='UPLOAD';",
            -1,
            &statement,
            NULL);

        *transition = QUEUE_FAILURE_RETRY;
        *retry_after_ms = delay;
    } else {
        result = sqlite3_prepare_v2(
            db->handle,
            "UPDATE jobs "
            "SET status='FAIL',"
            "last_error=?,"
            "updated_at_ms=?,"
            "completed_at_ms=?,"
            "next_attempt_at_ms=0,"
            "verification_required=0 "
            "WHERE id=? AND status='UPLOAD';",
            -1,
            &statement,
            NULL);

        *transition = QUEUE_FAILURE_TERMINAL;
    }

    if (result != SQLITE_OK) {
        set_error(error, error_size, "%s", sqlite3_errmsg(db->handle));
        rollback_transaction(db->handle);
        return -1;
    }

    sqlite3_bind_text(
        statement,
        1,
        last_error != NULL ? last_error : "",
        -1,
        SQLITE_TRANSIENT);

    sqlite3_bind_int64(statement, 2, now);

    if (*transition == QUEUE_FAILURE_RETRY) {
        sqlite3_bind_int64(
            statement,
            3,
            now + (int64_t)delay);
        sqlite3_bind_int(
            statement,
            4,
            verification_required != 0 ? 1 : 0);
        sqlite3_bind_int64(statement, 5, queue_id);
    } else {
        sqlite3_bind_int64(statement, 3, now);
        sqlite3_bind_int64(statement, 4, queue_id);
    }

    result = sqlite3_step(statement);
    sqlite3_finalize(statement);

    if (result != SQLITE_DONE ||
        sqlite3_changes(db->handle) != 1) {
        set_error(error, error_size, "failure state update failed");
        rollback_transaction(db->handle);
        return -1;
    }

    if (execute_sql(
            db->handle,
            "COMMIT;",
            error,
            error_size) != 0) {
        rollback_transaction(db->handle);
        return -1;
    }

    return 0;
}
