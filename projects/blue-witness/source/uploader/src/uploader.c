#define _POSIX_C_SOURCE 200809L

/**
 * @file uploader.c
 * @brief 단일 프로세스·업무 3스레드 영상 업로드 파이프라인.
 *
 * 스레드 구성은 다음과 같음.
 *  1. 메인(Ingest): 외부 CAMERA_READY IPC 수신, spool 저장, Queue 명령 생산
 *  2. Queue: SQLite 단독 소유, FIFO 상태 전이, Worker 작업 생산
 *  3. Worker: Health/HTTP/ACK 처리, Queue 결과 생산
 *
 * 스레드 사이는 Unix Socket을 사용하지 않음. 고정 크기 ring buffer와
 * pthread_mutex/pthread_cond를 이용한 생산자-소비자 방식으로만 통신함.
 * 외부 프로세스가 접속하는 Unix Domain Socket은 하나만 사용함.
 */

#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <inttypes.h>
#include <limits.h>
#include <math.h>
#include <pthread.h>
#include <poll.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/time.h>
#include <time.h>
#include <unistd.h>

#include "uploader_http.h"
#include "uploader_log.h"
#include "uploader_metadata.h"
#include "uploader_queue.h"
#include "uploader_ack.h"
#include "uploader_sha256.h"
#include "uploader_spool.h"
#include "uploader_settings.h"
#include "uploader_config.h"
#include "uploader_event_id.h"
#include "ipc.h"
#include "protocol.h"
#include "uploader_ipc_protocol.h"
#include "runtime_watchdog.h"
#include "uploader_watchdog.h"

#define UPLOADER_INTERNAL_COMMAND_CAPACITY 16U
#define UPLOADER_IPC_CLIENT_TIMEOUT_SECONDS 2

enum { WATCHDOG_INGEST, WATCHDOG_QUEUE, WATCHDOG_WORKER };
static ace_watchdog_t process_watchdog;
static _Thread_local unsigned watchdog_slot = WATCHDOG_INGEST;

/* 기능: 현재 스레드의 업로더 감시 기한을 갱신한다.
 * 입력: 허용 정체 시간 timeout_ms.
 * 출력·구조: 반환값 없음. 스레드별 watchdog_slot을 공통 감시기에 전달한다.
 */
void uploader_watchdog_progress(uint32_t timeout_ms)
{
    ace_watchdog_progress(&process_watchdog, watchdog_slot, timeout_ms);
}

/* 기능: 수신·큐·전송 작업자의 진행 감시를 시작한다.
 * 입력: 전역 process_watchdog와 세 슬롯 이름.
 * 출력·구조: 성공 0 또는 감시 시작 오류. 스풀 복구 중에는 아직 시작하지 않은 worker 슬롯의 기한을 유예한다.
 */
static int start_process_watchdog(void)
{
    static const char *const slots[] = {"ingest", "queue", "worker"};
    int result = ace_watchdog_start(&process_watchdog, slots, 3U);
    if (result == 0) {
        /* Worker starts after spool recovery. Only the running Ingest/Queue
         * slots supervise that recovery, however many jobs it contains. */
        ace_watchdog_progress(&process_watchdog, WATCHDOG_WORKER, UINT64_MAX);
    }
    return result;
}

/* 기능: 단조 증가 시계 기준 조건 변수를 초기화한다.
 * 입력: 초기화할 pthread_cond_t 포인터.
 * 출력·구조: pthread 상태 코드. 조건 속성을 CLOCK_MONOTONIC으로 설정해 벽시계 변경에 따른 대기 오류를 줄인다.
 */
static int monotonic_cond_init(pthread_cond_t *condition)
{
    pthread_condattr_t attributes;
    int result = pthread_condattr_init(&attributes);
    if (result != 0) return result;
    result = pthread_condattr_setclock(&attributes, CLOCK_MONOTONIC);
    if (result == 0) result = pthread_cond_init(condition, &attributes);
    pthread_condattr_destroy(&attributes);
    return result;
}

typedef enum {
    UPLOADER_COMMAND_ENQUEUE = 1,
    UPLOADER_COMMAND_REQUEST_JOB = 2,
    UPLOADER_COMMAND_MARK_DONE = 3,
    UPLOADER_COMMAND_MARK_FAILURE = 4,
    UPLOADER_COMMAND_PAUSE = 5,
    UPLOADER_COMMAND_RESUME = 6,
    UPLOADER_COMMAND_CONTROL_QUERY = 7,
    UPLOADER_COMMAND_PEEK_JOB = 8
} uploader_command_type_t;

typedef struct {
    pthread_mutex_t mutex;
    pthread_cond_t changed;
    int completed;
    int code;
    int found;
    int inserted;
    int transition;
    int64_t queue_id;
    uint32_t attempts;
    uint32_t retry_after_ms;
    uint64_t work_generation;
    uint32_t paused;
    uint32_t remaining_ms;
    uint64_t control_generation;
    queue_job_t job;
    char error[UPLOADER_ERROR_SIZE];
    char reason[UPLOADER_PAUSE_REASON_SIZE];
} uploader_command_reply_t;

typedef struct {
    uploader_command_type_t type;
    uploader_command_reply_t *reply;
    queue_enqueue_t enqueue;
    int64_t queue_id;
    int retriable;
    int verification_required;
    uint32_t requester_pid;
    uint32_t lease_ms;
    char reason[UPLOADER_PAUSE_REASON_SIZE];
    char error[UPLOADER_ERROR_SIZE];
} uploader_internal_command_t;

typedef struct {
    int paused;
    uint64_t expires_at_ns;
    uint64_t generation;
    uint32_t requester_pid;
    char reason[UPLOADER_PAUSE_REASON_SIZE];
} uploader_control_state_internal_t;

typedef struct {
    pthread_mutex_t mutex;
    pthread_cond_t command_added;
    pthread_cond_t command_removed;
    pthread_cond_t work_changed;
    pthread_cond_t queue_started;

    uploader_internal_command_t commands[UPLOADER_INTERNAL_COMMAND_CAPACITY];
    size_t command_head;
    size_t command_tail;
    size_t command_count;

    uint64_t work_generation;
    int stopping;
    int fatal_error;
    int queue_ready;
    int queue_start_code;
    char queue_start_error[UPLOADER_ERROR_SIZE];
} uploader_runtime_t;

typedef struct {
    uploader_runtime_t *runtime;
    const char *database_path;
} queue_thread_args_t;

typedef struct {
    uploader_runtime_t *runtime;
    const char *server_url;
    worker_config_t *config;
    const char *spool_root;
    uint64_t reserve_bytes;
} worker_thread_args_t;

static volatile sig_atomic_t stop_requested = 0;

/* =========================================================================
 * #### 프로세스 종료 신호와 시간 기준
 *
 * signal handler는 비동기 신호 안전성을 지키기 위해 플래그만 변경함. 실제
 * socket, thread, mutex 정리는 정상 실행 흐름으로 돌아온 main()이 담당함.
 * ========================================================================= */

/**
 * @brief SIGINT/SIGTERM 수신 사실을 메인 실행 흐름에 기록함.
 * @param signal_number 수신한 신호 번호. 현재는 종류와 관계없이 종료 요청으로 처리함.
 * @return 반환값 없음.
 * @note signal handler 안에서 lock, 로그, 메모리 할당을 수행하지 않음.
 */
static void handle_signal(int signal_number)
{
    (void)signal_number;
    stop_requested = 1;
}

/**
 * @brief 시스템 시각 보정의 영향을 받지 않는 단조 증가 시간을 구함.
 * @return 성공 시 CLOCK_MONOTONIC 기준 ns, 실패 시 0.
 * @note IPC header timestamp와 PAUSE lease 만료 계산에 사용함.
 */
static uint64_t monotonic_time_ns(void)
{
    struct timespec now;

    if (clock_gettime(CLOCK_MONOTONIC, &now) != 0) {
        return 0U;
    }

    return ((uint64_t)now.tv_sec * 1000000000ULL) +
        (uint64_t)now.tv_nsec;
}

/* =========================================================================
 * #### 내부 런타임 수명주기
 *
 * 세 스레드가 공유하는 bounded queue, 종료 상태, 조건변수를 초기화하고 정리함.
 * ========================================================================= */

/**
 * @brief 내부 생산자-소비자 runtime의 mutex와 condition variable을 초기화함.
 * @param runtime 초기화할 공유 runtime.
 * @return 성공 시 0, pthread 자원 초기화 실패 시 -1.
 * @note 중간에 실패하면 이미 만든 자원을 역순으로 정리함.
 */
static int runtime_init(uploader_runtime_t *runtime)
{
    memset(runtime, 0, sizeof(*runtime));

    if (pthread_mutex_init(&runtime->mutex, NULL) != 0) {
        return -1;
    }
    if (monotonic_cond_init(&runtime->command_added) != 0) {
        pthread_mutex_destroy(&runtime->mutex);
        return -1;
    }
    if (monotonic_cond_init(&runtime->command_removed) != 0) {
        pthread_cond_destroy(&runtime->command_added);
        pthread_mutex_destroy(&runtime->mutex);
        return -1;
    }
    if (monotonic_cond_init(&runtime->work_changed) != 0) {
        pthread_cond_destroy(&runtime->command_removed);
        pthread_cond_destroy(&runtime->command_added);
        pthread_mutex_destroy(&runtime->mutex);
        return -1;
    }
    if (monotonic_cond_init(&runtime->queue_started) != 0) {
        pthread_cond_destroy(&runtime->work_changed);
        pthread_cond_destroy(&runtime->command_removed);
        pthread_cond_destroy(&runtime->command_added);
        pthread_mutex_destroy(&runtime->mutex);
        return -1;
    }

    return 0;
}

/**
 * @brief 전체 내부 스레드에 종료를 알리고 모든 대기자를 깨움.
 * @param runtime 종료 상태를 게시할 공유 runtime.
 * @return 반환값 없음.
 * @note command queue, work wait, Queue 시작 대기 중인 스레드가 모두 깨어남.
 */
static void runtime_stop(uploader_runtime_t *runtime)
{
    pthread_mutex_lock(&runtime->mutex);
    runtime->stopping = 1;
    pthread_cond_broadcast(&runtime->command_added);
    pthread_cond_broadcast(&runtime->command_removed);
    pthread_cond_broadcast(&runtime->work_changed);
    pthread_cond_broadcast(&runtime->queue_started);
    pthread_mutex_unlock(&runtime->mutex);
}

/**
 * @brief mutex로 보호된 종료 플래그를 읽음.
 * @param runtime 조회할 공유 runtime.
 * @return 종료 중이면 1, 계속 실행 중이면 0.
 */
static int runtime_is_stopping(uploader_runtime_t *runtime)
{
    int stopping;

    pthread_mutex_lock(&runtime->mutex);
    stopping = runtime->stopping;
    pthread_mutex_unlock(&runtime->mutex);
    return stopping;
}

/**
 * @brief Queue 결과 보고가 반복 실패한 치명 상황을 전체 스레드에 전파함.
 * @return 반환값 없음. runtime 종료 상태를 게시하고 메인 accept를 신호로 깨움.
 */
static void runtime_fail(uploader_runtime_t *runtime)
{
    pthread_mutex_lock(&runtime->mutex);
    runtime->fatal_error = 1;
    runtime->stopping = 1;
    pthread_cond_broadcast(&runtime->command_added);
    pthread_cond_broadcast(&runtime->command_removed);
    pthread_cond_broadcast(&runtime->work_changed);
    pthread_cond_broadcast(&runtime->queue_started);
    pthread_mutex_unlock(&runtime->mutex);

    /* Worker/Queue는 종료 신호를 차단하므로 프로세스 신호가 메인 accept를 깨움. */
    kill(getpid(), SIGTERM);
}

/** @brief 프로세스가 치명 오류로 종료 중인지 mutex로 보호하여 조회함. */
static int runtime_has_fatal_error(uploader_runtime_t *runtime)
{
    int fatal;
    pthread_mutex_lock(&runtime->mutex);
    fatal = runtime->fatal_error;
    pthread_mutex_unlock(&runtime->mutex);
    return fatal;
}

/**
 * @brief runtime이 소유한 pthread 자원을 파괴함.
 * @param runtime 정리할 공유 runtime.
 * @return 반환값 없음.
 * @pre Queue와 Worker 스레드를 모두 join한 뒤 호출해야 함.
 */
static void runtime_destroy(uploader_runtime_t *runtime)
{
    pthread_cond_destroy(&runtime->queue_started);
    pthread_cond_destroy(&runtime->work_changed);
    pthread_cond_destroy(&runtime->command_removed);
    pthread_cond_destroy(&runtime->command_added);
    pthread_mutex_destroy(&runtime->mutex);
}

/* =========================================================================
 * #### 동기 응답 객체
 *
 * Ingest/Worker가 Queue 명령을 보낸 뒤 busy-loop하지 않고 결과를 기다리게 함.
 * Queue 스레드는 결과 필드를 채우고 reply_finish()로 요청자를 깨움.
 * ========================================================================= */

/**
 * @brief 내부 명령 한 건의 동기 응답 객체를 초기화함.
 * @param reply 초기화할 응답 객체.
 * @return 성공 시 0, mutex/condition 초기화 실패 시 -1.
 */
