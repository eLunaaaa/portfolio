#define _POSIX_C_SOURCE 200809L

/**
 * @file time_sync.c
 * @brief chronyd의 동기화 상태를 팀 IPC로 제공하는 단일 프로세스·업무 3스레드 프로그램.
 *
 * 스레드 역할
 *  1. 메인 IPC 스레드: 외부 상태 요청 수신 및 최신 상태 응답
 *  2. Monitor 스레드: chronyc tracking 측정값 생산
 *  3. State 스레드: 측정값 소비, 세대 번호 부여, 최신 상태와 로그 관리
 *
 * Monitor와 State 사이에는 고정 크기 ring buffer를 둠. mutex와 condition을
 * 사용하므로 데이터가 없거나 버퍼가 가득 찼을 때 busy-loop하지 않음.
 */

#include <errno.h>
#include <fcntl.h>
#include <poll.h>
#include <spawn.h>
#include <math.h>
#include <pthread.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/time.h>
#include <sys/wait.h>
#include <time.h>
#include <unistd.h>

#include "ipc.h"
#include "protocol.h"
#include "time_sync_protocol.h"
#include "runtime_watchdog.h"

#define TIME_SYNC_SOCKET_PATH "/tmp/time-sync.sock"
#define TIME_SYNC_QUEUE_CAPACITY 8U
#define TIME_SYNC_DEFAULT_INTERVAL_MS 1000U
#define TIME_SYNC_MIN_INTERVAL_MS 200U
#define TIME_SYNC_MAX_INTERVAL_MS 60000U
#define TIME_SYNC_DEFAULT_MAX_OFFSET_MS 100.0
#define TIME_SYNC_LINE_SIZE 512U
#define TIME_SYNC_CLIENT_TIMEOUT_SECONDS 2
#define TIME_SYNC_CHRONYC_TIMEOUT_MS 5000U

enum { WATCHDOG_IPC, WATCHDOG_MONITOR, WATCHDOG_STATE };
static ace_watchdog_t process_watchdog;
static uint32_t sample_deadline_ms = 11000U;

typedef struct {
    pthread_mutex_t mutex;
    pthread_cond_t sample_added;
    pthread_cond_t sample_removed;
    pthread_cond_t stop_changed;

    time_sync_status_t queue[TIME_SYNC_QUEUE_CAPACITY];
    uint64_t queue_monotonic_ns[TIME_SYNC_QUEUE_CAPACITY];
    size_t queue_head;
    size_t queue_tail;
    size_t queue_count;

    time_sync_status_t latest;
    int has_latest;
    uint64_t latest_monotonic_ns;
    int stopping;
} time_sync_runtime_t;

typedef struct {
    time_sync_runtime_t *runtime;
    uint32_t interval_ms;
    double max_offset_ms;
} monitor_args_t;

static volatile sig_atomic_t stop_requested = 0;

/* 기능: 단조 증가 시계에 맞춘 조건 변수를 생성한다.
 * 입력: 초기화할 condition 포인터.
 * 출력·구조: pthread 상태 코드. 속성 생성·시계 선택·조건 변수 초기화 후 속성을 해제한다.
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

/* =========================================================================
 * #### 프로세스 종료 신호와 두 시간 기준
 *
 * CLOCK_MONOTONIC은 IPC header처럼 경과 시간을 표현할 때 사용하고,
 * CLOCK_REALTIME은 실제 측정 epoch를 다른 프로세스에 알려줄 때 사용함.
 * ========================================================================= */

/**
 * @brief SIGINT/SIGTERM을 받았음을 메인 IPC loop에 알림.
 * @param signal_number 수신한 신호 번호. 현재는 종류를 구분하지 않음.
 * @return 반환값 없음.
 * @note 비동기 신호 안전성을 위해 플래그만 변경하고 정리는 main이 수행함.
 */
static void handle_signal(int signal_number)
{
    (void)signal_number;
    stop_requested = 1;
}

/**
 * @brief 시스템 시각 보정에 영향받지 않는 단조 시간 ns를 구함.
 * @return 성공 시 CLOCK_MONOTONIC ns, 실패 시 0.
 */
static uint64_t monotonic_time_ns(void)
{
    struct timespec now;

    if (clock_gettime(CLOCK_MONOTONIC, &now) != 0) {
        return 0U;
    }
    return (uint64_t)now.tv_sec * 1000000000ULL + (uint64_t)now.tv_nsec;
}

/**
 * @brief chrony sample이 생성된 실제 epoch 시각을 ns로 구함.
 * @return 성공 시 CLOCK_REALTIME ns, 실패 시 0.
 */
static int64_t realtime_time_ns(void)
{
    struct timespec now;

    if (clock_gettime(CLOCK_REALTIME, &now) != 0) {
        return 0;
    }
    return (int64_t)now.tv_sec * 1000000000LL + (int64_t)now.tv_nsec;
}

/* =========================================================================
 * #### 상태 이름과 구조화 로그
 * ========================================================================= */

/**
 * @brief 공개 상태 enum을 사람이 읽을 수 있는 고정 문자열로 변환함.
 * @return SYNCING/SYNCED/UNSYNCED/ERROR/UNKNOWN 정적 문자열.
 * @note 반환 문자열은 정적 저장소이므로 호출자가 free하면 안 됨.
 */
