/* 파일 역할: 주요 작업자의 진행 기한을 감시하고 systemd watchdog에 서비스 상태를 알린다.
 * 각 작업자는 자기 슬롯의 단조 증가 기한을 갱신한다. 하나라도 멈추면 알림을 중단한다.
 * pthread·원자 연산·Unix datagram을 사용하며 libsystemd 연결 없이 동작한다.
 */
#ifndef ACE_RUNTIME_WATCHDOG_H
#define ACE_RUNTIME_WATCHDOG_H

/* Critical workers renew their own monotonic deadline. Only healthy workers
 * permit systemd watchdog notifications. No libsystemd dependency is required. */
#include <errno.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <pthread.h>
#include <sys/socket.h>
#include <sys/un.h>
#include <time.h>
#include <unistd.h>

#define ACE_WATCHDOG_MAX_SLOTS 16U

typedef struct {
    uint64_t deadline_ns;
    const char *name;
} ace_watchdog_slot_t;

typedef struct {
    ace_watchdog_slot_t slots[ACE_WATCHDOG_MAX_SLOTS];
    pthread_t thread;
    unsigned count;
    int stop;
    int started;
    uint64_t interval_ns;
} ace_watchdog_t;

/*
 * 기능: 감시용 단조 증가 시각을 읽는다.
 * 입력: 없음.
 * 출력: 나노초 시각 또는 시계 조회 실패 시 0.
 * 구조·이유: CLOCK_MONOTONIC으로 시스템 시각 보정과 독립적으로 작업자 기한을 비교한다.
 */
static inline uint64_t ace_watchdog_now_ns(void)
{
    struct timespec now;
    if (clock_gettime(CLOCK_MONOTONIC, &now) != 0)
        return 0;
    return (uint64_t)now.tv_sec * UINT64_C(1000000000) + (uint64_t)now.tv_nsec;
}

/*
 * 기능: 작업자 슬롯의 다음 진행 기한을 갱신한다.
 * 입력: watchdog, 슬롯 번호 slot, 허용 지연 timeout_ms.
 * 출력: 원자적으로 갱신된 deadline_ns; 반환값 없음.
 * 구조·이유: 현재 시각에 제한 시간을 더하고 정수 오버플로는 포화 처리한다. 슬롯 범위를 벗어나면 무시한다.
 */
static inline void ace_watchdog_progress(ace_watchdog_t *watchdog,
                                          unsigned slot, uint64_t timeout_ms)
{
    uint64_t now, duration;
    if (slot >= ACE_WATCHDOG_MAX_SLOTS)
        return;
    now = ace_watchdog_now_ns();
    duration = timeout_ms > UINT64_MAX / UINT64_C(1000000)
             ? UINT64_MAX : timeout_ms * UINT64_C(1000000);
    __atomic_store_n(&watchdog->slots[slot].deadline_ns,
                     duration > UINT64_MAX - now ? UINT64_MAX : now + duration,
                     __ATOMIC_RELEASE);
}

/*
 * 기능: systemd의 NOTIFY_SOCKET으로 상태 메시지를 전송한다.
 * 입력: READY=1·WATCHDOG=1 등의 message와 환경 변수 NOTIFY_SOCKET.
 * 출력: 성공 또는 미설정 시 0, 경로·소켓·송신 오류 시 -1.
 * 구조·이유: 파일 시스템·추상 Unix 주소를 모두 처리하며 임시 datagram fd는 송신 후 닫는다.
 */
static inline int ace_watchdog_notify(const char *message)
{
    const char *path = getenv("NOTIFY_SOCKET");
    struct sockaddr_un address;
    size_t length;
    int fd, result, saved_errno;
    if (path == NULL || *path == '\0')
        return 0;
    length = strlen(path);
    if (length >= sizeof(address.sun_path) ||
        (path[0] != '/' && path[0] != '@')) {
        errno = EINVAL;
        return -1;
    }
    memset(&address, 0, sizeof(address));
    address.sun_family = AF_UNIX;
    memcpy(address.sun_path, path, length + 1U);
    if (path[0] == '@')
        address.sun_path[0] = '\0';
    fd = socket(AF_UNIX, SOCK_DGRAM | SOCK_CLOEXEC | SOCK_NONBLOCK, 0);
    if (fd < 0)
        return -1;
    result = sendto(fd, message, strlen(message), MSG_NOSIGNAL,
                    (struct sockaddr *)&address,
                    (socklen_t)(offsetof(struct sockaddr_un, sun_path) +
                    length + (path[0] == '@' ? 0U : 1U))) < 0 ? -1 : 0;
    saved_errno = errno;
    close(fd);
    errno = saved_errno;
    return result;
}

/*
 * 기능: 모든 작업자의 진행 기한을 주기적으로 검사한다.
 * 입력: ace_watchdog_t를 가리키는 스레드 argument.
 * 출력: 종료 시 NULL; 정상 주기마다 WATCHDOG=1 전송.
 * 구조·이유: 하나라도 기한을 넘기면 스레드를 종료해 알림을 중단한다. 다른 작업자의 진행이 정체 상태를 숨기지 못하게 한다.
 */