static int reply_init(uploader_command_reply_t *reply)
{
    memset(reply, 0, sizeof(*reply));
    if (pthread_mutex_init(&reply->mutex, NULL) != 0) {
        return -1;
    }
    if (monotonic_cond_init(&reply->changed) != 0) {
        pthread_mutex_destroy(&reply->mutex);
        return -1;
    }
    return 0;
}

/**
 * @brief Queue 스레드가 응답 작성을 끝냈음을 게시함.
 * @param reply 완료 처리할 응답 객체.
 * @return 반환값 없음.
 * @note completed를 설정한 뒤 해당 요청 스레드 하나를 깨움.
 */
static void reply_finish(uploader_command_reply_t *reply)
{
    pthread_mutex_lock(&reply->mutex);
    reply->completed = 1;
    pthread_cond_signal(&reply->changed);
    pthread_mutex_unlock(&reply->mutex);
}

/**
 * @brief 요청 스레드가 Queue 처리 완료까지 조건변수에서 대기함.
 * @param reply 기다릴 응답 객체.
 * @return 반환값 없음.
 * @note spurious wake-up을 while 조건으로 방어함.
 */
static void reply_wait(uploader_command_reply_t *reply)
{
    pthread_mutex_lock(&reply->mutex);
    while (reply->completed == 0) {
        pthread_cond_wait(&reply->changed, &reply->mutex);
    }
    pthread_mutex_unlock(&reply->mutex);
}

/**
 * @brief 사용이 끝난 동기 응답의 pthread 자원을 해제함.
 * @param reply 정리할 응답 객체.
 * @return 반환값 없음.
 */
static void reply_destroy(uploader_command_reply_t *reply)
{
    pthread_cond_destroy(&reply->changed);
    pthread_mutex_destroy(&reply->mutex);
}

/* =========================================================================
 * #### 내부 생산자-소비자 명령 큐
 *
 * 외부 IPC가 아니라 프로세스 내부의 16칸 ring buffer임. mutex가 head/tail/count를
 * 보호하고 condition variable이 빈 큐 또는 가득 찬 큐에서 스레드를 재움.
 * ========================================================================= */

/**
 * 생산자가 내부 명령을 넣는 함수임. ring buffer가 가득 찬 경우 공간이 생길 때까지
 * 잠들므로 busy-loop와 무제한 메모리 증가가 발생하지 않음.
 */
static int runtime_push_command(
    uploader_runtime_t *runtime,
    const uploader_internal_command_t *command)
{
    pthread_mutex_lock(&runtime->mutex);

    while (runtime->command_count == UPLOADER_INTERNAL_COMMAND_CAPACITY &&
           runtime->stopping == 0) {
        pthread_cond_wait(&runtime->command_removed, &runtime->mutex);
    }

    if (runtime->stopping != 0) {
        pthread_mutex_unlock(&runtime->mutex);
        return -1;
    }

    runtime->commands[runtime->command_tail] = *command;
    runtime->command_tail =
        (runtime->command_tail + 1U) % UPLOADER_INTERNAL_COMMAND_CAPACITY;
    runtime->command_count++;

    pthread_cond_signal(&runtime->command_added);
    pthread_mutex_unlock(&runtime->mutex);
    return 0;
}

/** Queue 스레드만 호출하는 소비 함수임. 새 명령이 없으면 조건변수에서 잠듦. */
static int runtime_pop_command(
    uploader_runtime_t *runtime,
    uploader_internal_command_t *command)
{
    pthread_mutex_lock(&runtime->mutex);

    while (runtime->command_count == 0U && runtime->stopping == 0) {
        struct timespec deadline;
        int result;
        clock_gettime(CLOCK_MONOTONIC, &deadline);
        deadline.tv_sec++;
        /* An empty queue is a healthy wait. A blocked command handler never
         * reaches this renewal, so worker death cannot be hidden here. */
        uploader_watchdog_progress(UPLOADER_LOCAL_PROGRESS_MS);
        result = pthread_cond_timedwait(&runtime->command_added,
                                        &runtime->mutex, &deadline);
        if (result != 0 && result != ETIMEDOUT) {
            pthread_mutex_unlock(&runtime->mutex);
            return -1;
        }
    }

    if (runtime->command_count == 0U && runtime->stopping != 0) {
        pthread_mutex_unlock(&runtime->mutex);
        return -1;
    }

    *command = runtime->commands[runtime->command_head];
    runtime->command_head =
        (runtime->command_head + 1U) % UPLOADER_INTERNAL_COMMAND_CAPACITY;
    runtime->command_count--;

    pthread_cond_signal(&runtime->command_removed);
    pthread_mutex_unlock(&runtime->mutex);
    return 0;
}

/* 기능: 대기 중인 작업자에게 작업 상태 변경을 알린다.
 * 입력: 공유 runtime.
 * 출력·구조: 반환값 없음. mutex 안에서 세대 번호를 증가시키고 모든 조건 대기자를 깨워 변경을 재검사하게 한다.
 */
static void runtime_publish_work_change(uploader_runtime_t *runtime)
{
    pthread_mutex_lock(&runtime->mutex);
    runtime->work_generation++;
    pthread_cond_broadcast(&runtime->work_changed);
    pthread_mutex_unlock(&runtime->mutex);
}

/**
 * @brief Worker가 관찰할 현재 작업 변경 세대 번호를 안전하게 읽음.
 * @param runtime 조회할 공유 runtime.
 * @return 현재 work_generation 값.
 */
static uint64_t runtime_work_generation(uploader_runtime_t *runtime)
{
    uint64_t generation;

    pthread_mutex_lock(&runtime->mutex);
    generation = runtime->work_generation;
    pthread_mutex_unlock(&runtime->mutex);
    return generation;
}

/**
 * Worker가 할 일이 없을 때 사용하는 interruptible timed wait임. 기다리는 사이
 * Ingest가 새 작업을 넣으면 generation이 바뀌고 즉시 깨어남.
 */
static void runtime_wait_for_work(
    uploader_runtime_t *runtime,
    uint64_t observed_generation,
    uint32_t milliseconds)
{
    struct timespec deadline;

    /* Revisit idle work at least once per reconcile interval. */
    if (milliseconds == UINT32_MAX) milliseconds = UPLOADER_SPOOL_RECONCILE_MS;
    uploader_watchdog_progress(milliseconds + UPLOADER_LOCAL_PROGRESS_MS);

    if (clock_gettime(CLOCK_MONOTONIC, &deadline) != 0) {
        return;
    }

    deadline.tv_sec += (time_t)(milliseconds / 1000U);
    deadline.tv_nsec += (long)(milliseconds % 1000U) * 1000000L;
    if (deadline.tv_nsec >= 1000000000L) {
        deadline.tv_sec++;
        deadline.tv_nsec -= 1000000000L;
    }

    pthread_mutex_lock(&runtime->mutex);
    while (runtime->stopping == 0 &&
           runtime->work_generation == observed_generation) {
        int result = pthread_cond_timedwait(
            &runtime->work_changed,
            &runtime->mutex,
            &deadline);
        if (result == ETIMEDOUT) {
            break;
        }
    }
    pthread_mutex_unlock(&runtime->mutex);
}

/* =========================================================================
 * #### Queue 스레드 동기 호출 래퍼
 *
 * 호출자는 SQLite를 직접 만지지 않고 명령을 생산한 뒤 Queue의 응답만 받음.
 * ========================================================================= */

/**
 * @brief 명령을 ring buffer에 넣고 Queue 스레드의 처리 완료까지 기다림.
 * @param runtime 내부 명령 큐.
 * @param command 전송할 명령. 함수 안에서 reply 포인터가 연결됨.
 * @param reply Queue 결과를 받을 동기 응답 객체.
 * @return 호출 자체가 성립하면 0, 초기화 실패 또는 종료 중이면 -1.
 * @note 실제 DB 명령 성공 여부는 reply->code로 확인함.
 */
static int runtime_call(
    uploader_runtime_t *runtime,
    uploader_internal_command_t *command,
    uploader_command_reply_t *reply)
{
    if (reply_init(reply) != 0) {
        return -1;
    }

    /* Keep reply alive until Queue finishes. A stuck Queue is recovered by
     * process supervision, never by returning with a dangling stack pointer. */
    uploader_watchdog_progress(UPLOADER_LOCAL_PROGRESS_MS);
    command->reply = reply;
    if (runtime_push_command(runtime, command) != 0) {
        reply_destroy(reply);
        return -1;
    }

    reply_wait(reply);
    return 0;
}

/**
 * @brief runtime_call() 응답 객체의 자원을 정리함.
 * @param reply 사용이 끝난 응답 객체.
 * @return 반환값 없음.
 */
static void runtime_call_complete(uploader_command_reply_t *reply)
{
    reply_destroy(reply);
}

/**
 * @brief Ingest가 완성된 spool 경로를 Queue에 등록하도록 요청함.
 * @return DB 등록 성공 시 0, Queue 종료 또는 DB 실패 시 -1.
 * @param queue_id 등록되거나 기존에 존재하던 행 ID를 돌려받음.
 * @param inserted 새 행이면 1, 같은 작업의 멱등 재등록이면 0.
 */
static int queue_enqueue(
    uploader_runtime_t *runtime,
    const queue_enqueue_t *request,
    int64_t *queue_id,
    int *inserted,
    char *error,
    size_t error_size)
{
    uploader_internal_command_t command;
    uploader_command_reply_t reply;
    int result;

    memset(&command, 0, sizeof(command));
    command.type = UPLOADER_COMMAND_ENQUEUE;
    command.enqueue = *request;

    if (runtime_call(runtime, &command, &reply) != 0) {
        snprintf(error, error_size, "Queue thread is stopping");
        return -1;
    }

    result = reply.code;
    *queue_id = reply.queue_id;
    *inserted = reply.inserted;
    snprintf(error, error_size, "%s", reply.error);
    runtime_call_complete(&reply);
    return result;
}

/**
 * @brief Worker가 다음 전송 가능한 FIFO 작업을 요청함.
 * @return 요청 처리 성공 시 0, Queue 내부 호출 실패 시 -1.
 * @param found 작업이 있으면 1, 현재 처리 가능한 작업이 없으면 0.
 * @param retry_after_ms 작업이 없을 때 권장 대기 시간.
 * @param generation lost wake-up 방지를 위한 현재 작업 세대 번호.
 */
static int queue_request_job(
    uploader_runtime_t *runtime,
    queue_job_t *job,
    int *found,
    uint32_t *retry_after_ms,
    uint64_t *generation)
{
    uploader_internal_command_t command;
    uploader_command_reply_t reply;
    int result;

    memset(&command, 0, sizeof(command));
    command.type = UPLOADER_COMMAND_REQUEST_JOB;

    if (runtime_call(runtime, &command, &reply) != 0) {
        return -1;
    }

    result = reply.code;
    *job = reply.job;
    *found = reply.found;
    *retry_after_ms = reply.retry_after_ms;
    *generation = reply.work_generation;
    runtime_call_complete(&reply);
    return result;
}

/**
 * @brief 작업을 claim하지 않고 현재 처리 가능한 PEND 존재 여부를 Queue에 요청함.
 * @return Queue 조회 성공 시 0, 내부 호출 또는 DB 실패 시 -1.
 */
static int queue_peek_job(
    uploader_runtime_t *runtime,
    int *found,
    uint32_t *retry_after_ms,
    uint64_t *generation)
{
    uploader_internal_command_t command;
    uploader_command_reply_t reply;
    int result;

    memset(&command, 0, sizeof(command));
    command.type = UPLOADER_COMMAND_PEEK_JOB;
    if (runtime_call(runtime, &command, &reply) != 0) {
        return -1;
    }
    result = reply.code;
    *found = reply.found;
    *retry_after_ms = reply.retry_after_ms;
    *generation = reply.work_generation;
    runtime_call_complete(&reply);
    return result;
}

/**
 * @brief Worker 처리 결과를 DONE 또는 실패 전이 명령으로 Queue에 보고함.
 * @param success 0이 아니면 DONE, 0이면 실패 처리.
 * @param retriable 실패 작업을 다시 PEND로 보낼 수 있는지 여부.
 * @param verification_required 서버 저장 여부를 먼저 조회해야 하는지 여부.
 * @return Queue가 상태 전이를 완료하면 0, 실패하면 -1.
 */
static int queue_report_result(
    uploader_runtime_t *runtime,
    int64_t queue_id,
    int success,
    int retriable,
    int verification_required,
    const char *last_error)
{
    uint32_t attempt;

    for (attempt = 1U; attempt <= UPLOADER_RESULT_REPORT_ATTEMPTS; attempt++) {
        uploader_internal_command_t command;
        uploader_command_reply_t reply;
        int result = -1;

        memset(&command, 0, sizeof(command));
        command.type = success != 0
            ? UPLOADER_COMMAND_MARK_DONE
            : UPLOADER_COMMAND_MARK_FAILURE;
        command.queue_id = queue_id;
        command.retriable = retriable;
        command.verification_required = verification_required;
        snprintf(command.error, sizeof(command.error), "%s",
                 last_error != NULL ? last_error : "");

        if (runtime_call(runtime, &command, &reply) == 0) {
            result = reply.code;
            runtime_call_complete(&reply);
        }
        if (result == 0) {
            if (attempt > 1U) {
                uploader_log_event(
                    "INFO", "uploader", "QUEUE_RESULT_REPORT_RECOVERED",
                    "queue_id=%" PRId64 " report_attempt=%u",
                    queue_id, attempt);
            }
            return 0;
        }

        if (attempt < UPLOADER_RESULT_REPORT_ATTEMPTS &&
            runtime_is_stopping(runtime) == 0) {
            struct timespec delay;
            uint32_t delay_ms = UPLOADER_RESULT_REPORT_BASE_MS << (attempt - 1U);

            uploader_log_event(
                "WARN", "uploader", "QUEUE_RESULT_REPORT_RETRY",
                "queue_id=%" PRId64 " report_attempt=%u retry_after_ms=%u",
                queue_id, attempt, delay_ms);
            delay.tv_sec = delay_ms / 1000U;
            delay.tv_nsec = (long)(delay_ms % 1000U) * 1000000L;
            while (nanosleep(&delay, &delay) != 0 && errno == EINTR) {
            }
        }
    }

    uploader_log_event(
        "ERROR", "uploader", "QUEUE_RESULT_REPORT_FATAL",
        "queue_id=%" PRId64 " attempts=%u action=process_exit",
        queue_id, UPLOADER_RESULT_REPORT_ATTEMPTS);
    runtime_fail(runtime);
    return -1;
}