static const char *state_name(uint32_t state)
{
    switch (state) {
    case TIME_SYNC_SYNCING:
        return "SYNCING";
    case TIME_SYNC_SYNCED:
        return "SYNCED";
    case TIME_SYNC_UNSYNCED:
        return "UNSYNCED";
    case TIME_SYNC_ERROR:
        return "ERROR";
    default:
        return "UNKNOWN";
    }
}

/**
 * @brief 최신 시간 상태를 검색 가능한 한 줄 로그로 stderr에 기록함.
 * @return 반환값 없음. 출력 직후 fflush함.
 */
static void log_status(const char *event, const time_sync_status_t *status)
{
    struct timespec now;

    clock_gettime(CLOCK_REALTIME, &now);
    fprintf(
        stderr,
        "time=%lld.%03ld level=%s component=time_sync event=%s "
        "state=%s generation=%u stratum=%u offset_ms=%.6f source=\"%s\" "
        "leap=\"%s\" detail=\"%s\"\n",
        (long long)now.tv_sec,
        now.tv_nsec / 1000000L,
        status->state == TIME_SYNC_ERROR ? "ERROR" : "INFO",
        event,
        state_name(status->state),
        status->generation,
        status->stratum,
        status->system_offset_ms,
        status->source,
        status->leap_status,
        status->detail);
    fflush(stderr);
}

/* =========================================================================
 * #### 공유 runtime 수명주기
 * ========================================================================= */

/**
 * @brief 8칸 sample queue와 최신 snapshot을 보호할 pthread 자원을 초기화함.
 * @return 모든 mutex/condition 생성 성공 시 0, 실패 시 -1.
 * @note 중간 실패 시 이미 만든 자원을 역순으로 정리함.
 */
static int runtime_init(time_sync_runtime_t *runtime)
{
    memset(runtime, 0, sizeof(*runtime));

    if (pthread_mutex_init(&runtime->mutex, NULL) != 0) {
        return -1;
    }
    if (monotonic_cond_init(&runtime->sample_added) != 0) {
        pthread_mutex_destroy(&runtime->mutex);
        return -1;
    }
    if (monotonic_cond_init(&runtime->sample_removed) != 0) {
        pthread_cond_destroy(&runtime->sample_added);
        pthread_mutex_destroy(&runtime->mutex);
        return -1;
    }
    if (monotonic_cond_init(&runtime->stop_changed) != 0) {
        pthread_cond_destroy(&runtime->sample_removed);
        pthread_cond_destroy(&runtime->sample_added);
        pthread_mutex_destroy(&runtime->mutex);
        return -1;
    }
    return 0;
}

/**
 * @brief 종료 플래그를 세우고 생산자·소비자·주기 대기자를 모두 깨움.
 * @return 반환값 없음.
 */
static void runtime_stop(time_sync_runtime_t *runtime)
{
    pthread_mutex_lock(&runtime->mutex);
    runtime->stopping = 1;
    pthread_cond_broadcast(&runtime->sample_added);
    pthread_cond_broadcast(&runtime->sample_removed);
    pthread_cond_broadcast(&runtime->stop_changed);
    pthread_mutex_unlock(&runtime->mutex);
}

/**
 * @brief mutex로 보호된 종료 상태를 읽음.
 * @return 종료 중이면 1, 실행 중이면 0.
 */
static int runtime_is_stopping(time_sync_runtime_t *runtime)
{
    int stopping;

    pthread_mutex_lock(&runtime->mutex);
    stopping = runtime->stopping;
    pthread_mutex_unlock(&runtime->mutex);
    return stopping;
}

/**
 * @brief runtime의 condition variable과 mutex를 파괴함.
 * @return 반환값 없음.
 * @pre Monitor와 State 스레드를 모두 join한 뒤 호출해야 함.
 */
static void runtime_destroy(time_sync_runtime_t *runtime)
{
    pthread_cond_destroy(&runtime->stop_changed);
    pthread_cond_destroy(&runtime->sample_removed);
    pthread_cond_destroy(&runtime->sample_added);
    pthread_mutex_destroy(&runtime->mutex);
}

/* =========================================================================
 * #### Monitor → State 생산자-소비자 Ring Buffer
 * ========================================================================= */

/**
 * @brief Monitor가 완성한 측정값을 고정 8칸 ring buffer에 생산함.
 * @return 등록 성공 시 0, 종료가 시작되면 -1.
 * @note buffer가 가득 차면 State가 공간을 만들 때까지 condition에서 잠듦.
 */
static int runtime_push_sample(
    time_sync_runtime_t *runtime,
    const time_sync_status_t *sample)
{
    uint64_t sampled_at = monotonic_time_ns();
    pthread_mutex_lock(&runtime->mutex);
    while (runtime->queue_count == TIME_SYNC_QUEUE_CAPACITY &&
           runtime->stopping == 0) {
        pthread_cond_wait(&runtime->sample_removed, &runtime->mutex);
    }
    if (runtime->stopping != 0) {
        pthread_mutex_unlock(&runtime->mutex);
        return -1;
    }

    runtime->queue[runtime->queue_tail] = *sample;
    runtime->queue_monotonic_ns[runtime->queue_tail] = sampled_at;
    runtime->queue_tail =
        (runtime->queue_tail + 1U) % TIME_SYNC_QUEUE_CAPACITY;
    runtime->queue_count++;
    pthread_cond_signal(&runtime->sample_added);
    pthread_mutex_unlock(&runtime->mutex);
    return 0;
}

