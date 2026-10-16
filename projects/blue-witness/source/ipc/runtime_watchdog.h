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

static inline uint64_t ace_watchdog_now_ns(void)
{
    struct timespec now;
    if (clock_gettime(CLOCK_MONOTONIC, &now) != 0)
        return 0;
    return (uint64_t)now.tv_sec * UINT64_C(1000000000) + (uint64_t)now.tv_nsec;
}

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

static inline void ace_watchdog_stop(ace_watchdog_t *watchdog)
{
    if (watchdog->started) {
        __atomic_store_n(&watchdog->stop, 1, __ATOMIC_RELEASE);
        pthread_join(watchdog->thread, NULL);
        watchdog->started = 0;
    }
}
#endif