/**
 * @brief 외부 PAUSE/RESUME/QUERY 요청을 Queue 소유 제어 상태에 전달함.
 * @param state 호출 후 공개 IPC 응답 형식의 현재 상태가 기록됨.
 * @return 제어 명령 성공 시 0, 잘못된 요청 또는 Queue 실패 시 -1.
 */
static int queue_control_command(
    uploader_runtime_t *runtime,
    uploader_command_type_t type,
    uint32_t requester_pid,
    uint32_t lease_ms,
    const char *reason,
    uploader_control_state_t *state)
{
    uploader_internal_command_t command;
    uploader_command_reply_t reply;
    int result;

    memset(&command, 0, sizeof(command));
    command.type = type;
    command.requester_pid = requester_pid;
    command.lease_ms = lease_ms;
    snprintf(command.reason, sizeof(command.reason), "%s",
             reason != NULL ? reason : "");

    if (runtime_call(runtime, &command, &reply) != 0) {
        return -1;
    }

    result = reply.code;
    memset(state, 0, sizeof(*state));
    state->version = UPLOADER_PROTOCOL_VERSION;
    state->code = result;
    state->paused = reply.paused;
    state->remaining_ms = reply.remaining_ms;
    state->generation = reply.control_generation;
    snprintf(state->reason, sizeof(state->reason), "%s", reply.reason);
    runtime_call_complete(&reply);
    return result;
}

/* =========================================================================
 * #### Soft PAUSE lease와 Queue 시작 동기화
 * ========================================================================= */

/**
 * @brief CLOCK_MONOTONIC 기준으로 만료된 PAUSE lease를 자동 해제함.
 * @param control Queue 스레드만 소유하는 제어 상태.
 * @return 반환값 없음.
 */
static void control_refresh(uploader_control_state_internal_t *control)
{
    if (control->paused != 0 &&
        monotonic_time_ns() >= control->expires_at_ns) {
        control->paused = 0;
        control->expires_at_ns = 0U;
        control->requester_pid = 0U;
        control->reason[0] = '\0';
        control->generation++;
    }
}

/**
 * @brief 내부 제어 상태를 명령 응답 필드로 복사함.
 * @return 반환값 없음.
 * @note 남은 lease는 현재 monotonic 시간과 만료 시각의 차이로 계산함.
 */
static void control_copy_reply(
    const uploader_control_state_internal_t *control,
    uploader_command_reply_t *reply)
{
    uint64_t now = monotonic_time_ns();
    uint64_t remaining_ns =
        control->paused != 0 && control->expires_at_ns > now
            ? control->expires_at_ns - now
            : 0U;

    reply->paused = control->paused != 0 ? 1U : 0U;
    reply->remaining_ms = remaining_ns / 1000000ULL > UINT32_MAX
        ? UINT32_MAX
        : (uint32_t)(remaining_ns / 1000000ULL);
    reply->control_generation = control->generation;
    snprintf(reply->reason, sizeof(reply->reason), "%s", control->reason);
}

/**
 * @brief Queue 스레드의 DB 초기화 결과를 main 스레드에 게시함.
 * @return 반환값 없음.
 */
static void queue_set_start_result(
    uploader_runtime_t *runtime,
    int code,
    const char *error)
{
    pthread_mutex_lock(&runtime->mutex);
    runtime->queue_ready = 1;
    runtime->queue_start_code = code;
    snprintf(runtime->queue_start_error,
             sizeof(runtime->queue_start_error),
             "%s", error != NULL ? error : "");
    pthread_cond_broadcast(&runtime->queue_started);
    pthread_mutex_unlock(&runtime->mutex);
}

/**
 * @brief main이 Queue의 DB 초기화 완료까지 대기함.
 * @param error Queue 시작 실패 설명을 돌려받는 buffer.
 * @return Queue 시작 성공 시 0, DB open/recovery 실패 시 -1.
 */
static int queue_wait_until_started(
    uploader_runtime_t *runtime,
    char *error,
    size_t error_size)
{
    int code;

    pthread_mutex_lock(&runtime->mutex);
    while (runtime->queue_ready == 0 && runtime->stopping == 0) {
        pthread_cond_wait(&runtime->queue_started, &runtime->mutex);
    }
    code = runtime->queue_start_code;
    snprintf(error, error_size, "%s", runtime->queue_start_error);
    pthread_mutex_unlock(&runtime->mutex);
    return code;
}

/* =========================================================================
 * #### Queue 스레드와 SQLite 상태 전이
 * ========================================================================= */

/**
 * @brief 내부 명령 한 건을 SQLite 또는 PAUSE 상태에 적용하고 응답을 완료함.
 * @param runtime 작업 변경 알림에 사용하는 공유 runtime.
 * @param database Queue 스레드가 단독 소유하는 SQLite handle.
 * @param control Queue 스레드가 단독 소유하는 PAUSE 상태.
 * @param command 처리할 명령과 응답 객체 포인터.
 * @return 반환값 없음. 성공/실패는 command->reply에 기록함.
 */
static void queue_handle_command(
    uploader_runtime_t *runtime,
    queue_db_t *database,
    uploader_control_state_internal_t *control,
    uploader_internal_command_t *command)
{
    uploader_command_reply_t *reply = command->reply;
    char db_error[UPLOADER_ERROR_SIZE];
    int lease_recovered = 0;

    memset(db_error, 0, sizeof(db_error));
    control_refresh(control);

    if (queue_db_recover_expired_uploads(
            database,
            UPLOADER_LEASE_MS,
            &lease_recovered,
            db_error,
            sizeof(db_error)) != 0) {
        reply->code = -1;
        snprintf(reply->error, sizeof(reply->error), "%s", db_error);
        reply_finish(reply);
        return;
    }
    if (lease_recovered > 0) {
        runtime_publish_work_change(runtime);
        uploader_log_event(
            "WARN", "uploader", "UPLOAD_LEASE_RECOVERED",
            "recovered=%d status_before=UPLOAD status_after=PEND "
            "verification_required=true files_preserved=true",
            lease_recovered);
    }

    if (command->type == UPLOADER_COMMAND_ENQUEUE) {
        reply->code = queue_db_enqueue(
            database,
            &command->enqueue,
            &reply->queue_id,
            &reply->inserted,
            db_error,
            sizeof(db_error));
        if (reply->code == 0) {
            runtime_publish_work_change(runtime);
        }
    } else if (command->type == UPLOADER_COMMAND_PEEK_JOB) {
        if (control->paused != 0) {
            control_copy_reply(control, reply);
            reply->code = 0;
            reply->found = 0;
            reply->retry_after_ms = reply->remaining_ms > 0U
                ? reply->remaining_ms
                : UPLOADER_LOOP_MIN_WAIT_MS;
        } else {
            reply->code = queue_db_peek_ready(
                database,
                &reply->found,
                &reply->retry_after_ms,
                db_error,
                sizeof(db_error));
        }
        reply->work_generation = runtime_work_generation(runtime);
    } else if (command->type == UPLOADER_COMMAND_REQUEST_JOB) {
        if (control->paused != 0) {
            control_copy_reply(control, reply);
            reply->code = 0;
            reply->found = 0;
            reply->retry_after_ms = reply->remaining_ms > 0U
                ? reply->remaining_ms
                : UPLOADER_LOOP_MIN_WAIT_MS;
        } else {
            reply->code = queue_db_claim_next(
                database,
                &reply->job,
                &reply->found,
                &reply->retry_after_ms,
                db_error,
                sizeof(db_error));
        }
        reply->work_generation = runtime_work_generation(runtime);
        if (reply->code == 0 && reply->found != 0) {
            uploader_log_event(
                "INFO", "uploader", "QUEUE_JOB_CLAIMED",
                "queue_id=%" PRId64 " job_id=%s status=UPLOAD attempts=%u "
                "verification_required=%u",
                reply->job.queue_id,
                reply->job.job_id,
                reply->job.attempts,
                reply->job.verification_required);
        }
    } else if (command->type == UPLOADER_COMMAND_MARK_DONE) {
        reply->code = queue_db_mark_done(
            database,
            command->queue_id,
            db_error,
            sizeof(db_error));
        if (reply->code == 0) {
            runtime_publish_work_change(runtime);
            uploader_log_event(
                "INFO", "uploader", "QUEUE_JOB_DONE",
                "queue_id=%" PRId64 " status=DONE", command->queue_id);
        }
    } else if (command->type == UPLOADER_COMMAND_MARK_FAILURE) {
        reply->code = queue_db_mark_failure(
            database,
            command->queue_id,
            command->retriable,
            command->verification_required,
            command->error,
            &reply->transition,
            &reply->attempts,
            &reply->retry_after_ms,
            db_error,
            sizeof(db_error));
        if (reply->code == 0) {
            runtime_publish_work_change(runtime);
            uploader_log_event(
                reply->transition == QUEUE_FAILURE_RETRY ? "WARN" : "ERROR",
                "uploader",
                reply->transition == QUEUE_FAILURE_RETRY
                    ? "RETRY_SCHEDULED"
                    : "QUEUE_JOB_FAIL",
                "queue_id=%" PRId64 " attempts=%u retry_after_ms=%u "
                "logical_status=%s files_preserved=true error=%s",
                command->queue_id,
                reply->attempts,
                reply->retry_after_ms,
                reply->transition == QUEUE_FAILURE_RETRY
                    ? (command->verification_required != 0 ? "VERIFY" : "PEND")
                    : "FAIL",
                command->error);
        }
    } else if (command->type == UPLOADER_COMMAND_PAUSE) {
        if (command->lease_ms == 0U ||
            command->lease_ms > UPLOADER_PAUSE_MAX_LEASE_MS ||
            command->reason[0] == '\0') {
            reply->code = -1;
            snprintf(db_error, sizeof(db_error), "invalid pause request");
        } else {
            control->paused = 1;
            control->expires_at_ns = monotonic_time_ns() +
                (uint64_t)command->lease_ms * 1000000ULL;
            control->requester_pid = command->requester_pid;
            control->generation++;
            snprintf(control->reason, sizeof(control->reason),
                     "%s", command->reason);
            reply->code = 0;
            runtime_publish_work_change(runtime);
            uploader_log_event(
                "INFO", "uploader", "UPLOAD_PAUSE_REQUESTED",
                "requester_pid=%u lease_ms=%u reason=%s",
                command->requester_pid, command->lease_ms, command->reason);
        }
        control_copy_reply(control, reply);
    } else if (command->type == UPLOADER_COMMAND_RESUME) {
        control->paused = 0;
        control->expires_at_ns = 0U;
        control->requester_pid = 0U;
        control->generation++;
        control->reason[0] = '\0';
        reply->code = 0;
        control_copy_reply(control, reply);
        runtime_publish_work_change(runtime);
        uploader_log_event(
            "INFO", "uploader", "UPLOAD_RESUME_REQUESTED",
            "requester_pid=%u reason=%s",
            command->requester_pid, command->reason);
    } else if (command->type == UPLOADER_COMMAND_CONTROL_QUERY) {
        reply->code = 0;
        control_copy_reply(control, reply);
    } else {
        reply->code = -1;
        snprintf(db_error, sizeof(db_error), "unknown internal command");
    }

    snprintf(reply->error, sizeof(reply->error), "%s", db_error);
    reply_finish(reply);
}

/**
 * @brief Queue 스레드 진입점. DB open, 재시작 복구, 명령 직렬화를 담당함.
 * @param argument queue_thread_args_t 포인터.
 * @return pthread 규약에 따라 항상 NULL.
 * @note SQLite handle은 이 스레드 안에서 열고 닫으며 다른 스레드에 공개하지 않음.
 */