/**
 * @brief State가 가장 오래된 측정값을 ring buffer에서 소비함.
 * @return sample을 돌려주면 0, 종료됐고 남은 sample도 없으면 -1.
 * @note queue가 비면 busy-loop하지 않고 sample_added에서 잠듦.
 */
static int runtime_pop_sample(
    time_sync_runtime_t *runtime,
    time_sync_status_t *sample,
    uint64_t *sample_monotonic_ns)
{
    pthread_mutex_lock(&runtime->mutex);
    while (runtime->queue_count == 0U && runtime->stopping == 0) {
        pthread_cond_wait(&runtime->sample_added, &runtime->mutex);
    }
    if (runtime->queue_count == 0U && runtime->stopping != 0) {
        pthread_mutex_unlock(&runtime->mutex);
        return -1;
    }

    *sample = runtime->queue[runtime->queue_head];
    *sample_monotonic_ns = runtime->queue_monotonic_ns[runtime->queue_head];
    runtime->queue_head =
        (runtime->queue_head + 1U) % TIME_SYNC_QUEUE_CAPACITY;
    runtime->queue_count--;
    pthread_cond_signal(&runtime->sample_removed);
    pthread_mutex_unlock(&runtime->mutex);
    return 0;
}

/**
 * @brief Monitor를 다음 측정 주기까지 재우되 종료 요청에는 즉시 반응함.
 * @return timeout으로 계속 실행하면 0, 종료 상태이면 1.
 * @note 시각 보정 중에도 측정 간격은 CLOCK_MONOTONIC으로 유지함.
 */
static int runtime_wait_interval(
    time_sync_runtime_t *runtime,
    uint32_t interval_ms)
{
    struct timespec deadline;
    int stopping;

    if (clock_gettime(CLOCK_MONOTONIC, &deadline) != 0) {
        return runtime_is_stopping(runtime);
    }
    deadline.tv_sec += (time_t)(interval_ms / 1000U);
    deadline.tv_nsec += (long)(interval_ms % 1000U) * 1000000L;
    if (deadline.tv_nsec >= 1000000000L) {
        deadline.tv_sec++;
        deadline.tv_nsec -= 1000000000L;
    }

    pthread_mutex_lock(&runtime->mutex);
    while (runtime->stopping == 0) {
        int result = pthread_cond_timedwait(
            &runtime->stop_changed,
            &runtime->mutex,
            &deadline);
        if (result == ETIMEDOUT) {
            break;
        }
    }
    stopping = runtime->stopping;
    pthread_mutex_unlock(&runtime->mutex);
    return stopping;
}

/* =========================================================================
 * #### State → IPC 최신 Snapshot 공유
 * ========================================================================= */

/**
 * @brief State가 generation까지 완성한 상태를 최신 snapshot으로 게시함.
 * @return 반환값 없음. 구조체 전체를 mutex 안에서 한 번에 복사함.
 */
static void runtime_publish_latest(
    time_sync_runtime_t *runtime,
    const time_sync_status_t *status,
    uint64_t sample_monotonic_ns)
{
    pthread_mutex_lock(&runtime->mutex);
    runtime->latest = *status;
    runtime->has_latest = 1;
    runtime->latest_monotonic_ns = sample_monotonic_ns;
    pthread_mutex_unlock(&runtime->mutex);
}

/**
 * @brief 메인 IPC 스레드가 최신 snapshot의 일관된 사본을 읽음.
 * @return 반환값 없음. 출력은 status에 기록함.
 * @note 첫 측정 전에는 protocol version과 UNKNOWN 설명을 직접 생성함.
 */
static void runtime_read_latest(
    time_sync_runtime_t *runtime,
    time_sync_status_t *status)
{
    pthread_mutex_lock(&runtime->mutex);
    if (runtime->has_latest != 0) {
        *status = runtime->latest;
        if (monotonic_time_ns() - runtime->latest_monotonic_ns >
                (uint64_t)sample_deadline_ms * 1000000ULL) {
            status->state = TIME_SYNC_ERROR;
            snprintf(status->detail, sizeof(status->detail),
                     "chrony sample is stale");
        }
    } else {
        memset(status, 0, sizeof(*status));
        status->version = TIME_SYNC_PROTOCOL_VERSION;
        status->state = TIME_SYNC_UNKNOWN;
        snprintf(status->detail, sizeof(status->detail),
                 "first chrony sample is not ready");
    }
    pthread_mutex_unlock(&runtime->mutex);
}

/* =========================================================================
 * #### chronyc tracking 문자열 Parsing Helper
 * ========================================================================= */

/**
 * @brief 문자열 앞뒤의 공백, tab, CR, LF를 제거함.
 * @return 실제 내용이 시작되는 원본 buffer 내부 포인터.
 * @note 뒤쪽 공백 위치에 NUL을 써서 입력 문자열을 수정함.
 */
static char *trim_text(char *text)
{
    char *end;

    while (*text == ' ' || *text == '\t') {
        text++;
    }
    end = text + strlen(text);
    while (end > text &&
           (end[-1] == ' ' || end[-1] == '\t' ||
            end[-1] == '\r' || end[-1] == '\n')) {
        end--;
    }
    *end = '\0';
    return text;
}

