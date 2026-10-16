#ifndef ACE_AI_RUNTIME_SUPPORT_H
#define ACE_AI_RUNTIME_SUPPORT_H
#include "../ipc/runtime_watchdog.h"
#include <poll.h>
enum ai_watchdog_slot { AI_CAPTURE, AI_RECOVERY, AI_RECEIVER, AI_WORKER, AI_HANDOFF, AI_LED };
static ace_watchdog_t ai_watchdog;
static const char *const ai_watchdog_names[] = {
    "camera ring", "camera recovery", "event receiver", "video worker", "evidence handoff", "LED worker"
};
static inline void ai_progress(unsigned slot)
{
    ace_watchdog_progress(&ai_watchdog, slot, 30000);
}
static inline void ai_handoff_progress() { ai_progress(AI_HANDOFF); }
/* Recovery scanning executes on the video worker before handoff starts. */
static inline void ai_restore_progress() { ai_progress(AI_WORKER); }
static inline struct timespec ai_idle_deadline()
{
    struct timespec deadline = {};
    clock_gettime(CLOCK_MONOTONIC, &deadline);
    ++deadline.tv_sec;
    return deadline;
}
/* Listener idle is unlimited. An accepted client has five seconds to send its
 * complete header/payload, including the wait for the first byte. */
static inline int ai_receive_event(int fd, ipc_header_t *header, void *payload,
                                   size_t capacity, int shutdown_fd,
                                   int (*termination_requested)())
{
    const uint64_t deadline = ace_watchdog_now_ns() + UINT64_C(5000000000);
    auto receive_exact = [&](void *output, size_t size) {
        size_t received = 0;
        while (received < size) {
            uint64_t now = ace_watchdog_now_ns();
            if (termination_requested() || now == 0 || now >= deadline)
                return IPC_ERROR_RECV;
            ssize_t count = recv(fd, static_cast<char *>(output) + received,
                                 size - received, MSG_DONTWAIT);
            if (count > 0) {
                received += static_cast<size_t>(count);
                ai_progress(AI_RECEIVER);
                continue;
            }
            if (count == 0)
                return IPC_CONNECTION_CLOSED;
            if (errno == EINTR)
                continue;
            if (errno != EAGAIN && errno != EWOULDBLOCK)
                return IPC_ERROR_RECV;
            struct pollfd descriptors[2] = {{fd, POLLIN, 0}, {shutdown_fd, POLLIN, 0}};
            int remaining_ms = static_cast<int>((deadline - now + 999999) / 1000000);
            int result = poll(descriptors, 2, remaining_ms > 1000 ? 1000 : remaining_ms);
            if (result < 0 && errno == EINTR)
                continue;
            if (result < 0 || descriptors[1].revents != 0 ||
                (descriptors[0].revents & (POLLERR | POLLNVAL)))
                return IPC_ERROR_RECV;
            ai_progress(AI_RECEIVER);
        }
        return IPC_SUCCESS;
    };
    int result = receive_exact(header, sizeof(*header));
    if (result != IPC_SUCCESS)
        return result;
    if (header->length > capacity)
        return IPC_ERROR_INVALID_LENGTH;
    return receive_exact(payload, header->length);
}
#endif