static void *queue_thread_main(void *argument)
{
    queue_thread_args_t *args = argument;
    queue_db_t database;
    uploader_internal_command_t command;
    uploader_control_state_internal_t control;
    char error[UPLOADER_ERROR_SIZE];
    int recovered = 0;

    watchdog_slot = WATCHDOG_QUEUE;
    uploader_watchdog_progress(UPLOADER_LOCAL_PROGRESS_MS);
    memset(&database, 0, sizeof(database));
    memset(&control, 0, sizeof(control));
    memset(error, 0, sizeof(error));

    if (queue_db_open(
            &database,
            args->database_path,
            error,
            sizeof(error)) != 0) {
        queue_set_start_result(args->runtime, -1, error);
        return NULL;
    }

    if (queue_db_recover_uploading(
            &database,
            &recovered,
            error,
            sizeof(error)) != 0) {
        queue_db_close(&database);
        queue_set_start_result(args->runtime, -1, error);
        return NULL;
    }

    uploader_log_event(
        "INFO", "uploader", "QUEUE_THREAD_READY",
        "database=%s recovered_uploads=%d sqlite_owner_thread=true",
        args->database_path, recovered);
    queue_set_start_result(args->runtime, 0, "");

    while (runtime_pop_command(args->runtime, &command) == 0) {
        uploader_watchdog_progress(UPLOADER_LOCAL_PROGRESS_MS);
        queue_handle_command(args->runtime, &database, &control, &command);
        uploader_watchdog_progress(UPLOADER_LOCAL_PROGRESS_MS);
    }

    queue_db_close(&database);
    uploader_log_event(
        "INFO", "uploader", "QUEUE_THREAD_STOPPED",
        "sqlite_closed=true");
    return NULL;
}

/* =========================================================================
 * #### Worker 오류 분류와 Queue 결과 보고
 * ========================================================================= */

/**
 * @brief HTTP 상태가 재시도 가능한 일시 오류인지 분류함.
 * @return 네트워크 무응답(0), 408/425/429 또는 5xx이면 1, 나머지는 0.
 */
static int retriable_http_failure(int http_status)
{
    return http_status == 0 || http_status == 200 || http_status == 201 ||
        http_status == 409 || http_status == 422 ||
        http_status == 408 ||
        http_status == 425 ||
        http_status == 429 ||
        (http_status >= 500 && http_status <= 599);
}

/* Transport 오류 문자열의 고정 prefix만 event 이름으로 노출한다. 비밀 값은 제외한다. */
static const char *transport_error_event(const char *error)
{
    static const char *const events[] = {
        "TCP_CONNECT_FAILED", "TLS_CA_LOAD_FAILED", "TLS_HANDSHAKE_FAILED",
        "TLS_CERT_VERIFY_FAILED", "TLS_HOST_VERIFY_FAILED",
        "TLS_WRITE_FAILED", "TLS_READ_FAILED"
    };
    size_t index;
    if (error == NULL) return NULL;
    for (index = 0; index < sizeof(events) / sizeof(events[0]); index++) {
        size_t length = strlen(events[index]);
        if (strlen(error) > length &&
            strncmp(error, events[index], length) == 0 &&
            error[length] == ':') return events[index];
    }
    return NULL;
}

/* 기능: 전송 이벤트의 로그 심각도를 결정한다.
 * 입력: 이벤트 이름 event(NULL 가능).
 * 출력·구조: CA·인증서·호스트 검증 실패는 ERROR, 나머지는 WARN 문자열. 인증 설정 실패를 일반 재시도 경고와 구분한다.
 */
static const char *transport_error_level(const char *event)
{
    return event != NULL &&
        (strcmp(event, "TLS_CA_LOAD_FAILED") == 0 ||
         strcmp(event, "TLS_CERT_VERIFY_FAILED") == 0 ||
         strcmp(event, "TLS_HOST_VERIFY_FAILED") == 0)
        ? "ERROR" : "WARN";
}

/**
 * @brief 영상 파일 자체의 영구 오류인지 현재 error prefix 규약으로 분류함.
 * @return stat/open/read/close/empty 계열이면 1, 아니면 0.
 * @note 장기적으로 문자열 대신 명시적 오류 enum으로 교체할 후보임.
 */
static int permanent_local_video_failure(const char *error)
{
    return error != NULL &&
        (strncmp(error, "stat video", 10U) == 0 ||
         strncmp(error, "video is empty", 14U) == 0 ||
         strncmp(error, "video is not a valid MP4", 24U) == 0);
}

/**
 * @brief 영상 앞부분에서 MP4의 필수 식별자인 ftyp box를 확인함.
 * @return 유효한 식별자를 찾으면 0, 명백한 손상 또는 일시 읽기 실패면 -1.
 * @note 실제 영구/일시 실패 분류는 오류 문자열을 받은 호출자가 수행함.
 */
static int worker_validate_mp4(
    const char *path,
    char *error,
    size_t error_size)
{
    unsigned char header[64];
    ssize_t length;
    size_t index;
    int descriptor = open(path, O_RDONLY);

    if (descriptor < 0) {
        snprintf(error, error_size, "open video: %s", strerror(errno));
        return -1;
    }
    do {
        length = read(descriptor, header, sizeof(header));
    } while (length < 0 && errno == EINTR);
    if (close(descriptor) != 0 || length < 8) {
        snprintf(error, error_size, "video read failed or header is too short");
        return -1;
    }

    for (index = 4U; index + 4U <= (size_t)length; index++) {
        if (memcmp(header + index, "ftyp", 4U) == 0) {
            return 0;
        }
    }
    snprintf(error, error_size, "video is not a valid MP4: ftyp box missing");
    return -1;
}

/**
 * @brief FastAPI health endpoint와 health JSON을 확인함.
 * @return 서버 사용 가능 시 0, 연결/HTTP/ACK 검증 실패 시 -1.
 * @param http_status 수신한 HTTP 상태 또는 연결 전 실패 시 0.
 */
static int worker_health_check(
    const char *server_url,
    int *http_status,
    char *error,
    size_t error_size)
{
    *http_status = 0;
    error[0] = '\0';

    if (http_health_check(
            server_url,
            http_status,
            error,
            error_size) != 0) {
        const char *transport_event = transport_error_event(error);
        if (transport_event != NULL) {
            uploader_log_event(transport_error_level(transport_event),
                "uploader", transport_event,
                "queue_claimed=false files_preserved=true error=%s", error);
        }
        uploader_log_event(
            "WARN", "uploader", "HEALTH_CHECK_FAILED",
            "server=%s http_status=%d queue_claimed=false "
            "files_preserved=true error=%s",
            server_url, *http_status, error);
        return -1;
    }

    uploader_log_event(
        "INFO", "uploader", "HEALTH_CHECK_OK",
        "server=%s http_status=%d", server_url, *http_status);
    return 0;
}

/**
 * @brief Worker 실패를 Queue의 PEND 재시도 또는 FAIL 전이로 보고함.
 * @return Queue 보고 성공 시 0, 내부 Queue 호출 실패 시 -1.
 */
static int worker_fail_job(
    uploader_runtime_t *runtime,
    const queue_job_t *job,
    const char *error,
    int retriable,
    int verification_required)
{
    if (queue_report_result(
            runtime,
            job->queue_id,
            0,
            retriable,
            verification_required,
            error) != 0) {
        uploader_log_event(
            "ERROR", "uploader", "QUEUE_RESULT_FAILED",
            "queue_id=%" PRId64 " result=FAIL", job->queue_id);
        return -1;
    }
    return 0;
}

/**
 * @brief 검증된 서버 저장 ACK를 받은 작업을 DONE으로 보고함.
 * @return Queue 상태 전이 성공 시 0, 실패 시 -1.
 */
static int worker_complete_job(
    uploader_runtime_t *runtime,
    const queue_job_t *job)
{
    if (queue_report_result(
            runtime,
            job->queue_id,
            1,
            0,
            0,
            "") != 0) {
        uploader_log_event(
            "ERROR", "uploader", "QUEUE_RESULT_FAILED",
            "queue_id=%" PRId64 " result=DONE", job->queue_id);
        return -1;
    }
    return 0;
}

static int recover_spool(
    uploader_runtime_t *runtime,
    const char *spool_root,
    uint64_t reserve_bytes);

/* =========================================================================
 * #### Worker 작업 처리 상태 머신
 * ========================================================================= */

/**
 * @brief Queue에서 받은 작업 한 건을 검증, 조회, 업로드, ACK 확인까지 처리함.
 * @param runtime 처리 결과를 Queue에 보고할 내부 runtime.
 * @param server_url FastAPI 기준 URL.
 * @param config 검증된 Node ID, Vehicle ID, API Key.
 * @param job Queue가 UPLOAD로 claim한 작업.
 * @return 결과를 Queue에 정상 보고했으면 0, Queue 보고 자체가 실패하면 -1.
 * @note HTTP 성공만으로 DONE 처리하지 않고 action/vehicle/SHA 저장 ACK까지 검증함.
 */