/**
 * @brief chronyc의 "Label : Value" 줄에서 첫 colon 뒤 값을 찾음.
 * @return trim된 값 포인터, colon이 없으면 NULL.
 */
static const char *value_after_colon(char *line)
{
    char *colon = strchr(line, ':');

    return colon != NULL ? trim_text(colon + 1) : NULL;
}

/**
 * @brief line이 지정 chronyc label로 시작하는지 검사함.
 * @return prefix가 일치하면 1, 아니면 0.
 */
static int starts_with(const char *text, const char *prefix)
{
    return strncmp(text, prefix, strlen(prefix)) == 0;
}

/**
 * @brief chronyc의 초 단위 선행 숫자를 millisecond로 변환함.
 * @return 유한한 숫자를 읽으면 0, 변환 오류면 -1.
 * @note fast/slow 방향은 System time 처리 분기에서 별도로 적용함.
 */
static int parse_seconds(const char *text, double *milliseconds)
{
    char *end = NULL;
    double seconds;

    errno = 0;
    seconds = strtod(text, &end);
    if (errno != 0 || end == text || !isfinite(seconds)) {
        return -1;
    }
    *milliseconds = seconds * 1000.0;
    return 0;
}

/**
 * chronyc는 chronyd가 제공하는 공식 관리 클라이언트임. 고정 명령만 실행하므로
 * 외부 입력이 shell 명령에 들어가지 않음. LC_ALL=C로 출력 라벨을 고정함.
 */
/* =========================================================================
 * #### Chronyd 상태 측정과 신뢰도 판정
 * ========================================================================= */

/**
 * @brief chronyc tracking을 실행하고 출력을 완전한 상태 snapshot으로 변환함.
 * @param max_offset_ms SYNCED로 인정할 system offset 절댓값 상한.
 * @param status 결과 상태, 측정값, source, 오류 설명을 돌려받음.
 * @return 반환값 없음. 실행/해석 실패는 status=ERROR로 표현함.
 * @note 외부 입력을 shell 명령에 합치지 않고 locale과 timeout을 고정함.
 */
/* One child, one monotonic deadline for both output and exit. A stuck chronyc
 * is killed and reaped before an ERROR sample is published. */
static int read_chrony_output(char *output, size_t capacity)
{
    int descriptors[2];
    posix_spawn_file_actions_t actions;
    posix_spawnattr_t attributes;
    sigset_t child_mask;
    pid_t child = -1;
    int result, child_status = 0, reaped = 0, eof = 0;
    size_t used = 0U;
    uint64_t deadline = monotonic_time_ns() +
        (uint64_t)TIME_SYNC_CHRONYC_TIMEOUT_MS * 1000000ULL;
    char *arguments[] = {"chronyc", "-n", "tracking", NULL};
    char *environment[] = {"LC_ALL=C", "PATH=/usr/sbin:/usr/bin:/sbin:/bin", NULL};

    if (pipe(descriptors) != 0) return -1;
    if (fcntl(descriptors[0], F_SETFL, O_NONBLOCK) < 0) {
        close(descriptors[0]);
        close(descriptors[1]);
        return -1;
    }
    result = posix_spawn_file_actions_init(&actions);
    if (result != 0) goto close_pipe;
    result = posix_spawnattr_init(&attributes);
    if (result != 0) {
        posix_spawn_file_actions_destroy(&actions);
        goto close_pipe;
    }
    sigemptyset(&child_mask);
    result = posix_spawnattr_setsigmask(&attributes, &child_mask);
    if (result == 0) result = posix_spawnattr_setflags(&attributes, POSIX_SPAWN_SETSIGMASK);
    if (result == 0) result = posix_spawn_file_actions_adddup2(&actions, descriptors[1], STDOUT_FILENO);
    if (result == 0) result = posix_spawn_file_actions_adddup2(&actions, descriptors[1], STDERR_FILENO);
    if (result == 0) result = posix_spawn_file_actions_addclose(&actions, descriptors[0]);
    if (result == 0) result = posix_spawn_file_actions_addclose(&actions, descriptors[1]);
    if (result == 0) result = posix_spawnp(&child, "chronyc", &actions,
                                         &attributes, arguments, environment);
    posix_spawnattr_destroy(&attributes);
    posix_spawn_file_actions_destroy(&actions);
    if (result != 0) goto close_pipe;
    close(descriptors[1]);

    while (!eof || !reaped) {
        struct pollfd descriptor = {descriptors[0], POLLIN, 0};
        ssize_t count;
        if (stop_requested != 0 || monotonic_time_ns() >= deadline) {
            errno = stop_requested != 0 ? EINTR : ETIMEDOUT;
            goto abort_child;
        }
        if (!eof) {
            if (used + 1U >= capacity) {
                errno = EOVERFLOW;
                goto abort_child;
            }
            count = read(descriptors[0], output + used, capacity - used - 1U);
            if (count > 0) used += (size_t)count;
            else if (count == 0) eof = 1;
            else if (errno != EAGAIN && errno != EINTR) goto abort_child;
        }
        if (!reaped) {
            pid_t waited = waitpid(child, &child_status, WNOHANG);
            if (waited == child) reaped = 1;
            else if (waited < 0 && errno != EINTR) goto abort_child;
        }
        if (!eof || !reaped) {
            if (poll(eof ? NULL : &descriptor, eof ? 0U : 1U, 50) < 0 && errno != EINTR)
                goto abort_child;
        }
    }
    close(descriptors[0]);
    output[used] = '\0';
    if (!WIFEXITED(child_status) || WEXITSTATUS(child_status) != 0) {
        errno = EIO;
        return -1;
    }
    return 0;

abort_child:
    result = errno;
    if (!reaped) {
        kill(child, SIGKILL);
        while (waitpid(child, &child_status, 0) < 0 && errno == EINTR) {}
    }
    close(descriptors[0]);
    errno = result;
    return -1;

close_pipe:
    close(descriptors[0]);
    close(descriptors[1]);
    errno = result;
    return -1;
}