static inline void *ace_watchdog_thread(void *argument)
{
    ace_watchdog_t *watchdog = (ace_watchdog_t *)argument;
    while (!__atomic_load_n(&watchdog->stop, __ATOMIC_ACQUIRE)) {
        uint64_t now = ace_watchdog_now_ns();
        unsigned slot;
        struct timespec delay;
        for (slot = 0; slot < watchdog->count; ++slot) {
            uint64_t deadline = __atomic_load_n(
                &watchdog->slots[slot].deadline_ns, __ATOMIC_ACQUIRE);
            if (now == 0 || now >= deadline) {
                fprintf(stderr, "watchdog: %s progress stalled; restarting via systemd\n",
                        watchdog->slots[slot].name);
                /* Latch failure: another worker cannot hide the stall. */
                return NULL;
            }
        }
        (void)ace_watchdog_notify("WATCHDOG=1");
        delay.tv_sec = (time_t)(watchdog->interval_ns / UINT64_C(1000000000));
        delay.tv_nsec = (long)(watchdog->interval_ns % UINT64_C(1000000000));
        while (nanosleep(&delay, &delay) != 0 && errno == EINTR) {
            if (__atomic_load_n(&watchdog->stop, __ATOMIC_ACQUIRE))
                return NULL;
        }
    }
    return NULL;
}

/* Call once before worker creation. Slot names must outlive the watchdog.
 * The first progress update replaces the 60-second initialization allowance. */
/*
 * 기능: 슬롯을 초기화하고 systemd 감시 알림 스레드를 시작한다.
 * 입력: watchdog, 수명이 충분한 작업자 이름 배열 names, 슬롯 수 count.
 * 출력: 성공 0, 설정·스레드·알림 오류 -1.
 * 구조·이유: 초기 60초 유예를 설정하고 WATCHDOG_PID/USEC를 검사한다. 감시 미설정 시 READY만 보내며 작업자 생성 전에 한 번 호출한다.
 */
static inline int ace_watchdog_start(ace_watchdog_t *watchdog,
                                      const char *const names[], unsigned count)
{
    const char *usec_text = getenv("WATCHDOG_USEC");
    const char *pid_text = getenv("WATCHDOG_PID");
    char *end;
    unsigned long long usec;
    unsigned slot;
    int error;
    if (count == 0 || count > ACE_WATCHDOG_MAX_SLOTS) {
        errno = EINVAL;
        return -1;
    }
    watchdog->started = 0;
    watchdog->count = count;
    __atomic_store_n(&watchdog->stop, 0, __ATOMIC_RELEASE);
    for (slot = 0; slot < count; ++slot) {
        watchdog->slots[slot].name = names[slot];
        ace_watchdog_progress(watchdog, slot, UINT64_C(60000));
    }
    if (pid_text != NULL && *pid_text != '\0') {
        long pid;
        errno = 0;
        pid = strtol(pid_text, &end, 10);
        if (errno != 0 || *end != '\0' || pid <= 0 || pid != (long)getpid())
            return 0;
    }
    if (usec_text == NULL || *usec_text == '\0')
        return ace_watchdog_notify("READY=1");
    errno = 0;
    usec = strtoull(usec_text, &end, 10);
    if (errno != 0 || *end != '\0' || usec_text[0] == '-' || usec == 0 ||
        getenv("NOTIFY_SOCKET") == NULL) {
        errno = EINVAL;
        return -1;
    }
    /* Notify at least three times per systemd window; stop joins within 500ms. */
    watchdog->interval_ns = usec > UINT64_C(1500000)
        ? UINT64_C(500000000) : (uint64_t)usec * UINT64_C(1000) / 3U;
    error = pthread_create(&watchdog->thread, NULL, ace_watchdog_thread, watchdog);
    if (error != 0) {
        errno = error;
        return -1;
    }
    watchdog->started = 1;
    if (ace_watchdog_notify("READY=1") != 0) {
        __atomic_store_n(&watchdog->stop, 1, __ATOMIC_RELEASE);
        pthread_join(watchdog->thread, NULL);
        watchdog->started = 0;
        return -1;
    }
    return 0;
}

/*
 * 기능: 감시 스레드를 종료하고 자원을 회수한다.
 * 입력: 초기화된 watchdog.
 * 출력: stop·started 상태 갱신; 반환값 없음.
 * 구조·이유: 원자적으로 종료를 요청한 뒤 pthread_join한다. 미시작 상태에서는 아무 작업도 하지 않는다.
 */
static inline void ace_watchdog_stop(ace_watchdog_t *watchdog)
{
    if (watchdog->started) {
        __atomic_store_n(&watchdog->stop, 1, __ATOMIC_RELEASE);
        pthread_join(watchdog->thread, NULL);
        watchdog->started = 0;
    }
}
#endif