static int worker_process_job(
    uploader_runtime_t *runtime,
    const char *server_url,
    const worker_config_t *config,
    const queue_job_t *job)
{
    server_upload_ack_t server_ack;
    server_evidence_status_t evidence_status;
    char metadata[UPLOADER_METADATA_MAX_SIZE + 1U];
    char event_id[UPLOADER_EVENT_ID_SIZE];
    char metadata_vehicle_id[UPLOADER_VEHICLE_ID_SIZE];
    char video_sha256[SHA256_HEX_SIZE];
    char ack_body[1024];
    char status_body[2048];
    char error[UPLOADER_ERROR_SIZE];
    size_t metadata_size = 0U;
    uint64_t video_size = 0U;
    int http_status = 0;
    int retriable;
    int legacy_action_removed = 0;
    upload_phase_t upload_phase = UPLOADER_PHASE_NOT_CONNECTED;

    memset(&server_ack, 0, sizeof(server_ack));
    memset(&evidence_status, 0, sizeof(evidence_status));
    memset(metadata, 0, sizeof(metadata));
    memset(event_id, 0, sizeof(event_id));
    memset(metadata_vehicle_id, 0, sizeof(metadata_vehicle_id));
    memset(video_sha256, 0, sizeof(video_sha256));
    memset(ack_body, 0, sizeof(ack_body));
    memset(status_body, 0, sizeof(status_body));
    memset(error, 0, sizeof(error));

    if (metadata_load(
            job->metadata_path,
            metadata,
            sizeof(metadata),
            &metadata_size,
            error,
            sizeof(error)) != 0 ||
        metadata_prepare_http(
            metadata, &metadata_size, &legacy_action_removed,
            error, sizeof(error)) != 0 ||
        metadata_get_event_id(
            metadata,
            event_id,
            sizeof(event_id),
            error,
            sizeof(error)) != 0 ||
        metadata_get_vehicle_id(
            metadata,
            metadata_vehicle_id,
            sizeof(metadata_vehicle_id),
            error,
            sizeof(error)) != 0) {
        uploader_log_event(
            "ERROR", "uploader", "METADATA_INVALID",
            "queue_id=%" PRId64 " job_id=%s retriable=false "
            "files_preserved=true error=%s",
            job->queue_id, job->job_id, error);
        return worker_fail_job(runtime, job, error, 0, 0);
    }

    if (metadata_validate_vehicle_id(
            metadata_vehicle_id,
            config->vehicle_id,
            error,
            sizeof(error)) != 0) {
        uploader_log_event(
            "ERROR", "uploader", "METADATA_IDENTITY_MISMATCH",
            "queue_id=%" PRId64 " event_id=%s vehicle_id=%s "
            "configured_vehicle_id=%s files_preserved=true error=%s",
            job->queue_id,
            event_id,
            metadata_vehicle_id,
            config->vehicle_id,
            error);
        return worker_fail_job(runtime, job, error, 0, 0);
    }

    if (worker_validate_mp4(
            job->video_path,
            error,
            sizeof(error)) != 0 ||
        sha256_file(
            job->video_path,
            video_sha256,
            &video_size,
            error,
            sizeof(error)) != 0) {
        uploader_log_event(
            "ERROR", "uploader", "VIDEO_INVALID",
            "queue_id=%" PRId64 " job_id=%s files_preserved=true error=%s",
            job->queue_id, job->job_id, error);
        return worker_fail_job(runtime, job, error, 0, 0);
    }

    uploader_log_event(
        "INFO", "uploader", "VIDEO_SHA256_COMPLETE",
        "queue_id=%" PRId64 " event_id=%s video_bytes=%" PRIu64
        " sha256_prefix=%.12s",
        job->queue_id, event_id, video_size, video_sha256);

    /* ACK가 유실된 작업은 MP4를 다시 보내기 전에 서버 저장 상태를 먼저 조회함. */
    if (job->verification_required != 0U) {
        int status_valid = 0;
        recovery_action_t action;

        uploader_log_event(
            "INFO", "uploader", "EVIDENCE_STATUS_QUERY_START",
            "queue_id=%" PRId64 " event_id=%s sha256_prefix=%.12s",
            job->queue_id, event_id, video_sha256);

        if (http_query_evidence_status(
                server_url,
                event_id,
                config->node_id,
                config->api_key,
                video_sha256,
                &http_status,
                status_body,
                sizeof(status_body),
                error,
                sizeof(error)) == 0) {
            status_valid = http_status == 200 &&
                server_evidence_status_validate(
                    status_body,
                    event_id,
                    config->vehicle_id,
                    video_sha256,
                    &evidence_status,
                    error,
                    sizeof(error)) == 0;
        }

        action = decide_recovery_action(http_status, status_valid);

        if (transport_error_event(error) != NULL) {
            uploader_log_event(transport_error_level(transport_error_event(error)),
                "uploader", transport_error_event(error),
                "queue_id=%" PRId64 " files_preserved=true error=%s",
                job->queue_id, error);
        }

        uploader_log_event(
            action == RECOVERY_RETRY_LATER ? "WARN" : "INFO",
            "uploader", "EVIDENCE_STATUS_QUERY_RESULT",
            "queue_id=%" PRId64 " event_id=%s http_status=%d "
            "response_valid=%s action=%d files_preserved=true",
            job->queue_id,
            event_id,
            http_status,
            status_valid != 0 ? "true" : "false",
            (int)action);

        if (action == RECOVERY_MARK_DONE) {
            if (worker_complete_job(runtime, job) != 0) {
                return -1;
            }
            uploader_log_event(
                "INFO", "uploader", "UPLOAD_RECOVERED_FROM_SERVER_ACK",
                "queue_id=%" PRId64 " event_id=%s reuploaded=false status=DONE",
                job->queue_id, event_id);
            return 0;
        }

        if (action == RECOVERY_RETRY_LATER) {
            if (error[0] == '\0') {
                snprintf(error, sizeof(error),
                         "evidence status temporarily unavailable: HTTP %d",
                         http_status);
            }
            return worker_fail_job(runtime, job, error, 1, 1);
        }

        if (action == RECOVERY_PERMANENT_FAILURE) {
            if (error[0] == '\0') {
                snprintf(error, sizeof(error),
                         "evidence status rejected: HTTP %d", http_status);
            }
            return worker_fail_job(runtime, job, error, 0, 0);
        }

        uploader_log_event(
            "INFO", "uploader", "EVIDENCE_STATUS_NOT_FOUND",
            "queue_id=%" PRId64 " event_id=%s action=UPLOAD_REQUIRED",
            job->queue_id, event_id);
    }

    uploader_log_event(
        "INFO", "uploader", "UPLOAD_START",
        "queue_id=%" PRId64 " job_id=%s event_id=%s attempts=%u "
        "metadata_bytes=%zu video=%s",
        job->queue_id,
        job->job_id,
        event_id,
        job->attempts,
        metadata_size,
        job->video_path);
    if (job->attempts > 1U) {
        uploader_log_event(
            "INFO", "uploader", "UPLOAD_RETRY_STARTED",
            "queue_id=%" PRId64 " job_id=%s attempts=%u files_preserved=true",
            job->queue_id, job->job_id, job->attempts);
    }

    http_status = 0;
    error[0] = '\0';
    if (http_upload_evidence(
            server_url,
            event_id,
            metadata,
            metadata_size,
            job->video_path,
            config->node_id,
            config->api_key,
            video_sha256,
            &upload_phase,
            &http_status,
            ack_body,
            sizeof(ack_body),
            error,
            sizeof(error)) != 0) {
        if (transport_error_event(error) != NULL) {
            uploader_log_event(transport_error_level(transport_error_event(error)),
                "uploader", transport_error_event(error),
                "queue_id=%" PRId64 " files_preserved=true error=%s",
                job->queue_id, error);
        }
        retriable = permanent_local_video_failure(error) != 0
            ? 0
            : retriable_http_failure(http_status);

        uploader_log_event(
            retriable != 0 ? "WARN" : "ERROR",
            "uploader",
            retriable != 0
                ? "UPLOAD_RETRYABLE_FAILURE"
                : "UPLOAD_PERMANENT_FAILURE",
            "queue_id=%" PRId64 " event_id=%s http_status=%d "
            "retriable=%s files_preserved=true error=%s",
            job->queue_id,
            event_id,
            http_status,
            retriable != 0 ? "true" : "false",
            error);
        if (retriable != 0 && http_status == 0) {
            uploader_log_event(
                "WARN", "uploader", "UPLOAD_CONNECTION_LOST",
                "queue_id=%" PRId64 " event_id=%s phase=%d "
                "verification_required=%s files_preserved=true",
                job->queue_id,
                event_id,
                (int)upload_phase,
                upload_phase >= UPLOADER_PHASE_BODY_SENDING
                    ? "true"
                    : "false");
        }

        return worker_fail_job(
            runtime,
            job,
            error,
            retriable,
            retriable != 0 &&
                (http_status == 409 || http_status == 200 || http_status == 201 ||
                 (http_status == 0 && upload_phase >= UPLOADER_PHASE_BODY_SENDING)));
    }

    if (server_upload_ack_validate(
            ack_body,
            event_id,
            config->vehicle_id,
            video_sha256,
            &server_ack,
            error,
            sizeof(error)) != 0) {
        uploader_log_event(
            "WARN", "uploader", "UPLOAD_ACK_UNCERTAIN",
            "queue_id=%" PRId64 " event_id=%s http_status=%d "
            "verification_required=true files_preserved=true error=%s",
            job->queue_id, event_id, http_status, error);
        return worker_fail_job(runtime, job, error, 1, 1);
    }

    uploader_log_event(
        "INFO", "uploader", "UPLOAD_SERVER_ACK",
        "queue_id=%" PRId64 " event_id=%s http_status=%d "
        "storage_ack_confirmed=true status=%s sha256_prefix=%.12s "
        "idempotent_replay=%s",
        job->queue_id,
        event_id,
        http_status,
        server_ack.upload_status,
        server_ack.sha256,
        server_ack.idempotent_replay != 0 ? "true" : "false");

    if (worker_complete_job(runtime, job) != 0) {
        return -1;
    }

    uploader_log_event(
        "INFO", "uploader", "UPLOAD_COMPLETE",
        "queue_id=%" PRId64 " job_id=%s status=DONE "
        "local_files_preserved=true",
        job->queue_id, job->job_id);
    return 0;
}

/**
 * @brief Worker 스레드 진입점. 작업이 없으면 조건변수에서 대기함.
 * @param argument worker_thread_args_t 포인터.
 * @return pthread 규약에 따라 항상 NULL.
 * @note 작업이 있을 때만 health와 HTTP를 수행하여 idle polling을 줄임.
 */
static void *worker_thread_main(void *argument)
{
    worker_thread_args_t *args = argument;
    const char *ca_file = getenv("UPLOADER_CA_FILE");
    char tls_error[UPLOADER_ERROR_SIZE];
    uint64_t next_reconcile_ns = monotonic_time_ns() +
        (uint64_t)UPLOADER_SPOOL_RECONCILE_MS * 1000000ULL;

    watchdog_slot = WATCHDOG_WORKER;
    uploader_watchdog_progress(UPLOADER_LOCAL_PROGRESS_MS);
    if (ca_file == NULL || ca_file[0] == '\0') {
        ca_file = UPLOADER_DEFAULT_CA_FILE;
    }
    memset(tls_error, 0, sizeof(tls_error));
    if (http_client_prepare(args->server_url, ca_file,
                            tls_error, sizeof(tls_error)) != 0) {
        uploader_log_event("ERROR", "uploader", "TLS_TRANSPORT_DEGRADED",
            "queue_claimed=false files_preserved=true error=%s", tls_error);
    }

    uploader_log_event(
        "INFO", "uploader", "WORKER_THREAD_READY",
        "server=%s node_id=%s vehicle_id=%s http_owner_thread=true",
        args->server_url,
        args->config->node_id,
        args->config->vehicle_id);

    while (runtime_is_stopping(args->runtime) == 0) {
        queue_job_t job;
        char error[UPLOADER_ERROR_SIZE];
        uint32_t retry_after_ms = UPLOADER_NO_JOB_RETRY_MS;
        /* Queue 요청 자체가 실패해도 대기 조건을 비교할 수 있도록 현재 세대값으로
         * 먼저 초기화함. 성공한 요청에서는 Queue 응답값으로 다시 갱신됨. */
        uint64_t generation = runtime_work_generation(args->runtime);
        int http_status;
        int found = 0;

        uploader_watchdog_progress(UPLOADER_LOCAL_PROGRESS_MS);
        memset(&job, 0, sizeof(job));
        memset(error, 0, sizeof(error));

        /*
         * 완성 spool은 영속 기록이므로 주기적으로 DB와 대조한다. 실행 중 enqueue가
         * 잠깐 실패해도 별도 스레드/무제한 RAM 목록 없이 같은 job_id를 재등록함.
         */
        if (monotonic_time_ns() >= next_reconcile_ns) {
            (void)recover_spool(
                args->runtime, args->spool_root, args->reserve_bytes);
            next_reconcile_ns = monotonic_time_ns() +
                (uint64_t)UPLOADER_SPOOL_RECONCILE_MS * 1000000ULL;
        }

        /* PEND 존재 여부만 확인하며 아직 UPLOAD 전이/attempt 증가를 하지 않음. */
        if (queue_peek_job(
                args->runtime,
                &found,
                &retry_after_ms,
                &generation) != 0) {
            if (runtime_is_stopping(args->runtime) == 0) {
                runtime_wait_for_work(
                    args->runtime,
                    generation,
                    UPLOADER_HEALTH_RETRY_MS);
            }
            continue;
        }

        if (found == 0) {
            runtime_wait_for_work(
                args->runtime,
                generation,
                retry_after_ms == UINT_MAX ||
                    retry_after_ms > UPLOADER_SPOOL_RECONCILE_MS
                    ? UPLOADER_SPOOL_RECONCILE_MS
                    : (retry_after_ms > 0U
                        ? retry_after_ms
                        : UPLOADER_NO_JOB_RETRY_MS));
            continue;
        }

        /* TLS 설정 실패는 Worker만 보류한다. Ingest/Queue와 PEND는 계속 유지한다. */
        if (http_client_prepare(args->server_url, ca_file,
                                error, sizeof(error)) != 0) {
            uploader_log_event("ERROR", "uploader", "TLS_TRANSPORT_DEGRADED",
                "queue_claimed=false files_preserved=true error=%s", error);
            generation = runtime_work_generation(args->runtime);
            runtime_wait_for_work(args->runtime, generation, UPLOADER_RETRY_MAX_MS);
            continue;
        }

        /* 작업이 있을 때만 Health를 호출하며 실패 시 Queue 상태와 attempts는 불변임. */
        if (worker_health_check(
                args->server_url,
                &http_status,
                error,
                sizeof(error)) != 0) {
            generation = runtime_work_generation(args->runtime);
            runtime_wait_for_work(
                args->runtime,
                generation,
                strncmp(error, "TLS_CERT_VERIFY_FAILED", 22U) == 0 ||
                strncmp(error, "TLS_HOST_VERIFY_FAILED", 22U) == 0
                    ? UPLOADER_RETRY_MAX_MS : UPLOADER_HEALTH_RETRY_MS);
            continue;
        }

        /* Health 성공 후에만 원자적으로 PEND를 UPLOAD로 claim함. */
        found = 0;
        if (queue_request_job(
                args->runtime,
                &job,
                &found,
                &retry_after_ms,
                &generation) != 0) {
            if (runtime_is_stopping(args->runtime) == 0) {
                runtime_wait_for_work(
                    args->runtime, generation, UPLOADER_LOOP_MIN_WAIT_MS);
            }
            continue;
        }
        if (found == 0) {
            continue;
        }

        if (worker_process_job(
                args->runtime,
                args->server_url,
                args->config,
                &job) != 0) {
            /* 결과 보고 실패는 queue_report_result가 fatal로 전환했으므로 무시하지 않음. */
            break;
        }
    }

    http_client_cleanup();
    uploader_log_event(
        "INFO", "uploader", "WORKER_THREAD_STOPPED",
        "http_closed=true");
    return NULL;
}

/* =========================================================================
 * #### Ingest 설정과 CAMERA_READY 입력 검증
 * ========================================================================= */

/**
 * @brief spool 복사 후 남겨야 할 최소 디스크 여유량을 읽음.
 * @return 유효한 UPLOADER_MIN_FREE_BYTES 또는 기본 512MiB.
 */
static uint64_t configured_minimum_free_bytes(void)
{
    const char *value = getenv("UPLOADER_MIN_FREE_BYTES");
    char *end = NULL;
    unsigned long long parsed;

    if (value == NULL || value[0] == '\0') {
        return UPLOADER_DEFAULT_MIN_FREE_BYTES;
    }

    errno = 0;
    parsed = strtoull(value, &end, 10);
    if (errno != 0 || end == value || *end != '\0') {
        return UPLOADER_DEFAULT_MIN_FREE_BYTES;
    }
    return (uint64_t)parsed;
}

/**
 * @brief 비어 있지 않은 환경변수 또는 기본값을 선택함.
 * @return getenv가 소유한 문자열 또는 전달받은 기본값 포인터.
 */
static const char *configured_value(
    const char *name,
    const char *default_value)
{
    const char *value = getenv(name);
    return value != NULL && value[0] != '\0' ? value : default_value;
}

/**
 * @brief 경로 환경변수를 읽거나 실행 계정 HOME 기준 기본 경로를 생성함.
 * @param name 우선해서 읽을 환경변수 이름.
 * @param relative_path HOME 뒤에 붙일 기본 상대 경로.
 * @param destination 최종 절대 경로를 받을 버퍼.
 * @return 유효한 절대 경로를 만들면 0, HOME 누락·길이 초과 시 -1.
 *
 * 특정 개발 계정(/home/eclipse 등)을 실행 파일에 넣지 않는다. systemd에서도
 * User=로 지정한 계정의 HOME을 Environment=HOME=...으로 전달하므로 같은 규칙을
 * 사용한다. 운영자가 UPLOADER_* 경로를 지정하면 그 값을 그대로 우선한다.
 */