/* 기능: chrony 측정 결과를 공통 시간 동기화 상태로 변환한다.
 * 입력: 허용 오차 max_offset_ms와 출력 status.
 * 출력·구조: 반환값 없음; 측정 시각·오차·동기화 상태·설명을 status에 기록. tracking 실행 결과를 파싱하고 소스 동기화와 오차 조건을 구분한다.
 */
static void collect_chrony_sample(
    double max_offset_ms,
    time_sync_status_t *status)
{
    char output[TIME_SYNC_LINE_SIZE * 16U];
    char *line, *save = NULL;
    int saw_tracking = 0;

    memset(status, 0, sizeof(*status));
    status->version = TIME_SYNC_PROTOCOL_VERSION;
    status->state = TIME_SYNC_ERROR;
    status->sampled_realtime_ns = realtime_time_ns();
    snprintf(status->detail, sizeof(status->detail),
             "chronyc tracking failed");

    if (read_chrony_output(output, sizeof(output)) != 0) {
        snprintf(status->detail, sizeof(status->detail),
                 "chronyc tracking: %s", strerror(errno));
        status->sampled_realtime_ns = realtime_time_ns();
        return;
    }
    status->sampled_realtime_ns = realtime_time_ns();

    for (line = strtok_r(output, "\n", &save); line != NULL;
         line = strtok_r(NULL, "\n", &save)) {
        char *text = trim_text(line);
        const char *value = value_after_colon(text);

        if (value == NULL) {
            if (status->detail[0] == '\0' ||
                strcmp(status->detail, "chronyc tracking failed") == 0) {
                snprintf(status->detail, sizeof(status->detail), "%s", text);
            }
            continue;
        }

        if (starts_with(text, "Reference ID")) {
            snprintf(status->source, sizeof(status->source), "%s", value);
            saw_tracking = 1;
        } else if (starts_with(text, "Stratum")) {
            char *end = NULL;
            unsigned long parsed = strtoul(value, &end, 10);
            if (end != value && parsed <= UINT32_MAX) {
                status->stratum = (uint32_t)parsed;
            }
        } else if (starts_with(text, "System time")) {
            double offset_ms;
            if (parse_seconds(value, &offset_ms) == 0) {
                if (strstr(value, "slow") != NULL) {
                    offset_ms = -fabs(offset_ms);
                } else {
                    offset_ms = fabs(offset_ms);
                }
                status->system_offset_ms = offset_ms;
            }
        } else if (starts_with(text, "Last offset")) {
            (void)parse_seconds(value, &status->last_offset_ms);
        } else if (starts_with(text, "RMS offset")) {
            (void)parse_seconds(value, &status->rms_offset_ms);
        } else if (starts_with(text, "Root delay")) {
            (void)parse_seconds(value, &status->root_delay_ms);
        } else if (starts_with(text, "Root dispersion")) {
            (void)parse_seconds(value, &status->root_dispersion_ms);
        } else if (starts_with(text, "Update interval")) {
            char *end = NULL;
            double parsed = strtod(value, &end);
            if (end != value && isfinite(parsed)) {
                status->update_interval_s = parsed;
            }
        } else if (starts_with(text, "Leap status")) {
            snprintf(status->leap_status,
                     sizeof(status->leap_status), "%s", value);
        }
    }

    if (saw_tracking == 0) {
        if (status->detail[0] == '\0') {
            snprintf(status->detail, sizeof(status->detail),
                     "chronyc exited with an error");
        }
        return;
    }

    if (strcmp(status->leap_status, "Normal") != 0 ||
        status->stratum == 0U || status->stratum >= 16U) {
        status->state = TIME_SYNC_UNSYNCED;
        snprintf(status->detail, sizeof(status->detail),
                 "chronyd has no synchronized source");
    } else if (fabs(status->system_offset_ms) > max_offset_ms) {
        status->state = TIME_SYNC_SYNCING;
        snprintf(status->detail, sizeof(status->detail),
                 "offset exceeds %.3f ms threshold", max_offset_ms);
    } else {
        status->state = TIME_SYNC_SYNCED;
        snprintf(status->detail, sizeof(status->detail),
                 "chronyd synchronized");
    }
}

/* =========================================================================
 * #### Monitor 생산자와 State 소비자 스레드
 * ========================================================================= */

/**
 * @brief 설정 주기마다 chrony sample을 생성해 ring buffer에 넣는 스레드 진입점.
 * @param argument monitor_args_t 포인터.
 * @return pthread 규약에 따라 항상 NULL.
 */