static int resolve_upload_path(
    const char *name,
    const char *relative_path,
    char *destination,
    size_t destination_size,
    char *error,
    size_t error_size)
{
    const char *configured = getenv(name);
    const char *home;
    int written;

    if (configured != NULL && configured[0] != '\0') {
        written = snprintf(destination, destination_size, "%s", configured);
    } else {
        home = getenv("HOME");
        if (home == NULL || home[0] != '/') {
            snprintf(error, error_size,
                     "%s is not set and HOME is unavailable", name);
            return -1;
        }
        written = snprintf(destination, destination_size,
                           "%s/%s", home, relative_path);
    }

    if (written < 0 || (size_t)written >= destination_size ||
        destination[0] != '/') {
        snprintf(error, error_size, "%s path is invalid or too long", name);
        return -1;
    }
    return 0;
}

/**
 * @brief 외부 식별자의 NUL 종료, 길이, 허용 문자를 검사함.
 * @return 영문/숫자/밑줄/하이픈으로 구성되면 1, 아니면 0.
 */
static int valid_identifier(const char *value, size_t capacity)
{
    size_t index;
    size_t length = strnlen(value, capacity);

    if (length == 0U || length == capacity) {
        return 0;
    }
    for (index = 0U; index < length; index++) {
        unsigned char character = (unsigned char)value[index];
        if (!((character >= 'A' && character <= 'Z') ||
              (character >= 'a' && character <= 'z') ||
              (character >= '0' && character <= '9') ||
              character == '_' || character == '-')) {
            return 0;
        }
    }
    return 1;
}

/**
 * @brief Camera가 아직 작성 중일 수 있는 .part 경로인지 검사함.
 * @return .part로 끝나면 1, 아니면 0.
 */
static int has_part_suffix(const char *path)
{
    size_t length = strlen(path);
    return length >= 5U && strcmp(path + length - 5U, ".part") == 0;
}

/** 외부 IPC payload와 최종 MP4 조건을 spool 복사 전에 검사함. */
static int validate_ready_payload(
    const uploader_evidence_ready_t *ready,
    const char *configured_vehicle_id,
    char *error,
    size_t error_size)
{
    struct stat information;

    if (ready->version != UPLOADER_PROTOCOL_VERSION ||
        !valid_identifier(ready->action_id, sizeof(ready->action_id)) ||
        !valid_identifier(
            ready->accident_vehicle_id,
            sizeof(ready->accident_vehicle_id)) ||
        !valid_identifier(ready->vehicle_id, sizeof(ready->vehicle_id)) ||
        strnlen(ready->video_path, sizeof(ready->video_path)) ==
            sizeof(ready->video_path)) {
        snprintf(error, error_size, "invalid CAMERA_READY identity or path");
        return -1;
    }

    if (strcmp(configured_vehicle_id, ready->vehicle_id) != 0) {
        snprintf(error, error_size,
                 "CAMERA_READY vehicle_id does not match UPLOADER_VEHICLE_ID");
        return -1;
    }

    if (ready->event_time_ns <= 0 || ready->clip_start_time_ns <= 0 ||
        ready->clip_end_time_ns <= 0 ||
        ready->clip_start_time_ns > ready->event_time_ns ||
        ready->event_time_ns > ready->clip_end_time_ns ||
        !isfinite(ready->chrony_offset_ms)) {
        snprintf(error, error_size, "invalid CAMERA_READY timestamp range");
        return -1;
    }

    if (strnlen(
            ready->time_sync_status,
            sizeof(ready->time_sync_status)) ==
            sizeof(ready->time_sync_status) ||
        (strcmp(ready->time_sync_status, "SYNCED") != 0 &&
         strcmp(ready->time_sync_status, "HOLDOVER") != 0 &&
         strcmp(ready->time_sync_status, "UNRELIABLE") != 0 &&
         strcmp(ready->time_sync_status, "UNKNOWN") != 0 &&
         /* 기존 시험 데이터와의 호환만 유지함. 신규 코드는 UNSYNCED를 만들지 않음. */
         strcmp(ready->time_sync_status, "UNSYNCED") != 0)) {
        snprintf(error, error_size, "invalid time_sync_status");
        return -1;
    }

    if (ready->video_path[0] == '\0' ||
        has_part_suffix(ready->video_path) ||
        stat(ready->video_path, &information) != 0 ||
        !S_ISREG(information.st_mode) || information.st_size <= 0) {
        snprintf(error, error_size,
                 "video is missing, empty, non-regular, or temporary");
        return -1;
    }

    return 0;
}

/**
 * @brief CAMERA_READY를 서버 계약의 metadata.json 임시 파일로 직렬화함.
 * @param path 성공 시 fsync가 끝난 임시 파일 경로를 돌려받음.
 * @return 성공 시 0, 생성/쓰기/fsync/길이 실패 시 -1.
 * @note 호출자는 사용 후 임시 파일을 unlink해야 함.
 */
static int write_metadata_file(
    const uploader_evidence_ready_t *ready,
    const char *event_id,
    char *path,
    size_t path_size,
    char *error,
    size_t error_size)
{
    char metadata[2048];
    char temporary[] = "/tmp/uploader-metadata-XXXXXX";
    size_t used = 0U;
    size_t total;
    int descriptor;
    int length;

    length = snprintf(
        metadata,
        sizeof(metadata),
        "{\n"
        "  \"event_id\":\"%s\",\n"
        "  \"accident_vehicle_id\":\"%s\",\n"
        "  \"vehicle_id\":\"%s\",\n"
        "  \"event_time_ns\":%" PRId64 ",\n"
        "  \"clip_start_time_ns\":%" PRId64 ",\n"
        "  \"clip_end_time_ns\":%" PRId64 ",\n"
        "  \"time_sync_status\":\"%s\",\n"
        "  \"chrony_offset_ms\":%.3f\n"
        "}\n",
        event_id,
        ready->accident_vehicle_id,
        ready->vehicle_id,
        ready->event_time_ns,
        ready->clip_start_time_ns,
        ready->clip_end_time_ns,
        ready->time_sync_status,
        ready->chrony_offset_ms);

    if (length < 0 || (size_t)length >= sizeof(metadata)) {
        snprintf(error, error_size, "metadata exceeds fixed buffer");
        return -1;
    }

    descriptor = mkstemp(temporary);
    if (descriptor < 0) {
        snprintf(error, error_size, "create metadata: %s", strerror(errno));
        return -1;
    }

    total = (size_t)length;
    while (used < total) {
        ssize_t written = write(descriptor, metadata + used, total - used);
        if (written < 0 && errno == EINTR) {
            continue;
        }
        if (written <= 0) {
            snprintf(error, error_size, "write metadata: %s", strerror(errno));
            close(descriptor);
            unlink(temporary);
            return -1;
        }
        used += (size_t)written;
    }

    if (fsync(descriptor) != 0 || close(descriptor) != 0) {
        snprintf(error, error_size, "sync metadata: %s", strerror(errno));
        unlink(temporary);
        return -1;
    }

    if (snprintf(path, path_size, "%s", temporary) >= (int)path_size) {
        unlink(temporary);
        snprintf(error, error_size, "metadata path exceeds limit");
        return -1;
    }
    return 0;
}

/**
 * @brief MP4와 metadata의 원자적 spool commit 후 Queue 등록까지 수행함.
 * @return 두 단계가 모두 성공하면 0, 하나라도 실패하면 -1.
 * @note Queue 등록 실패 시에도 이미 commit된 spool 파일은 복구를 위해 보존함.
 */
static int submit_spool_job(
    uploader_runtime_t *runtime,
    const char *spool_root,
    const char *job_id,
    const char *source_video,
    const char *source_metadata,
    uint64_t reserve_bytes,
    int recovery_mode,
    char *error,
    size_t error_size)
{
    spool_result_t spool_result;
    queue_enqueue_t enqueue;
    int64_t queue_id = 0;
    int inserted = 0;

    memset(&spool_result, 0, sizeof(spool_result));
    memset(&enqueue, 0, sizeof(enqueue));

    if (spool_commit(
            spool_root,
            job_id,
            source_video,
            source_metadata,
            reserve_bytes,
            &spool_result,
            error,
            error_size) != 0) {
        if (spool_result.cleanup_performed != 0) {
            uploader_log_event(
                spool_result.cleanup_succeeded != 0 ? "INFO" : "ERROR",
                "uploader",
                spool_result.cleanup_succeeded != 0
                    ? "SPOOL_TEMP_CLEANUP_COMPLETE"
                    : "SPOOL_TEMP_CLEANUP_FAILED",
                "job_id=%s published=false", job_id);
        }
        uploader_log_event(
            "ERROR", "uploader",
            strstr(error, "insufficient spool space") != NULL
                ? "LOW_SPACE_REJECTED"
                : "SPOOL_COMMIT_FAILED",
            "job_id=%s source_preserved=true error=%s", job_id, error);
        switch (spool_result.failure) {
        case SPOOL_FAILURE_INVALID_INPUT:
            return UPLOADER_RESPONSE_INVALID_REQUEST;
        case SPOOL_FAILURE_SOURCE_NOT_READY:
            return UPLOADER_RESPONSE_SOURCE_NOT_READY;
        case SPOOL_FAILURE_INSUFFICIENT_SPACE:
            return UPLOADER_RESPONSE_INSUFFICIENT_SPACE;
        default:
            return UPLOADER_RESPONSE_STORAGE_TEMPORARY_ERROR;
        }
    }

    enqueue.version = UPLOADER_PROTOCOL_VERSION;
    snprintf(enqueue.job_id, sizeof(enqueue.job_id), "%s", job_id);
    snprintf(enqueue.video_path, sizeof(enqueue.video_path),
             "%s", spool_result.video_path);
    snprintf(enqueue.metadata_path, sizeof(enqueue.metadata_path),
             "%s", spool_result.metadata_path);

    if (queue_enqueue(
            runtime,
            &enqueue,
            &queue_id,
            &inserted,
            error,
            error_size) != 0) {
        if (recovery_mode != 0) {
            uploader_log_event(
                "INFO", "uploader", "QUEUE_ENQUEUE_RETRY",
                "job_id=%s retry_source=spool_reconcile "
                "spool_committed=true files_preserved=true",
                job_id);
        }
        uploader_log_event(
            "WARN", "uploader",
            recovery_mode != 0
                ? "QUEUE_ENQUEUE_RETRY_FAILED"
                : "QUEUE_ENQUEUE_DEFERRED",
            "job_id=%s retry_after_ms=%u spool_committed=true "
            "files_preserved=true error=%s",
            job_id, UPLOADER_SPOOL_RECONCILE_MS, error);
        return UPLOADER_RESPONSE_QUEUE_DEFERRED;
    }

    uploader_log_event(
        "INFO", "uploader", "SPOOL_COMMIT_COMPLETE",
        "job_id=%s bytes=%" PRIu64 " already_present=%d video=%s metadata=%s",
        job_id,
        spool_result.source_bytes,
        spool_result.already_present,
        spool_result.video_path,
        spool_result.metadata_path);
    uploader_log_event(
        "INFO", "uploader", "QUEUE_ENQUEUE_ACK",
        "job_id=%s queue_id=%" PRId64 " inserted=%d status=PEND",
        job_id, queue_id, inserted);
    if (recovery_mode != 0 && inserted != 0) {
        uploader_log_event(
            "INFO", "uploader", "QUEUE_ENQUEUE_RETRY",
            "job_id=%s retry_source=spool_reconcile "
            "spool_committed=true files_preserved=true",
            job_id);
        uploader_log_event(
            "INFO", "uploader", "QUEUE_ENQUEUE_RECOVERED",
            "job_id=%s queue_id=%" PRId64 " status_after=PEND "
            "files_preserved=true",
            job_id, queue_id);
    }
    return 0;
}

/**
 * @brief CAMERA_READY 한 건을 검증하여 결정적 job ID로 영속화하고 큐에 넣음.
 * @return 안전한 spool과 Queue 등록 완료 시 0, 입력/파일/저장 실패 시 -1.
 * @note job ID는 action ID와 V2V 원본 사고 시각으로 구성됨.
 * 동일 사고의 재입력은 멱등 처리하고 action_id가 재사용된 새 사고는 구분함.
 */
static int process_ready_payload(
    uploader_runtime_t *runtime,
    const uploader_evidence_ready_t *ready,
    const char *configured_vehicle_id,
    const char *spool_root,
    uint64_t reserve_bytes,
    char *error,
    size_t error_size)
{
    char sha256[SHA256_HEX_SIZE];
    char job_id[UPLOADER_JOB_ID_SIZE];
    char event_id[UPLOADER_EVENT_ID_SIZE];
    char metadata_path[UPLOADER_PATH_SIZE];
    uint64_t video_bytes = 0U;
    int result;

    memset(sha256, 0, sizeof(sha256));
    memset(job_id, 0, sizeof(job_id));
    memset(event_id, 0, sizeof(event_id));
    memset(metadata_path, 0, sizeof(metadata_path));

    if (validate_ready_payload(
            ready,
            configured_vehicle_id,
            error,
            error_size) != 0) {
        return UPLOADER_RESPONSE_INVALID_REQUEST;
    }
    if (sha256_file(
            ready->video_path,
            sha256,
            &video_bytes,
            error,
            error_size) != 0) {
        return UPLOADER_RESPONSE_SOURCE_NOT_READY;
    }

    if (event_id_build(
            event_id,
            sizeof(event_id),
            ready->event_time_ns,
            ready->accident_vehicle_id,
            error,
            error_size) != 0) {
        return UPLOADER_RESPONSE_INVALID_REQUEST;
    }

    if (snprintf(
            job_id,
            sizeof(job_id),
            "%s-%" PRId64,
            ready->action_id,
            ready->event_time_ns) >= (int)sizeof(job_id)) {
        snprintf(error, error_size, "deterministic job_id exceeds limit");
        return UPLOADER_RESPONSE_INVALID_REQUEST;
    }

    if (write_metadata_file(
            ready,
            event_id,
            metadata_path,
            sizeof(metadata_path),
            error,
            error_size) != 0) {
        return UPLOADER_RESPONSE_STORAGE_TEMPORARY_ERROR;
    }

    uploader_log_event(
        "INFO", "uploader", "CAMERA_READY_ACCEPTED",
        "action_id=%s vehicle_id=%s job_id=%s video_bytes=%" PRIu64
        " sha256_prefix=%.12s",
        ready->action_id,
        ready->vehicle_id,
        job_id,
        video_bytes,
        sha256);

    result = submit_spool_job(
        runtime,
        spool_root,
        job_id,
        ready->video_path,
        metadata_path,
        reserve_bytes,
        0,
        error,
        error_size);
    unlink(metadata_path);
    return result;
}