static void *monitor_thread_main(void *argument)
{
    monitor_args_t *args = argument;

    while (runtime_is_stopping(args->runtime) == 0) {
        time_sync_status_t sample;

        ace_watchdog_progress(&process_watchdog, WATCHDOG_MONITOR, sample_deadline_ms);
        collect_chrony_sample(args->max_offset_ms, &sample);
        if (runtime_push_sample(args->runtime, &sample) != 0) {
            break;
        }
        ace_watchdog_progress(&process_watchdog, WATCHDOG_MONITOR, sample_deadline_ms);
        if (runtime_wait_interval(args->runtime, args->interval_ms) != 0) {
            break;
        }
    }
    return NULL;
}

/**
 * @brief sample을 순서대로 소비하여 generation, 최신 snapshot, 로그를 관리함.
 * @param argument time_sync_runtime_t 포인터.
 * @return pthread 규약에 따라 항상 NULL.
 * @note 상태 전이는 항상 기록하고 동일 상태는 30회마다만 기록함.
 */
static void *state_thread_main(void *argument)
{
    time_sync_runtime_t *runtime = argument;
    uint32_t generation = 0U;
    uint32_t previous_state = UINT32_MAX;

    for (;;) {
        time_sync_status_t sample;

        uint64_t sample_monotonic_ns;
        if (runtime_pop_sample(runtime, &sample, &sample_monotonic_ns) != 0) {
            break;
        }
        sample.generation = ++generation;
        runtime_publish_latest(runtime, &sample, sample_monotonic_ns);
        ace_watchdog_progress(&process_watchdog, WATCHDOG_STATE, sample_deadline_ms);

        /* 상태 전이는 반드시 남기고, 정상 상태는 30회마다 기록해 로그 폭증을 막음. */
        if (sample.state != previous_state || generation % 30U == 0U) {
            log_status(
                sample.state != previous_state
                    ? "TIME_SYNC_STATE_CHANGED"
                    : "TIME_SYNC_STATUS",
                &sample);
        }
        previous_state = sample.state;
    }
    return NULL;
}

/* =========================================================================
 * #### 외부 시간 상태 조회 IPC
 * ========================================================================= */

/**
 * @brief 연결 한 건의 TIME_SYNC_REQUEST를 검증하고 최신 STATUS를 응답함.
 * @return 반환값 없음. 잘못된 요청도 state=ERROR 응답으로 종료함.
 * @note client fd close는 run_ipc_server()가 담당함.
 */
static void handle_ipc_client(
    time_sync_runtime_t *runtime,
    int client_fd)
{
    time_sync_request_t request;
    time_sync_status_t response;
    ipc_header_t receive_header;
    ipc_header_t send_header;
    int result;
    const struct timeval timeout = {TIME_SYNC_CLIENT_TIMEOUT_SECONDS, 0};

    /* 느리거나 중단된 요청은 이 연결만 종료한다. 측정 스레드와 최신 상태는 유지한다. */
    if (setsockopt(client_fd, SOL_SOCKET, SO_RCVTIMEO,
                   &timeout, sizeof(timeout)) != 0 ||
        setsockopt(client_fd, SOL_SOCKET, SO_SNDTIMEO,
                   &timeout, sizeof(timeout)) != 0) {
        fprintf(stderr, "time_sync client timeout setup failed: %s\n", strerror(errno));
        return;
    }

    memset(&request, 0, sizeof(request));
    memset(&response, 0, sizeof(response));
    memset(&receive_header, 0, sizeof(receive_header));
    memset(&send_header, 0, sizeof(send_header));

    result = ipc_recv(
        client_fd,
        &receive_header,
        &request,
        sizeof(request));

    if (result != IPC_SUCCESS) {
        return;
    }

    if (receive_header.type != IPC_MSG_TIME_SYNC_REQUEST ||
        receive_header.length != sizeof(request) ||
        request.version != TIME_SYNC_PROTOCOL_VERSION) {
        response.version = TIME_SYNC_PROTOCOL_VERSION;
        response.state = TIME_SYNC_ERROR;
        snprintf(response.detail, sizeof(response.detail),
                 "invalid time sync IPC request");
    } else {
        runtime_read_latest(runtime, &response);
    }

    send_header.type = IPC_MSG_TIME_SYNC_STATUS;
    send_header.flags = IPC_FLAG_NONE;
    send_header.sequence = receive_header.sequence;
    send_header.timestamp = monotonic_time_ns();
    (void)ipc_send(client_fd, &send_header, &response, sizeof(response));
}

/** 메인 스레드가 time_sync 외부 IPC 서버 소켓의 생성, 요청 처리, 종료를 전담함. */
static int run_ipc_server(time_sync_runtime_t *runtime)
{
    int server_fd = ipc_server_create(TIME_SYNC_SOCKET_PATH);

    if (server_fd < 0) {
        return -1;
    }
    fprintf(
        stderr,
        "level=INFO component=time_sync event=IPC_SERVER_READY "
        "socket=%s role=main_thread\n",
        TIME_SYNC_SOCKET_PATH);

    while (stop_requested == 0 && runtime_is_stopping(runtime) == 0) {
        struct pollfd descriptor = {server_fd, POLLIN, 0};
        int client_fd;
        int available = poll(&descriptor, 1, 1000);
        if (available < 0) {
            if (errno == EINTR) continue;
            ipc_close(server_fd);
            return -1;
        }
        ace_watchdog_progress(&process_watchdog, WATCHDOG_IPC, 10000U);
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
        handle_ipc_client(runtime, client_fd);
        ipc_close(client_fd);
    }

    ipc_close(server_fd);
    unlink(TIME_SYNC_SOCKET_PATH);
    return 0;
}

/* =========================================================================
 * #### 환경변수 기반 측정 정책
 * ========================================================================= */

/**
 * @brief chrony 측정 interval을 환경에서 읽고 안전한 범위로 제한함.
 * @return 200~60000ms 유효값, 누락/오류 시 기본 1000ms.
 */
static uint32_t configured_interval_ms(void)
{
    const char *text = getenv("TIME_SYNC_INTERVAL_MS");
    char *end = NULL;
    unsigned long value;

    if (text == NULL || text[0] == '\0') {
        return TIME_SYNC_DEFAULT_INTERVAL_MS;
    }
    errno = 0;
    value = strtoul(text, &end, 10);
    if (errno != 0 || end == text || *end != '\0' ||
        value < TIME_SYNC_MIN_INTERVAL_MS ||
        value > TIME_SYNC_MAX_INTERVAL_MS) {
        return TIME_SYNC_DEFAULT_INTERVAL_MS;
    }
    return (uint32_t)value;
}

/**
 * @brief SYNCED 판정에 사용할 최대 offset을 환경에서 읽음.
 * @return 0~60000ms 범위 유효값, 누락/오류 시 기본 100ms.
 */
static double configured_max_offset_ms(void)
{
    const char *text = getenv("TIME_SYNC_MAX_OFFSET_MS");
    char *end = NULL;
    double value;

    if (text == NULL || text[0] == '\0') {
        return TIME_SYNC_DEFAULT_MAX_OFFSET_MS;
    }
    errno = 0;
    value = strtod(text, &end);
    if (errno != 0 || end == text || *end != '\0' ||
        !isfinite(value) || value <= 0.0 || value > 60000.0) {
        return TIME_SYNC_DEFAULT_MAX_OFFSET_MS;
    }
    return value;
}

/* =========================================================================
 * #### 다른 프로세스를 위한 내장 IPC Client 예제
 * ========================================================================= */

/**
 * @brief 실행 중인 time_sync에 최신 상태를 요청하고 사람이 읽게 출력함.
 * @return SYNCED이면 0, IPC/응답 형식 실패면 1, 통신은 됐으나 비동기면 2.
 * @note --status가 2인 것은 IPC 장애가 아니라 현재 시간이 신뢰 불가하다는 뜻임.
 */
static int request_status(void)
{
    time_sync_request_t request;
    time_sync_status_t response;
    ipc_header_t send_header;
    ipc_header_t receive_header;
    int client_fd;
    int result;

    memset(&request, 0, sizeof(request));
    memset(&response, 0, sizeof(response));
    memset(&send_header, 0, sizeof(send_header));
    memset(&receive_header, 0, sizeof(receive_header));

    request.version = TIME_SYNC_PROTOCOL_VERSION;
    client_fd = ipc_client_connect(TIME_SYNC_SOCKET_PATH);
    if (client_fd < 0) {
        return 1;
    }

    send_header.type = IPC_MSG_TIME_SYNC_REQUEST;
    send_header.flags = IPC_FLAG_NONE;
    send_header.sequence = 1U;
    send_header.timestamp = monotonic_time_ns();
    result = ipc_send(client_fd, &send_header, &request, sizeof(request));
    if (result == IPC_SUCCESS) {
        result = ipc_recv(
            client_fd,
            &receive_header,
            &response,
            sizeof(response));
    }
    ipc_close(client_fd);

    if (result != IPC_SUCCESS ||
        receive_header.type != IPC_MSG_TIME_SYNC_STATUS ||
        receive_header.length != sizeof(response) ||
        response.version != TIME_SYNC_PROTOCOL_VERSION) {
        fprintf(stderr, "invalid time sync IPC response\n");
        return 1;
    }

    printf(
        "state=%s generation=%u stratum=%u offset_ms=%.6f "
        "last_offset_ms=%.6f rms_offset_ms=%.6f source=%s leap=%s "
        "detail=%s\n",
        state_name(response.state),
        response.generation,
        response.stratum,
        response.system_offset_ms,
        response.last_offset_ms,
        response.rms_offset_ms,
        response.source,
        response.leap_status,
        response.detail);
    return response.state == TIME_SYNC_SYNCED ? 0 : 2;
}

/* =========================================================================
 * #### 프로세스 진입점과 일관된 종료
 * ========================================================================= */

/**
 * @brief SIGINT와 SIGTERM에 공통 종료 handler를 설치함.
 * @return 두 handler가 모두 설치되면 0, 실패하면 -1.
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
 * @brief 기본 실행과 check/status, 이전 --옵션 호환 사용법을 출력함.
 * @return 반환값 없음.
 */
static void print_usage(const char *program)
{
    fprintf(
        stderr,
        "사용법:\n"
        "  %s                 # 정식 장기 실행\n"
        "  %s check           # 구성과 IPC 규약 점검\n"
        "  %s status          # 실행 중 프로세스에 상태 요청\n"
        "\n호환 별칭: --run, --check, --status\n",
        program, program, program);
}