/**
 * @brief 시작 시 완성 spool 디렉터리를 검색해 DB 누락 작업을 멱등 재등록함.
 * @return 전부 처리되면 0, 하나 이상 실패하면 -1.
 * @note main은 일부 복구 실패를 로그로 남기되 서비스 시작 자체는 계속함.
 */
static int recover_spool(
    uploader_runtime_t *runtime,
    const char *spool_root,
    uint64_t reserve_bytes)
{
    DIR *directory = opendir(spool_root);
    struct dirent *entry;
    int recovered = 0;
    int failed = 0;

    if (directory == NULL) {
        if (errno == ENOENT) {
            uploader_log_event(
                "INFO", "uploader", "SPOOL_RECOVERY_COMPLETE",
                "recovered=0 failed=0 reason=not_created_yet");
            return 0;
        }
        return -1;
    }

    while ((entry = readdir(directory)) != NULL) {
        char video_path[UPLOADER_PATH_SIZE];
        char metadata_path[UPLOADER_PATH_SIZE];
        char error[UPLOADER_ERROR_SIZE];

        uploader_watchdog_progress(UPLOADER_LOCAL_PROGRESS_MS);
        if (entry->d_name[0] == '.') {
            continue;
        }
        if (spool_validate_job_id(
                entry->d_name, error, sizeof(error)) != 0) {
            continue;
        }
        if (snprintf(video_path, sizeof(video_path), "%s/%s/video.mp4",
                     spool_root, entry->d_name) >= (int)sizeof(video_path) ||
            snprintf(metadata_path, sizeof(metadata_path), "%s/%s/metadata.json",
                     spool_root, entry->d_name) >= (int)sizeof(metadata_path)) {
            failed++;
            continue;
        }
        if (access(video_path, R_OK) != 0 || access(metadata_path, R_OK) != 0) {
            failed++;
            continue;
        }
        if (submit_spool_job(
                runtime,
                spool_root,
                entry->d_name,
                video_path,
                metadata_path,
                reserve_bytes,
                1,
                error,
                sizeof(error)) == 0) {
            recovered++;
        } else {
            failed++;
        }
    }
    closedir(directory);

    uploader_log_event(
        failed == 0 ? "INFO" : "WARN",
        "uploader", "SPOOL_RECOVERY_COMPLETE",
        "recovered=%d failed=%d spool_root=%s",
        recovered, failed, spool_root);
    return failed == 0 ? 0 : -1;
}

/* =========================================================================
 * #### 외부 Unix Domain Socket IPC
 * ========================================================================= */

/**
 * @brief CAMERA_READY 처리 결과를 공용 IPC 응답으로 전송함.
 * @return ipc_send()의 성공/실패 코드를 그대로 반환함.
 */
static int send_ipc_response(
    int client_fd,
    uint32_t sequence,
    int code,
    const char *message)
{
    uploader_response_t status;
    ipc_header_t header;

    memset(&status, 0, sizeof(status));
    memset(&header, 0, sizeof(header));
    status.version = UPLOADER_PROTOCOL_VERSION;
    status.code = code;
    snprintf(status.message, sizeof(status.message), "%s", message);
    header.type = UPLOADER_MSG_RESPONSE;
    header.sequence = sequence;
    header.timestamp = monotonic_time_ns();
    return ipc_send(client_fd, &header, &status, sizeof(status));
}

/**
 * @brief PAUSE/RESUME/QUERY 결과 구조체를 외부 요청자에게 전송함.
 * @return ipc_send() 결과를 그대로 반환함.
 */
static int send_control_state(
    int client_fd,
    uint32_t sequence,
    const uploader_control_state_t *state)
{
    ipc_header_t header;

    memset(&header, 0, sizeof(header));
    header.type = UPLOADER_MSG_CONTROL_STATE;
    header.sequence = sequence;
    header.timestamp = monotonic_time_ns();
    return ipc_send(client_fd, &header, state, sizeof(*state));
}

/**
 * @brief 연결 한 건에서 메시지 하나를 읽어 control 또는 CAMERA_READY로 분기함.
 * @return 반환값 없음. 처리 결과는 같은 client socket으로 응답함.
 * @note client fd의 close는 호출자인 run_ipc_server()가 담당함.
 */
static void handle_ipc_client(
    uploader_runtime_t *runtime,
    int client_fd,
    const char *vehicle_id,
    const char *spool_root,
    uint64_t reserve_bytes)
{
    union {
        uploader_evidence_ready_t ready;
        uploader_control_request_t control;
    } payload;
    uploader_control_state_t control_state;
    ipc_header_t header;
    char error[UPLOADER_ERROR_SIZE];
    int result;
    const struct timeval timeout = {UPLOADER_IPC_CLIENT_TIMEOUT_SECONDS, 0};

    /* IPC 송수신 대기만 제한한다. 로컬 spool/Queue 처리와 HTTP Worker는 계속한다. */
    if (setsockopt(client_fd, SOL_SOCKET, SO_RCVTIMEO,
                   &timeout, sizeof(timeout)) != 0 ||
        setsockopt(client_fd, SOL_SOCKET, SO_SNDTIMEO,
                   &timeout, sizeof(timeout)) != 0) {
        uploader_log_event(
            "WARN", "uploader", "IPC_CLIENT_TIMEOUT_SETUP_FAILED",
            "error=%s action=close_client", strerror(errno));
        return;
    }

    memset(&payload, 0, sizeof(payload));
    memset(&control_state, 0, sizeof(control_state));
    memset(&header, 0, sizeof(header));
    memset(error, 0, sizeof(error));

    result = ipc_recv(client_fd, &header, &payload, sizeof(payload));
    if (result != IPC_SUCCESS) {
        send_ipc_response(
            client_fd,
            header.sequence,
            UPLOADER_RESPONSE_INVALID_REQUEST,
            "invalid CAMERA_READY message");
        return;
    }

    if (header.type == UPLOADER_MSG_PAUSE ||
        header.type == UPLOADER_MSG_RESUME ||
        header.type == UPLOADER_MSG_CONTROL_QUERY) {
        uploader_command_type_t command_type;

        if (header.length != sizeof(payload.control)) {
            send_ipc_response(
                client_fd,
                header.sequence,
                UPLOADER_RESPONSE_INVALID_REQUEST,
                "invalid control message");
            return;
        }

        command_type = header.type == UPLOADER_MSG_PAUSE
            ? UPLOADER_COMMAND_PAUSE
            : (header.type == UPLOADER_MSG_RESUME
                ? UPLOADER_COMMAND_RESUME
                : UPLOADER_COMMAND_CONTROL_QUERY);

        if (payload.control.version != UPLOADER_PROTOCOL_VERSION ||
            strnlen(payload.control.reason, sizeof(payload.control.reason)) ==
                sizeof(payload.control.reason) ||
            queue_control_command(
                runtime,
                command_type,
                payload.control.requester_pid,
                payload.control.lease_ms,
                payload.control.reason,
                &control_state) != 0) {
            control_state.version = UPLOADER_PROTOCOL_VERSION;
            control_state.code = UPLOADER_RESPONSE_INVALID_REQUEST;
        }
        send_control_state(client_fd, header.sequence, &control_state);
        return;
    }

    if (header.type != IPC_MSG_CAMERA_READY ||
        header.length != sizeof(payload.ready)) {
        send_ipc_response(
            client_fd,
            header.sequence,
            UPLOADER_RESPONSE_INVALID_REQUEST,
            "invalid CAMERA_READY message");
        return;
    }

    result = process_ready_payload(
            runtime,
            &payload.ready,
            vehicle_id,
            spool_root,
            reserve_bytes,
            error,
            sizeof(error));
    if (result != UPLOADER_RESPONSE_OK) {
        uploader_log_event(
            "ERROR", "uploader", "CAMERA_READY_REJECTED",
            "action_id=%.64s vehicle_id=%.64s files_preserved=true error=%s",
            payload.ready.action_id, payload.ready.vehicle_id, error);
        send_ipc_response(
            client_fd,
            header.sequence,
            result,
            error);
        return;
    }

    send_ipc_response(
        client_fd,
        header.sequence,
        UPLOADER_RESPONSE_OK,
        "evidence safely spooled and queued");
}

/** 메인 스레드가 소유하는 유일한 외부 IPC accept loop임. */
static int run_ipc_server(
    uploader_runtime_t *runtime,
    const char *vehicle_id,
    const char *spool_root,
    uint64_t reserve_bytes)
{
    int server_fd = ipc_server_create(UPLOADER_SOCKET_PATH);

    if (server_fd < 0) {
        uploader_log_event(
            "ERROR", "uploader", "STARTUP_FAILED",
            "stage=ipc_server_create socket=%s error=소켓_생성_실패 "
            "action=process_exit",
            UPLOADER_SOCKET_PATH);
        return -1;
    }

    uploader_log_event(
        "INFO", "uploader", "INGEST_THREAD_READY",
        "socket=%s spool_root=%s role=main_thread",
        UPLOADER_SOCKET_PATH, spool_root);

    while (stop_requested == 0 && runtime_is_stopping(runtime) == 0) {
        struct pollfd descriptor = {server_fd, POLLIN, 0};
        int client_fd;
        int available = poll(&descriptor, 1, 1000);
        if (available < 0) {
            if (errno == EINTR) continue;
            ipc_close(server_fd);
            return -1;
        }
        uploader_watchdog_progress(UPLOADER_LOCAL_PROGRESS_MS);
        if (available == 0) continue;
        if ((descriptor.revents & POLLIN) == 0) {
            ipc_close(server_fd);
            return -1;
        }
        client_fd = ipc_server_accept(server_fd);
        if (client_fd < 0) {
            if (stop_requested != 0) {
                break;
            }
            continue;
        }
        handle_ipc_client(
            runtime,
            client_fd,
            vehicle_id,
            spool_root,
            reserve_bytes);
        ipc_close(client_fd);
    }

    ipc_close(server_fd);
    unlink(UPLOADER_SOCKET_PATH);
    return 0;
}

/**
 * @brief DB 파일 경로의 부모 디렉터리를 단계별로 생성함.
 * @return 성공 또는 이미 존재하면 0, 경로/권한 오류 시 -1.
 */
static int create_parent_directories(
    const char *file_path,
    char *error,
    size_t error_size)
{
    char path[UPLOADER_PATH_SIZE];
    char *cursor;
    char *last_slash;
    size_t length = strlen(file_path);

    if (length == 0U || length >= sizeof(path)) {
        snprintf(error, error_size, "database path is invalid");
        return -1;
    }
    memcpy(path, file_path, length + 1U);
    last_slash = strrchr(path, '/');
    if (last_slash == NULL || last_slash == path) {
        return 0;
    }
    *last_slash = '\0';

    for (cursor = path + 1; *cursor != '\0'; cursor++) {
        if (*cursor != '/') {
            continue;
        }
        *cursor = '\0';
        if (mkdir(path, 0750) != 0 && errno != EEXIST) {
            snprintf(error, error_size, "mkdir %s: %s", path, strerror(errno));
            return -1;
        }
        *cursor = '/';
    }
    if (mkdir(path, 0750) != 0 && errno != EEXIST) {
        snprintf(error, error_size, "mkdir %s: %s", path, strerror(errno));
        return -1;
    }
    return 0;
}

/**
 * @brief PAUSE lease CLI 값을 1~60000ms 범위로 검증함.
 * @return 성공 시 0, 잘못된 숫자나 범위면 -1.
 */
static int parse_lease_ms(const char *text, uint32_t *lease_ms)
{
    char *end = NULL;
    unsigned long value;

    errno = 0;
    value = strtoul(text, &end, 10);
    if (errno != 0 || end == text || *end != '\0' ||
        value == 0UL || value > UPLOADER_PAUSE_MAX_LEASE_MS) {
        return -1;
    }
    *lease_ms = (uint32_t)value;
    return 0;
}

/**
 * @brief 실행 중인 daemon에 운영용 PAUSE/RESUME/QUERY IPC를 보냄.
 * @return 정상 응답이면 0, 연결/송수신/응답 오류면 1.
 */
static int send_control_command(
    uint16_t message_type,
    const char *reason,
    uint32_t lease_ms)
{
    uploader_control_request_t request;
    uploader_control_state_t response;
    ipc_header_t send_header;
    ipc_header_t receive_header;
    int socket_fd;
    int result;

    memset(&request, 0, sizeof(request));
    memset(&response, 0, sizeof(response));
    memset(&send_header, 0, sizeof(send_header));
    memset(&receive_header, 0, sizeof(receive_header));
    request.version = UPLOADER_PROTOCOL_VERSION;
    request.requester_pid = (uint32_t)getpid();
    request.lease_ms = lease_ms;
    snprintf(request.reason, sizeof(request.reason), "%s", reason);

    socket_fd = ipc_client_connect(UPLOADER_SOCKET_PATH);
    if (socket_fd < 0) {
        return 1;
    }
    send_header.type = message_type;
    send_header.sequence = 1U;
    send_header.timestamp = monotonic_time_ns();
    result = ipc_send(socket_fd, &send_header, &request, sizeof(request));
    if (result == IPC_SUCCESS) {
        result = ipc_recv(
            socket_fd,
            &receive_header,
            &response,
            sizeof(response));
    }
    ipc_close(socket_fd);

    if (result != IPC_SUCCESS ||
        receive_header.type != UPLOADER_MSG_CONTROL_STATE ||
        response.version != UPLOADER_PROTOCOL_VERSION) {
        fprintf(stderr, "invalid control response\n");
        return 1;
    }

    printf("paused=%u remaining_ms=%u generation=%" PRIu64 " reason=%s\n",
           response.paused,
           response.remaining_ms,
           response.generation,
           response.reason);
    return response.code == 0 ? 0 : 1;
}

/* =========================================================================
 * #### 프로세스 진입점과 일관된 종료
 * ========================================================================= */

/**
 * @brief SIGINT와 SIGTERM에 공통 종료 handler를 설치함.
 * @return 두 handler 모두 설치되면 0, 하나라도 실패하면 -1.
 */
static int install_signal_handlers(void)
{
    struct sigaction action;

    memset(&action, 0, sizeof(action));
    action.sa_handler = handle_signal;
    sigemptyset(&action.sa_mask);
    return sigaction(SIGINT, &action, NULL) == 0 &&
        sigaction(SIGTERM, &action, NULL) == 0
            ? 0
            : -1;
}

/**
 * @brief 지원하는 실행 mode와 인자를 stderr에 출력함.
 * @return 반환값 없음.
 */
static void print_usage(const char *program)
{
    fprintf(
        stderr,
        "사용법:\n"
        "  %s\n"
        "  %s daemon                 (기존 호환 별칭)\n"
        "  %s check\n"
        "  %s status\n"
        "  %s pause REASON LEASE_MS\n"
        "  %s resume REASON\n\n"
        "systemd ExecStart에는 인자 없이 '%s'를 사용함.\n"
        "영상 입력은 Camera/AI/Filter가 CAMERA_READY IPC로 전달함.\n",
        program, program, program, program, program, program, program);
}

/**
 * @brief CLI 분기와 단일 프로세스 업무 3스레드 전체 수명주기를 총괄함.
 * @return 성공 0, 실행 실패 1, 사용법 또는 필수 설정 오류 2.
 * @note Queue 시작과 spool 복구 후 Worker를 시작하며 종료 시 Worker→Queue 순으로 join함.
 */
int main(int argc, char **argv)
{
    uploader_runtime_t runtime;
    worker_config_t config;
    queue_thread_args_t queue_args;
    worker_thread_args_t worker_args;
    pthread_t queue_thread;
    pthread_t worker_thread;
    char database_path_buffer[UPLOADER_PATH_SIZE];
    char spool_root_buffer[UPLOADER_PATH_SIZE];
    const char *database_path;
    const char *spool_root;
    const char *server_url;
    uint64_t reserve_bytes;
    char error[UPLOADER_ERROR_SIZE];
    sigset_t worker_signal_mask;
    sigset_t original_signal_mask;
    int queue_created = 0;
    int worker_created = 0;
    int signals_blocked = 0;
    int result = 1;

    if (argc == 2 && strcmp(argv[1], "check") == 0) {
        printf(
            "uploader check: OK\n"
            "processes=1\n"
            "worker_threads=3\nwatchdog=systemd_progress\n"
            "roles=ingest,queue,worker\n"
            "external_ipc_sockets=1\n"
            "socket=%s\n"
            "protocol_version=%u\n"
            "internal_transport=bounded_mutex_condition_queue\n"
            "soft_pause=monotonic_lease\n",
            UPLOADER_SOCKET_PATH,
            UPLOADER_PROTOCOL_VERSION);
        return 0;
    }

    if (argc == 4 && strcmp(argv[1], "pause") == 0) {
        uint32_t lease_ms;
        if (argv[2][0] == '\0' ||
            strlen(argv[2]) >= UPLOADER_PAUSE_REASON_SIZE ||
            parse_lease_ms(argv[3], &lease_ms) != 0) {
            fprintf(stderr, "invalid pause reason or lease\n");
            return 2;
        }
        return send_control_command(
            UPLOADER_MSG_PAUSE, argv[2], lease_ms);
    }

    if (argc == 3 && strcmp(argv[1], "resume") == 0) {
        if (argv[2][0] == '\0' ||
            strlen(argv[2]) >= UPLOADER_PAUSE_REASON_SIZE) {
            fprintf(stderr, "invalid resume reason\n");
            return 2;
        }
        return send_control_command(UPLOADER_MSG_RESUME, argv[2], 0U);
    }

    if (argc == 2 && strcmp(argv[1], "status") == 0) {
        return send_control_command(UPLOADER_MSG_CONTROL_QUERY, "", 0U);
    }

    /*
     * 인자 없는 실행을 정식 운영 계약으로 사용함. 기존 배포 스크립트와 수동
     * 시험을 깨지 않도록 "daemon"은 동일한 서비스 진입점의 호환 별칭으로
     * 유지함. 두 경우 모두 아래의 동일한 1프로세스·업무 3스레드 수명주기를 실행함.
     */
    if (argc != 1 &&
        !(argc == 2 && strcmp(argv[1], "daemon") == 0)) {
        print_usage(argv[0]);
        return 2;
    }

    if (install_signal_handlers() != 0) {
        perror("sigaction");
        return 1;
    }
    signal(SIGPIPE, SIG_IGN);

    memset(error, 0, sizeof(error));
    if (resolve_upload_path(
            "UPLOADER_DB_PATH",
            UPLOADER_DEFAULT_DB_RELATIVE_PATH,
            database_path_buffer,
            sizeof(database_path_buffer),
            error,
            sizeof(error)) != 0 ||
        resolve_upload_path(
            "UPLOADER_SPOOL_ROOT",
            UPLOADER_DEFAULT_SPOOL_RELATIVE_PATH,
            spool_root_buffer,
            sizeof(spool_root_buffer),
            error,
            sizeof(error)) != 0) {
        uploader_log_event(
            "ERROR", "uploader", "STARTUP_FAILED",
            "stage=storage_path error=%s action=process_exit",
            error);
        return 2;
    }
    database_path = database_path_buffer;
    spool_root = spool_root_buffer;
    server_url = configured_value("UPLOADER_SERVER_URL", NULL);
    reserve_bytes = configured_minimum_free_bytes();

    if (server_url == NULL) {
        uploader_log_event(
            "ERROR", "uploader", "STARTUP_FAILED",
            "stage=server_config error=UPLOADER_SERVER_URL이_설정되지_않음 "
            "action=process_exit");
        return 2;
    }

    memset(&runtime, 0, sizeof(runtime));
    memset(&config, 0, sizeof(config));
    memset(&queue_args, 0, sizeof(queue_args));
    memset(&worker_args, 0, sizeof(worker_args));
    memset(error, 0, sizeof(error));

    if (worker_config_load(&config, error, sizeof(error)) != 0) {
        uploader_log_event(
            "ERROR", "uploader", "STARTUP_FAILED",
            "stage=identity_config error=%s action=process_exit",
            error);
        return 1;
    }
    if (create_parent_directories(
            database_path, error, sizeof(error)) != 0) {
        uploader_log_event(
            "ERROR", "uploader", "STARTUP_FAILED",
            "stage=database_directory error=%s action=process_exit",
            error);
        worker_config_clear(&config);
        return 1;
    }
    if (runtime_init(&runtime) != 0) {
        uploader_log_event(
            "ERROR", "uploader", "STARTUP_FAILED",
            "stage=internal_runtime error=초기화_실패 action=process_exit");
        worker_config_clear(&config);
        return 1;
    }

    /* 생성되는 두 스레드는 종료 신호를 받지 않게 함. SIGINT/SIGTERM은 메인
     * Ingest 스레드에만 전달되어 accept를 깨우고 일관된 join 순서를 시작함. */
    sigemptyset(&worker_signal_mask);
    sigaddset(&worker_signal_mask, SIGINT);
    sigaddset(&worker_signal_mask, SIGTERM);
    if (pthread_sigmask(
            SIG_BLOCK,
            &worker_signal_mask,
            &original_signal_mask) != 0) {
        uploader_log_event(
            "ERROR", "uploader", "STARTUP_FAILED",
            "stage=signal_mask error=스레드_신호마스크_설정_실패 "
            "action=cleanup_and_exit");
        goto cleanup;
    }
    signals_blocked = 1;
    if (start_process_watchdog() != 0) {
        perror("uploader watchdog start");
        goto cleanup;
    }

    queue_args.runtime = &runtime;
    queue_args.database_path = database_path;
    if (pthread_create(
            &queue_thread, NULL, queue_thread_main, &queue_args) != 0) {
        uploader_log_event(
            "ERROR", "uploader", "STARTUP_FAILED",
            "stage=queue_thread_create error=스레드_생성_실패 "
            "action=cleanup_and_exit");
        goto cleanup;
    }
    queue_created = 1;

    if (queue_wait_until_started(&runtime, error, sizeof(error)) != 0) {
        uploader_log_event(
            "ERROR", "uploader", "STARTUP_FAILED",
            "stage=queue_thread_start error=%s action=cleanup_and_exit",
            error);
        goto cleanup;
    }

    /* 강제 종료로 남은 오래된 임시 복사본만 먼저 제거함. 최종 job은 건드리지 않음. */
    {
        int removed = 0;
        int cleanup_failed = 0;
        char cleanup_error[UPLOADER_ERROR_SIZE];

        memset(cleanup_error, 0, sizeof(cleanup_error));
        (void)spool_cleanup_stale_incoming(
            spool_root,
            UPLOADER_STALE_INCOMING_SECONDS,
            &removed,
            &cleanup_failed,
            cleanup_error,
            sizeof(cleanup_error));
        uploader_log_event(
            cleanup_failed == 0 ? "INFO" : "WARN",
            "uploader",
            cleanup_failed == 0
                ? "SPOOL_STALE_INCOMING_REMOVED"
                : "SPOOL_TEMP_CLEANUP_FAILED",
            "removed=%d failed=%d error=%s",
            removed, cleanup_failed, cleanup_error);
    }

    /* Worker 시작 전에 기존 spool을 모두 멱등 등록하여 재부팅 복구 순서를 고정함. */
    (void)recover_spool(&runtime, spool_root, reserve_bytes);

    worker_args.runtime = &runtime;
    worker_args.server_url = server_url;
    worker_args.config = &config;
    worker_args.spool_root = spool_root;
    worker_args.reserve_bytes = reserve_bytes;
    ace_watchdog_progress(&process_watchdog, WATCHDOG_WORKER, UPLOADER_LOCAL_PROGRESS_MS);
    if (pthread_create(
            &worker_thread, NULL, worker_thread_main, &worker_args) != 0) {
        uploader_log_event(
            "ERROR", "uploader", "STARTUP_FAILED",
            "stage=worker_thread_create error=스레드_생성_실패 "
            "action=cleanup_and_exit");
        goto cleanup;
    }
    worker_created = 1;

    if (pthread_sigmask(SIG_SETMASK, &original_signal_mask, NULL) != 0) {
        uploader_log_event(
            "ERROR", "uploader", "STARTUP_FAILED",
            "stage=main_signal_restore error=신호마스크_복원_실패 "
            "action=cleanup_and_exit");
        goto cleanup;
    }
    signals_blocked = 0;

    uploader_log_event(
        "INFO", "uploader", "PROCESS_START",
        "pid=%ld processes=1 worker_threads=3 external_ipc_sockets=1 "
        "internal_queue_capacity=%u database=%s spool=%s server=%s",
        (long)getpid(),
        UPLOADER_INTERNAL_COMMAND_CAPACITY,
        database_path,
        spool_root,
        server_url);

    result = run_ipc_server(
        &runtime,
        config.vehicle_id,
        spool_root,
        reserve_bytes) == 0
            ? 0
            : 1;
    if (runtime_has_fatal_error(&runtime) != 0) {
        result = 1;
    }

cleanup:
    if (signals_blocked != 0) {
        pthread_sigmask(SIG_SETMASK, &original_signal_mask, NULL);
    }
    runtime_stop(&runtime);
    if (worker_created != 0) {
        pthread_join(worker_thread, NULL);
    }
    if (queue_created != 0) {
        pthread_join(queue_thread, NULL);
    }
    unlink(UPLOADER_SOCKET_PATH);
    ace_watchdog_stop(&process_watchdog);
    runtime_destroy(&runtime);
    worker_config_clear(&config);

    uploader_log_event(
        "INFO", "uploader", "PROCESS_STOP",
        "reason=%s cleanup=complete threads_joined=true",
        stop_requested != 0 ? "signal" : "server_return");
    return result;
}