/**
 * @brief CLI 분기와 단일 애플리케이션 프로세스 업무 3스레드 수명주기를 총괄함.
 * @return 성공 0, 실행/IPC 실패 1, 비동기 상태 또는 사용법 오류 2.
 * @note chronyd는 별도 OS 서비스이며 이 프로세스가 시스템 시각을 직접 바꾸지 않음.
 */
int main(int argc, char **argv)
{
    time_sync_runtime_t runtime;
    monitor_args_t monitor_args;
    pthread_t monitor_thread;
    pthread_t state_thread;
    sigset_t worker_signal_mask;
    sigset_t original_signal_mask;
    int state_created = 0;
    int monitor_created = 0;
    int signals_blocked = 0;
    int result = 1;

    if (argc == 2 &&
        (strcmp(argv[1], "check") == 0 ||
         strcmp(argv[1], "--check") == 0)) {
        printf(
            "time_sync check: OK\n"
            "application_processes=1\n"
            "worker_threads=3\nwatchdog=systemd_progress\n"
            "roles=ipc,monitor,state\n"
            "external_ipc_sockets=1\n"
            "socket=%s\n"
            "protocol_version=%u\n"
            "internal_transport=bounded_mutex_condition_queue\n"
            "chrony_interface=chronyc_tracking\n",
            TIME_SYNC_SOCKET_PATH,
            TIME_SYNC_PROTOCOL_VERSION);
        return 0;
    }
    if (argc == 2 &&
        (strcmp(argv[1], "status") == 0 ||
         strcmp(argv[1], "--status") == 0)) {
        return request_status();
    }

    /* 인자 없는 실행을 운영 계약으로 사용한다. 기존 시험 스크립트를 깨지 않도록
     * --run은 같은 장기 실행 경로로 들어오는 호환 별칭으로만 유지한다. */
    if (argc != 1 &&
        !(argc == 2 && strcmp(argv[1], "--run") == 0)) {
        print_usage(argv[0]);
        return 2;
    }

    if (install_signal_handlers() != 0) {
        perror("sigaction");
        return 1;
    }
    signal(SIGPIPE, SIG_IGN);

    if (runtime_init(&runtime) != 0) {
        fprintf(stderr, "runtime initialization failed\n");
        return 1;
    }
    memset(&monitor_args, 0, sizeof(monitor_args));
    monitor_args.runtime = &runtime;
    monitor_args.interval_ms = configured_interval_ms();
    monitor_args.max_offset_ms = configured_max_offset_ms();
    sample_deadline_ms = monitor_args.interval_ms + TIME_SYNC_CHRONYC_TIMEOUT_MS + 5000U;

    /* SIGINT/SIGTERM은 메인 IPC 스레드만 받아 IPC 대기를 깨우게 함. */
    sigemptyset(&worker_signal_mask);
    sigaddset(&worker_signal_mask, SIGINT);
    sigaddset(&worker_signal_mask, SIGTERM);
    if (pthread_sigmask(
            SIG_BLOCK,
            &worker_signal_mask,
            &original_signal_mask) != 0) {
        fprintf(stderr, "signal mask setup failed\n");
        goto cleanup;
    }
    signals_blocked = 1;
    {
        const char *const slots[] = {"ipc", "chronyc", "state"};
        if (ace_watchdog_start(&process_watchdog, slots, 3U) != 0) {
            perror("time_sync watchdog start");
            goto cleanup;
        }
        ace_watchdog_progress(&process_watchdog, WATCHDOG_STATE, sample_deadline_ms);
    }

    if (pthread_create(
            &state_thread, NULL, state_thread_main, &runtime) != 0) {
        fprintf(stderr, "State thread creation failed\n");
        goto cleanup;
    }
    state_created = 1;

    if (pthread_create(
            &monitor_thread, NULL, monitor_thread_main, &monitor_args) != 0) {
        fprintf(stderr, "Monitor thread creation failed\n");
        goto cleanup;
    }
    monitor_created = 1;

    if (pthread_sigmask(SIG_SETMASK, &original_signal_mask, NULL) != 0) {
        fprintf(stderr, "main signal mask restore failed\n");
        goto cleanup;
    }
    signals_blocked = 0;

    fprintf(
        stderr,
        "level=INFO component=time_sync event=PROCESS_START "
        "pid=%ld application_processes=1 worker_threads=3 interval_ms=%u "
        "max_offset_ms=%.3f queue_capacity=%u\n",
        (long)getpid(),
        monitor_args.interval_ms,
        monitor_args.max_offset_ms,
        TIME_SYNC_QUEUE_CAPACITY);

    result = run_ipc_server(&runtime) == 0 ? 0 : 1;

cleanup:
    if (signals_blocked != 0) {
        pthread_sigmask(SIG_SETMASK, &original_signal_mask, NULL);
    }
    runtime_stop(&runtime);
    if (monitor_created != 0) {
        pthread_join(monitor_thread, NULL);
    }
    if (state_created != 0) {
        pthread_join(state_thread, NULL);
    }
    unlink(TIME_SYNC_SOCKET_PATH);
    ace_watchdog_stop(&process_watchdog);
    runtime_destroy(&runtime);

    fprintf(
        stderr,
        "level=INFO component=time_sync event=PROCESS_STOP "
        "reason=%s threads_joined=true cleanup=complete\n",
        stop_requested != 0 ? "signal" : "server_return");
    return result;
}
