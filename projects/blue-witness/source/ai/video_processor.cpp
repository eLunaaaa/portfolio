/* 파일 역할: 영상 프레임의 수집·버퍼링과 사건 구간 영상 처리를 담당하는 팀 공통 모듈. 프레임 풀·링 버퍼·작업 스레드를 연결한다. */
/* 사고차량용 영상 처리 프로세스: AI 추론 없이 사고 영상을 저장·전송한다. */
#include <libcamera/control_ids.h>
#include <libcamera/formats.h>
#include <libcamera/libcamera.h>
#include <libcamera/orientation.h>
#include <linux/dma-buf.h>

#define _Static_assert static_assert
extern "C" {
#include "ipc.h"
#include "protocol.h"
#include "video_processor_protocol.h"
#include "time_sync_protocol.h"
#include "uploader_ipc_protocol.h"
}
#undef _Static_assert

extern "C" {
#include <libavcodec/avcodec.h>
#include <libavformat/avformat.h>
#include <libavutil/dict.h>
#include <libavutil/error.h>
}

#include <atomic>
#include <chrono>
#include <condition_variable>
#include <list>
#include <mutex>
#include <thread>
#include <sys/socket.h>
#include <sys/un.h>
#include <cerrno>
#include <cinttypes>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <exception>
#include <fcntl.h>
#include <memory>
#include <poll.h>
#include <pthread.h>
#include <signal.h>
#include <string>
#include <sys/ioctl.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <time.h>
#include <unistd.h>
#include <vector>
#include "runtime_support.h"
#include "evidence_handoff.h"

#define CAMERA_WIDTH 640U
#define CAMERA_HEIGHT 480U
#define CAMERA_FRAME_RATE 30U
#define NV12_FRAME_SIZE (CAMERA_WIDTH * CAMERA_HEIGHT * 3U / 2U)
#define RING_CAPACITY 640U
#define SNAPSHOT_POOL_RESERVE 320U
#define FRAME_POOL_CAPACITY (RING_CAPACITY + SNAPSHOT_POOL_RESERVE)
#define RING_RETENTION_NS 20000000000LL
#define ACCIDENT_VIDEO_NS 10000000000LL
#define ACCIDENT_VIDEO_BEFORE_NS 8000000000LL
#define ACCIDENT_VIDEO_AFTER_NS 2000000000LL
#define FRAME_COVERAGE_TOLERANCE_NS 500000000LL
#define EVENT_QUEUE_CAPACITY 4U
#define H264_BIT_RATE 2500000LL
#define OUTPUT_DIRECTORY "recordings"
#define TIME_SYNC_SOCKET_PATH "/tmp/time-sync.sock"
#define CAMERA_WATCHDOG_TIMEOUT_MS 1000U
#define CAMERA_WATCHDOG_CHECK_MS 200U
#define ITS_EPOCH_UNIX_MS 1072915200000LL
#define ITS_LEAP_SECONDS_MS 5000LL

static volatile sig_atomic_t shutdown_requested = 0;
static std::atomic_bool runtime_failure_requested(false);
static int shutdown_pipe[2] = {-1, -1};
static int event_server_fd = -1;

struct accident_event {
    uint32_t station_id;
    uint16_t sequence_number;
    int64_t detection_time_ns;
};

struct frame_buffer_storage {
    uint8_t *data;
    size_t refcount;
};

struct frame_pool {
    struct frame_buffer_storage *buffers;
    struct frame_buffer_storage **free_buffers;
    size_t capacity;
    size_t free_count;
};

struct frame_slot {
    struct frame_buffer_storage *storage;
    int64_t timestamp_ns;
    int valid;
};

struct frame_view {
    const uint8_t *data;
    size_t size;
    int64_t timestamp_ns;
    struct frame_buffer_storage *storage;
};

struct video_snapshot {
    struct frame_view *frames;
    size_t count;
};

struct ring_buffer {
    struct frame_slot *slots;
    struct frame_pool pool;
    size_t capacity;
    size_t head;
    size_t count;
    pthread_mutex_t mutex;
    pthread_cond_t frame_available;
};

struct event_queue {
    struct accident_event events[EVENT_QUEUE_CAPACITY];
    size_t head;
    size_t tail;
    size_t count;
    int closed;
    pthread_mutex_t mutex;
    pthread_cond_t available;
};

struct worker_context {
    struct event_queue *queue;
    struct ring_buffer *ring;
    const char *vehicle_id;
};

static int termination_requested(void)
{
    return shutdown_requested || runtime_failure_requested.load();
}

static void notify_shutdown_pipe(void)
{
    uint8_t byte = 1;
    if (shutdown_pipe[1] >= 0) {
        ssize_t write_result = write(shutdown_pipe[1], &byte, sizeof(byte));

        (void)write_result;
    }
}

static void request_runtime_failure(void)
{
    runtime_failure_requested.store(true);
    notify_shutdown_pipe();
}

static void shutdown_signal_handler(int signal_number)
{
    int saved_errno = errno;
    (void)signal_number;
    shutdown_requested = 1;
    notify_shutdown_pipe();
    errno = saved_errno;
}

static int shutdown_signal_init(void)
{
    struct sigaction action;
    int flags;

    if (pipe(shutdown_pipe) != 0)
        return -1;
    flags = fcntl(shutdown_pipe[0], F_GETFL, 0);
    if (flags < 0 || fcntl(shutdown_pipe[0], F_SETFL, flags | O_NONBLOCK) != 0)
        goto fail;
    flags = fcntl(shutdown_pipe[1], F_GETFL, 0);
    if (flags < 0 || fcntl(shutdown_pipe[1], F_SETFL, flags | O_NONBLOCK) != 0)
        goto fail;

    memset(&action, 0, sizeof(action));
    action.sa_handler = shutdown_signal_handler;
    sigemptyset(&action.sa_mask);
    if (sigaction(SIGINT, &action, NULL) != 0 ||
        sigaction(SIGTERM, &action, NULL) != 0)
        goto fail;
    return 0;

fail:
    perror("shutdown signal 초기화");
    close(shutdown_pipe[0]);
    close(shutdown_pipe[1]);
    shutdown_pipe[0] = -1;
    shutdown_pipe[1] = -1;
    return -1;
}

static void shutdown_signal_destroy(void)
{
    sigset_t signals;
    struct sigaction action;

    sigemptyset(&signals);
    sigaddset(&signals, SIGINT);
    sigaddset(&signals, SIGTERM);
    pthread_sigmask(SIG_BLOCK, &signals, NULL);
    memset(&action, 0, sizeof(action));
    action.sa_handler = SIG_DFL;
    sigemptyset(&action.sa_mask);
    sigaction(SIGINT, &action, NULL);
    sigaction(SIGTERM, &action, NULL);
    close(shutdown_pipe[0]);
    close(shutdown_pipe[1]);
    shutdown_pipe[0] = -1;
    shutdown_pipe[1] = -1;
}

static int frame_pool_init(struct frame_pool *pool, size_t capacity)
{
    size_t i;
    memset(pool, 0, sizeof(*pool));
    pool->buffers = (struct frame_buffer_storage *)calloc(
        capacity, sizeof(*pool->buffers));
    pool->free_buffers = (struct frame_buffer_storage **)calloc(
        capacity, sizeof(*pool->free_buffers));
    if (pool->buffers == NULL || pool->free_buffers == NULL)
        goto fail;
    pool->capacity = capacity;
    for (i = 0; i < capacity; ++i) {
        pool->buffers[i].data = (uint8_t *)malloc(NV12_FRAME_SIZE);
        if (pool->buffers[i].data == NULL)
            goto fail;
        pool->free_buffers[i] = &pool->buffers[i];
    }
    pool->free_count = capacity;
    return 0;

fail:
    if (pool->buffers != NULL)
        for (i = 0; i < capacity; ++i)
            free(pool->buffers[i].data);
    free(pool->free_buffers);
    free(pool->buffers);
    memset(pool, 0, sizeof(*pool));
    return -1;
}

static void frame_pool_destroy(struct frame_pool *pool)
{
    for (size_t i = 0; i < pool->capacity; ++i)
        free(pool->buffers[i].data);
    free(pool->free_buffers);
    free(pool->buffers);
    memset(pool, 0, sizeof(*pool));
}

static struct frame_buffer_storage *
frame_pool_acquire_locked(struct frame_pool *pool)
{
    struct frame_buffer_storage *storage;
    if (pool->free_count == 0)
        return NULL;
    storage = pool->free_buffers[pool->free_count - 1];
    if (storage == NULL || storage->refcount != 0)
        return NULL;
    --pool->free_count;
    storage->refcount = 1;
    return storage;
}

static int frame_pool_add_reference_locked(struct frame_buffer_storage *storage)
{
    if (storage == NULL || storage->refcount == 0 ||
        storage->refcount == SIZE_MAX)
        return -1;
    ++storage->refcount;
    return 0;
}

static int frame_pool_release_locked(struct frame_pool *pool,
                                     struct frame_buffer_storage *storage)
{
    if (storage == NULL || storage->refcount == 0 ||
        (storage->refcount == 1 && pool->free_count == pool->capacity))
        return -1;
    --storage->refcount;
    if (storage->refcount == 0)
        pool->free_buffers[pool->free_count++] = storage;
    return 0;
}

static int ring_init(struct ring_buffer *ring, size_t capacity)
{
    pthread_condattr_t attribute;
    size_t i;

    memset(ring, 0, sizeof(*ring));
    ring->slots = (struct frame_slot *)calloc(capacity, sizeof(*ring->slots));
    if (ring->slots == NULL)
        return -1;
    ring->capacity = capacity;
    if (frame_pool_init(&ring->pool, FRAME_POOL_CAPACITY) != 0)
        goto fail;
    for (i = 0; i < capacity; ++i) {
        ring->slots[i].storage = frame_pool_acquire_locked(&ring->pool);
        if (ring->slots[i].storage == NULL)
            goto fail;
    }
    if (pthread_mutex_init(&ring->mutex, NULL) != 0)
        goto fail;
    if (pthread_condattr_init(&attribute) != 0) {
        pthread_mutex_destroy(&ring->mutex);
        goto fail;
    }
    if (pthread_condattr_setclock(&attribute, CLOCK_MONOTONIC) != 0 ||
        pthread_cond_init(&ring->frame_available, &attribute) != 0) {
        pthread_condattr_destroy(&attribute);
        pthread_mutex_destroy(&ring->mutex);
        goto fail;
    }
    pthread_condattr_destroy(&attribute);
    return 0;

fail:
    frame_pool_destroy(&ring->pool);
    free(ring->slots);
    memset(ring, 0, sizeof(*ring));
    return -1;
}

static void ring_destroy(struct ring_buffer *ring)
{
    if (ring->slots == NULL)
        return;
    pthread_cond_destroy(&ring->frame_available);
    pthread_mutex_destroy(&ring->mutex);
    frame_pool_destroy(&ring->pool);
    free(ring->slots);
    memset(ring, 0, sizeof(*ring));
}

static int ring_push(struct ring_buffer *ring, const uint8_t *data,
                     size_t size, int64_t timestamp_ns)
{
    struct frame_slot *slot;
    struct frame_buffer_storage *replacement;

    if (data == NULL || size != NV12_FRAME_SIZE)
        return -1;
    pthread_mutex_lock(&ring->mutex);
    while (ring->count > 0) {
        size_t oldest = (ring->head + ring->capacity - ring->count) %
                        ring->capacity;
        if (timestamp_ns - ring->slots[oldest].timestamp_ns <= RING_RETENTION_NS)
            break;
        ring->slots[oldest].valid = 0;
        --ring->count;
    }
    slot = &ring->slots[ring->head];
    if (slot->storage->refcount > 1) {
        replacement = frame_pool_acquire_locked(&ring->pool);
        if (replacement == NULL ||
            frame_pool_release_locked(&ring->pool, slot->storage) != 0) {
            if (replacement != NULL)
                (void)frame_pool_release_locked(&ring->pool, replacement);
            pthread_mutex_unlock(&ring->mutex);
            fprintf(stderr, "frame pool free buffer가 부족함\n");
            return -1;
        }
        slot->storage = replacement;
    }
    memcpy(slot->storage->data, data, size);
    slot->timestamp_ns = timestamp_ns;
    slot->valid = 1;
    ring->head = (ring->head + 1) % ring->capacity;
    if (ring->count < ring->capacity)
        ++ring->count;
    pthread_cond_signal(&ring->frame_available);
    pthread_mutex_unlock(&ring->mutex);
    return 0;
}

static int ring_wait_for_snapshot_end(struct ring_buffer *ring,
                                      int64_t detection_time_ns,
                                      int64_t *end_ns,
                                      int64_t wait_ns = FRAME_COVERAGE_TOLERANCE_NS)
{
    struct timespec deadline;
    int result = -1;

    if (clock_gettime(CLOCK_MONOTONIC, &deadline) != 0)
        return -1;
    deadline.tv_sec += wait_ns / 1000000000LL;
    deadline.tv_nsec += wait_ns % 1000000000LL;
    if (deadline.tv_nsec >= 1000000000L) {
        ++deadline.tv_sec;
        deadline.tv_nsec -= 1000000000L;
    }
    pthread_mutex_lock(&ring->mutex);
    while (!termination_requested()) {
        size_t oldest = (ring->head + ring->capacity - ring->count) %
                        ring->capacity;
        for (size_t i = 0; i < ring->count; ++i) {
            const struct frame_slot *slot =
                &ring->slots[(oldest + i) % ring->capacity];
            if (!slot->valid || slot->timestamp_ns <= detection_time_ns)
                continue;
            if (slot->timestamp_ns - detection_time_ns <=
                FRAME_COVERAGE_TOLERANCE_NS) {
                *end_ns = slot->timestamp_ns;
                result = 0;
            }
            goto done;
        }
        if (pthread_cond_timedwait(&ring->frame_available, &ring->mutex,
                                   &deadline) != 0)
            goto done;
    }
done:
    pthread_mutex_unlock(&ring->mutex);
    return result;
}

static void ring_wake_waiters(struct ring_buffer *ring)
{
    pthread_mutex_lock(&ring->mutex);
    pthread_cond_broadcast(&ring->frame_available);
    pthread_mutex_unlock(&ring->mutex);
}

static int ring_range_is_complete_locked(const struct ring_buffer *ring,
                                         int64_t begin_ns, int64_t end_ns,
                                         size_t *first_out, size_t *count_out)
{
    size_t oldest = (ring->head + ring->capacity - ring->count) %
                    ring->capacity;
    size_t first = 0;
    size_t selected = 0;
    int64_t previous = 0;

    *first_out = 0;
    *count_out = 0;
    for (size_t i = 0; i < ring->count; ++i) {
        const struct frame_slot *slot =
            &ring->slots[(oldest + i) % ring->capacity];
        if (!slot->valid || slot->timestamp_ns < begin_ns ||
            slot->timestamp_ns > end_ns)
            continue;
        if (selected == 0) {
            first = i;
            if (slot->timestamp_ns > begin_ns + FRAME_COVERAGE_TOLERANCE_NS)
                return 0;
        } else if (slot->timestamp_ns - previous >
                   FRAME_COVERAGE_TOLERANCE_NS) {
            return 0;
        }
        previous = slot->timestamp_ns;
        ++selected;
    }
    if (selected == 0 || previous < end_ns - FRAME_COVERAGE_TOLERANCE_NS)
        return 0;
    *first_out = first;
    *count_out = selected;
    return 1;
}

static int ring_copy_snapshot(struct ring_buffer *ring,
                              int64_t begin_ns, int64_t end_ns,
                              struct video_snapshot *snapshot)
{
    size_t first;
    size_t count;
    size_t oldest;
    size_t i;

    memset(snapshot, 0, sizeof(*snapshot));
    pthread_mutex_lock(&ring->mutex);
    if (!ring_range_is_complete_locked(ring, begin_ns, end_ns,
                                       &first, &count)) {
        pthread_mutex_unlock(&ring->mutex);
        return -1;
    }
    if (count > SIZE_MAX / sizeof(*snapshot->frames)) {
        pthread_mutex_unlock(&ring->mutex);
        return -1;
    }
    snapshot->frames = (struct frame_view *)calloc(
        count, sizeof(*snapshot->frames));
    if (snapshot->frames == NULL) {
        pthread_mutex_unlock(&ring->mutex);
        return -1;
    }
    oldest = (ring->head + ring->capacity - ring->count) % ring->capacity;
    for (i = 0; i < count; ++i) {
        const struct frame_slot *slot =
            &ring->slots[(oldest + first + i) % ring->capacity];
        if (frame_pool_add_reference_locked(slot->storage) != 0) {
            while (i > 0) {
                --i;
                (void)frame_pool_release_locked(
                    &ring->pool, snapshot->frames[i].storage);
            }
            free(snapshot->frames);
            memset(snapshot, 0, sizeof(*snapshot));
            pthread_mutex_unlock(&ring->mutex);
            return -1;
        }
        snapshot->frames[i].data = slot->storage->data;
        snapshot->frames[i].size = NV12_FRAME_SIZE;
        snapshot->frames[i].timestamp_ns = slot->timestamp_ns;
        snapshot->frames[i].storage = slot->storage;
    }
    snapshot->count = count;
    pthread_mutex_unlock(&ring->mutex);
    return 0;
}

static void snapshot_destroy(struct ring_buffer *ring,
                             struct video_snapshot *snapshot)
{
    pthread_mutex_lock(&ring->mutex);
    for (size_t i = 0; i < snapshot->count; ++i)
        if (frame_pool_release_locked(&ring->pool,
                                      snapshot->frames[i].storage) != 0)
            fprintf(stderr, "snapshot frame refcount 반환 실패\n");
    pthread_mutex_unlock(&ring->mutex);
    free(snapshot->frames);
    memset(snapshot, 0, sizeof(*snapshot));
}

static int event_queue_init(struct event_queue *queue)
{
    memset(queue, 0, sizeof(*queue));
    if (pthread_mutex_init(&queue->mutex, NULL) != 0)
        return -1;
    pthread_condattr_t attribute;
    if (pthread_condattr_init(&attribute) != 0) {
        pthread_mutex_destroy(&queue->mutex);
        return -1;
    }
    int error = pthread_condattr_setclock(&attribute, CLOCK_MONOTONIC);
    if (error == 0)
        error = pthread_cond_init(&queue->available, &attribute);
    pthread_condattr_destroy(&attribute);
    if (error != 0) {
        pthread_mutex_destroy(&queue->mutex);
        return -1;
    }
    return 0;
}

static void event_queue_close(struct event_queue *queue)
{
    pthread_mutex_lock(&queue->mutex);
    queue->closed = 1;
    pthread_cond_broadcast(&queue->available);
    pthread_mutex_unlock(&queue->mutex);
}

static int event_queue_push(struct event_queue *queue,
                            const struct accident_event *event)
{
    struct accident_event dropped;
    int event_dropped = 0;

    pthread_mutex_lock(&queue->mutex);
    if (queue->closed) {
        pthread_mutex_unlock(&queue->mutex);
        return -1;
    }
    if (queue->count == EVENT_QUEUE_CAPACITY) {
        dropped = queue->events[queue->head];
        queue->head = (queue->head + 1) % EVENT_QUEUE_CAPACITY;
        --queue->count;
        event_dropped = 1;
    }
    queue->events[queue->tail] = *event;
    queue->tail = (queue->tail + 1) % EVENT_QUEUE_CAPACITY;
    ++queue->count;
    pthread_cond_signal(&queue->available);
    pthread_mutex_unlock(&queue->mutex);

    if (event_dropped)
        fprintf(stderr,
                "event queue full: oldest event drop "
                "(station=%" PRIu32 ", sequence=%" PRIu16 ")\n",
                dropped.station_id, dropped.sequence_number);
    return 0;
}

static int event_queue_pop(struct event_queue *queue,
                           struct accident_event *event)
{
    pthread_mutex_lock(&queue->mutex);
    ai_progress(AI_WORKER);
    while (queue->count == 0 && !queue->closed) {
        struct timespec deadline = ai_idle_deadline();
        int error = pthread_cond_timedwait(&queue->available, &queue->mutex, &deadline);
        if (error != 0 && error != ETIMEDOUT) {
            pthread_mutex_unlock(&queue->mutex);
            request_runtime_failure();
            return 0;
        }
        ai_progress(AI_WORKER);
    }
    if (queue->count == 0) {
        pthread_mutex_unlock(&queue->mutex);
        return 0;
    }
    *event = queue->events[queue->head];
    queue->head = (queue->head + 1) % EVENT_QUEUE_CAPACITY;
    --queue->count;
    pthread_mutex_unlock(&queue->mutex);
    return 1;
}

static void event_queue_destroy(struct event_queue *queue)
{
    pthread_cond_destroy(&queue->available);
    pthread_mutex_destroy(&queue->mutex);
    memset(queue, 0, sizeof(*queue));
}

static int wait_for_socket_or_shutdown(int socket_fd)
{
    struct pollfd descriptors[2];

    for (;;) {
        if (termination_requested())
            return 0;
        descriptors[0] = {socket_fd, POLLIN, 0};
        descriptors[1] = {shutdown_pipe[0], POLLIN, 0};
        int result = poll(descriptors, 2, 1000);
        if (result < 0 && errno == EINTR)
            continue;
        if (result < 0) {
            perror("video processor event poll");
            return -1;
        }
        ai_progress(AI_RECEIVER);
        if (termination_requested() || (descriptors[1].revents & POLLIN))
            return 0;
        if (descriptors[0].revents & POLLIN)
            return 1;
        if (descriptors[0].revents & (POLLERR | POLLHUP | POLLNVAL))
            return -1;
    }
}

enum event_read_result {
    EVENT_READ_FATAL = -1,
    EVENT_READ_SUCCESS = 0,
    EVENT_READ_DISCONNECTED = 1,
    EVENT_READ_SHUTDOWN = 2
};

/* Sensor의 ITS epoch ms를 목격차량 경로와 같은 Unix epoch ns로 복원한다. */
static int convert_sensor_event(const video_processor_event_t *wire,
                                struct accident_event *event)
{
    int64_t unix_ms;

    if (wire->detection_time <= 0 ||
        wire->detection_time > INT64_MAX - ITS_EPOCH_UNIX_MS)
        return -1;
    unix_ms = wire->detection_time + ITS_EPOCH_UNIX_MS;
    if (unix_ms < ITS_LEAP_SECONDS_MS)
        return -1;
    unix_ms -= ITS_LEAP_SECONDS_MS;
    if (unix_ms > (INT64_MAX - ACCIDENT_VIDEO_AFTER_NS -
                   FRAME_COVERAGE_TOLERANCE_NS) / 1000000LL)
        return -1;

    event->station_id = wire->station_id;
    event->sequence_number = wire->sequence_number;
    event->detection_time_ns = unix_ms * 1000000LL;
    return 0;
}

static enum event_read_result read_video_processor_event(
    struct accident_event *event)
{
    video_processor_event_t wire;
    ipc_header_t header;
    int client_fd;
    int wait_result;
    int receive_result;

    if (event_server_fd < 0) {
        event_server_fd = ipc_server_create(VIDEO_PROCESSOR_SOCKET_PATH);
        if (event_server_fd < 0) {
            event_server_fd = -1;
            return EVENT_READ_FATAL;
        }
        printf("사고차량 event socket 대기: %s\n",
               VIDEO_PROCESSOR_SOCKET_PATH);
    }

    wait_result = wait_for_socket_or_shutdown(event_server_fd);
    if (wait_result == 0)
        return EVENT_READ_SHUTDOWN;
    if (wait_result < 0)
        return EVENT_READ_FATAL;

    client_fd = ipc_server_accept(event_server_fd);
    if (client_fd < 0)
        return termination_requested() ? EVENT_READ_SHUTDOWN
                                       : EVENT_READ_FATAL;
    memset(&wire, 0, sizeof(wire));
    memset(&header, 0, sizeof(header));
    receive_result = ai_receive_event(client_fd, &header, &wire, sizeof(wire),
                                      shutdown_pipe[0], termination_requested);
    ipc_close(client_fd);
    if (receive_result != IPC_SUCCESS)
        return termination_requested() ? EVENT_READ_SHUTDOWN
                                       : EVENT_READ_DISCONNECTED;
    if (header.type != IPC_MSG_VIDEO_PROCESSOR_EVENT ||
        header.length != sizeof(wire) ||
        header.sequence != wire.sequence_number ||
        convert_sensor_event(&wire, event) != 0) {
        fprintf(stderr, "사고차량 event 계약 또는 시각 범위 오류\n");
        return EVENT_READ_DISCONNECTED;
    }

    printf("사고차량 event 수신: station=%" PRIu32
           ", sequence=%" PRIu16 ", detection_ns=%" PRId64 "\n",
           event->station_id, event->sequence_number,
           event->detection_time_ns);
    return EVENT_READ_SUCCESS;
}

static void *event_receiver_thread(void *argument)
{
    struct event_queue *queue = (struct event_queue *)argument;
    struct accident_event event;

    while (!termination_requested()) {
        enum event_read_result result = read_video_processor_event(&event);
        if (result == EVENT_READ_DISCONNECTED)
            continue;
        if (result == EVENT_READ_SHUTDOWN)
            break;
        if (result == EVENT_READ_FATAL) {
            request_runtime_failure();
            break;
        }
        if (event_queue_push(queue, &event) != 0) {
            request_runtime_failure();
            break;
        }
    }

    if (event_server_fd >= 0) {
        ipc_close(event_server_fd);
        event_server_fd = -1;
        unlink(VIDEO_PROCESSOR_SOCKET_PATH);
    }
    event_queue_close(queue);
    return NULL;
}

class CameraCapture {
public:
    explicit CameraCapture(struct ring_buffer *ring);
    ~CameraCapture();
    int start(void);
    void stop(void);
    int failed(void) const;

private:
    struct mapped_plane {
        int fd;
        void *base;
        size_t mapped_size;
        const uint8_t *data;
        size_t data_size;
    };
    struct mapped_buffer {
        libcamera::FrameBuffer *buffer;
        std::vector<struct mapped_plane> planes;
    };

    int map_buffers(void);
    void unmap_buffers(void);
    int copy_nv12(const libcamera::FrameBuffer *,
                  const libcamera::FrameMetadata &, uint8_t *);
    void record_normal_frame(void);
    void request_complete(libcamera::Request *);
    int start_camera(void);
    void stop_camera(void);
    void request_camera_reinitialization(void);
    static void *recovery_thread_entry(void *);
    void recovery_loop(void);

    struct ring_buffer *ring_;
    std::unique_ptr<libcamera::CameraManager> manager_;
    std::shared_ptr<libcamera::Camera> camera_;
    std::unique_ptr<libcamera::CameraConfiguration> configuration_;
    std::unique_ptr<libcamera::FrameBufferAllocator> allocator_;
    libcamera::Stream *stream_;
    std::vector<std::unique_ptr<libcamera::Request>> requests_;
    std::vector<struct mapped_buffer> mappings_;
    uint8_t *scratch_;
    unsigned int stride_;
    std::atomic_bool running_;
    std::atomic_bool fatal_error_;
    pthread_t recovery_thread_;
    pthread_mutex_t recovery_mutex_;
    pthread_cond_t recovery_condition_;
    int recovery_sync_initialized_;
    int recovery_thread_started_;
    int recovery_requested_;
    int recovery_closed_;
    int64_t last_normal_frame_monotonic_ns_;
    int awaiting_normal_frame_after_recovery_;
};

CameraCapture::CameraCapture(struct ring_buffer *ring)
    : ring_(ring), stream_(NULL), scratch_((uint8_t *)malloc(NV12_FRAME_SIZE)),
      stride_(0), running_(false), fatal_error_(false),
      recovery_sync_initialized_(0), recovery_thread_started_(0),
      recovery_requested_(0), recovery_closed_(0),
      last_normal_frame_monotonic_ns_(0),
      awaiting_normal_frame_after_recovery_(0)
{
}

CameraCapture::~CameraCapture()
{
    stop();
    free(scratch_);
}

int CameraCapture::map_buffers(void)
{
    long page_size = sysconf(_SC_PAGESIZE);
    auto unmap_mapping = [](struct mapped_buffer *mapping) {
        for (auto &plane : mapping->planes)
            munmap(plane.base, plane.mapped_size);
        mapping->planes.clear();
    };

    if (page_size <= 0)
        return -1;
    for (const auto &buffer : allocator_->buffers(stream_)) {
        struct mapped_buffer mapping;
        mapping.buffer = buffer.get();
        for (const auto &plane : buffer->planes()) {
            off_t aligned = plane.offset & ~(static_cast<off_t>(page_size) - 1);
            size_t delta = plane.offset - aligned;
            size_t mapped_size = plane.length + delta;
            void *base = mmap(NULL, mapped_size, PROT_READ, MAP_SHARED,
                              plane.fd.get(), aligned);
            if (base == MAP_FAILED) {
                perror("camera buffer mmap");
                unmap_mapping(&mapping);
                unmap_buffers();
                return -1;
            }
            try {
                mapping.planes.push_back({
                    plane.fd.get(), base, mapped_size,
                    static_cast<const uint8_t *>(base) + delta,
                    plane.length});
            } catch (const std::exception &error) {
                munmap(base, mapped_size);
                fprintf(stderr, "camera mapping 저장 실패: %s\n",
                        error.what());
                unmap_mapping(&mapping);
                unmap_buffers();
                return -1;
            }
        }
        try {
            mappings_.push_back(std::move(mapping));
        } catch (const std::exception &error) {
            fprintf(stderr, "camera mapping 등록 실패: %s\n", error.what());
            unmap_mapping(&mapping);
            unmap_buffers();
            return -1;
        }
    }
    return 0;
}

void CameraCapture::unmap_buffers(void)
{
    for (auto &mapping : mappings_)
        for (auto &plane : mapping.planes)
            munmap(plane.base, plane.mapped_size);
    mappings_.clear();
}

int CameraCapture::copy_nv12(const libcamera::FrameBuffer *buffer,
                             const libcamera::FrameMetadata &metadata,
                             uint8_t *destination)
{
    const struct mapped_buffer *mapping = NULL;
    libcamera::Span<const libcamera::FrameMetadata::Plane> metadata_planes =
        metadata.planes();
    int synchronized_fds[2];
    size_t synchronized_count = 0;
    size_t plane_limit;
    int copy_result = -1;

    for (const auto &item : mappings_)
        if (item.buffer == buffer) {
            mapping = &item;
            break;
        }
    if (mapping == NULL || mapping->planes.empty() || stride_ < CAMERA_WIDTH)
        return -1;

    if (mapping->planes.size() >= 2) {
        if (mapping->planes[0].data_size < stride_ * CAMERA_HEIGHT ||
            mapping->planes[1].data_size < stride_ * CAMERA_HEIGHT / 2U ||
            metadata_planes.size() < 2 ||
            metadata_planes[0].bytesused < stride_ * CAMERA_HEIGHT ||
            metadata_planes[1].bytesused < stride_ * CAMERA_HEIGHT / 2U)
            return -1;
        plane_limit = 2;
    } else {
        if (mapping->planes[0].data_size <
                stride_ * CAMERA_HEIGHT * 3U / 2U ||
            metadata_planes.empty() || metadata_planes[0].bytesused <
                stride_ * CAMERA_HEIGHT * 3U / 2U)
            return -1;
        plane_limit = 1;
    }

    for (size_t i = 0; i < plane_limit; ++i) {
        struct dma_buf_sync sync;
        int duplicate = 0;
        for (size_t previous = 0; previous < synchronized_count; ++previous)
            if (synchronized_fds[previous] == mapping->planes[i].fd)
                duplicate = 1;
        if (duplicate)
            continue;
        memset(&sync, 0, sizeof(sync));
        sync.flags = DMA_BUF_SYNC_START | DMA_BUF_SYNC_READ;
        if (ioctl(mapping->planes[i].fd, DMA_BUF_IOCTL_SYNC, &sync) != 0) {
            perror("DMA-BUF CPU read START");
            goto sync_end;
        }
        synchronized_fds[synchronized_count++] = mapping->planes[i].fd;
    }

    for (unsigned int row = 0; row < CAMERA_HEIGHT; ++row)
        memcpy(destination + row * CAMERA_WIDTH,
               mapping->planes[0].data + row * stride_, CAMERA_WIDTH);
    if (mapping->planes.size() >= 2) {
        for (unsigned int row = 0; row < CAMERA_HEIGHT / 2U; ++row)
            memcpy(destination + CAMERA_WIDTH * CAMERA_HEIGHT +
                       row * CAMERA_WIDTH,
                   mapping->planes[1].data + row * stride_, CAMERA_WIDTH);
    } else {
        for (unsigned int row = 0; row < CAMERA_HEIGHT / 2U; ++row)
            memcpy(destination + CAMERA_WIDTH * CAMERA_HEIGHT +
                       row * CAMERA_WIDTH,
                   mapping->planes[0].data + stride_ * CAMERA_HEIGHT +
                       row * stride_,
                   CAMERA_WIDTH);
    }
    copy_result = 0;

sync_end:
    while (synchronized_count > 0) {
        struct dma_buf_sync sync;
        memset(&sync, 0, sizeof(sync));
        sync.flags = DMA_BUF_SYNC_END | DMA_BUF_SYNC_READ;
        --synchronized_count;
        if (ioctl(synchronized_fds[synchronized_count], DMA_BUF_IOCTL_SYNC,
                  &sync) != 0) {
            perror("DMA-BUF CPU read END");
            copy_result = -1;
        }
    }
    return copy_result;
}

void CameraCapture::record_normal_frame(void)
{
    struct timespec now;
    if (clock_gettime(CLOCK_MONOTONIC, &now) != 0)
        return;
    pthread_mutex_lock(&recovery_mutex_);
    last_normal_frame_monotonic_ns_ =
        (int64_t)now.tv_sec * 1000000000LL + now.tv_nsec;
    awaiting_normal_frame_after_recovery_ = 0;
    pthread_mutex_unlock(&recovery_mutex_);
}

void CameraCapture::request_complete(libcamera::Request *request)
{
    auto buffer = request->buffers().find(stream_);

    if (request->status() == libcamera::Request::RequestCancelled)
        return;
    if (buffer == request->buffers().end()) {
        fprintf(stderr, "완료 request에 camera framebuffer가 없음\n");
    } else {
        libcamera::FrameBuffer *frame_buffer = buffer->second;
        const libcamera::FrameMetadata &metadata = frame_buffer->metadata();
        if (metadata.status == libcamera::FrameMetadata::FrameStartup) {
            /* 안정화 전 frame은 버리되 callback 끝에서 request를 재등록한다. */
        } else if (metadata.status != libcamera::FrameMetadata::FrameSuccess) {
            fprintf(stderr, "camera frame metadata 오류: %d\n",
                    (int)metadata.status);
        } else {
            auto frame_wall_clock =
                request->metadata().get(libcamera::controls::FrameWallClock);
            if (!frame_wall_clock) {
                fprintf(stderr, "camera frame에 FrameWallClock metadata가 없음\n");
            } else if (copy_nv12(frame_buffer, metadata, scratch_) == 0) {
                record_normal_frame();
                if (ring_push(ring_, scratch_, NV12_FRAME_SIZE,
                              *frame_wall_clock) != 0)
                    fprintf(stderr, "camera frame ring 저장 실패\n");
                else
                    ai_progress(AI_CAPTURE);
            }
        }
    }

    request->reuse(libcamera::Request::ReuseBuffers);
    if (running_.load() && camera_->queueRequest(request) != 0) {
        fprintf(stderr, "capture request 재등록 실패\n");
        request_camera_reinitialization();
    }
}

int CameraCapture::start_camera(void)
{
    libcamera::StreamConfiguration *configuration;
    libcamera::CameraConfiguration::Status validation_status;
    struct timespec watchdog_start;

    if (scratch_ == NULL)
        return -1;
    manager_ = std::make_unique<libcamera::CameraManager>();
    if (manager_->start() != 0 || manager_->cameras().empty()) {
        fprintf(stderr, "libcamera 시작 또는 카메라 검색 실패\n");
        return -1;
    }
    camera_ = manager_->cameras().front();
    if (camera_->acquire() != 0)
        return -1;
    configuration_ = camera_->generateConfiguration(
        {libcamera::StreamRole::VideoRecording});
    if (!configuration_ || configuration_->empty())
        return -1;

    configuration = &configuration_->at(0);
    configuration->size = {CAMERA_WIDTH, CAMERA_HEIGHT};
    configuration->pixelFormat = libcamera::formats::NV12;
    configuration->colorSpace = libcamera::ColorSpace::Rec709;
    configuration->bufferCount = 6;
    configuration_->orientation = libcamera::Orientation::Rotate180;
    validation_status = configuration_->validate();
    if (configuration_->orientation != libcamera::Orientation::Rotate180) {
        fprintf(stderr, "카메라가 180도 orientation을 지원하지 않음\n");
        return -1;
    }
    if (validation_status == libcamera::CameraConfiguration::Invalid) {
        fprintf(stderr, "카메라 설정 validation 실패 (Rotate180 포함)\n");
        return -1;
    }
    if (configuration->size != libcamera::Size(CAMERA_WIDTH, CAMERA_HEIGHT) ||
        configuration->pixelFormat != libcamera::formats::NV12 ||
        configuration->colorSpace != libcamera::ColorSpace::Rec709) {
        fprintf(stderr, "카메라가 640x480 Rec.709 NV12 설정을 지원하지 않음\n");
        return -1;
    }
    if (camera_->configure(configuration_.get()) != 0)
        return -1;

    stream_ = configuration->stream();
    stride_ = configuration->stride;
    allocator_ = std::make_unique<libcamera::FrameBufferAllocator>(camera_);
    if (allocator_->allocate(stream_) < 0 || map_buffers() != 0)
        return -1;
    for (const auto &buffer : allocator_->buffers(stream_)) {
        std::unique_ptr<libcamera::Request> request = camera_->createRequest();
        if (!request || request->addBuffer(stream_, buffer.get()) != 0)
            return -1;
        requests_.push_back(std::move(request));
    }

    camera_->requestCompleted.connect(this, &CameraCapture::request_complete);
    libcamera::ControlList controls(camera_->controls());
    controls.set(libcamera::controls::FrameDurationLimits,
                 libcamera::Span<const int64_t, 2>({33333, 33333}));
    if (camera_->start(&controls) != 0)
        return -1;
    running_.store(true);
    for (auto &request : requests_)
        if (camera_->queueRequest(request.get()) != 0)
            return -1;

    if (clock_gettime(CLOCK_MONOTONIC, &watchdog_start) != 0)
        return -1;
    pthread_mutex_lock(&recovery_mutex_);
    last_normal_frame_monotonic_ns_ =
        (int64_t)watchdog_start.tv_sec * 1000000000LL +
        watchdog_start.tv_nsec;
    pthread_mutex_unlock(&recovery_mutex_);
    return 0;
}

void CameraCapture::stop_camera(void)
{
    if (camera_ && running_.exchange(false))
        camera_->stop();
    requests_.clear();
    unmap_buffers();
    allocator_.reset();
    configuration_.reset();
    stream_ = NULL;
    if (camera_) {
        camera_->release();
        camera_.reset();
    }
    if (manager_) {
        manager_->stop();
        manager_.reset();
    }
}

void CameraCapture::request_camera_reinitialization(void)
{
    if (!recovery_sync_initialized_)
        return;
    pthread_mutex_lock(&recovery_mutex_);
    if (!recovery_closed_) {
        recovery_requested_ = 1;
        pthread_cond_signal(&recovery_condition_);
    }
    pthread_mutex_unlock(&recovery_mutex_);
}

void *CameraCapture::recovery_thread_entry(void *argument)
{
    static_cast<CameraCapture *>(argument)->recovery_loop();
    return NULL;
}

void CameraCapture::recovery_loop(void)
{
    pthread_mutex_lock(&recovery_mutex_);
    while (!recovery_closed_) {
        ai_progress(AI_RECOVERY);
        struct timespec deadline;
        struct timespec now;
        int wait_result = 0;
        int watchdog_timeout;
        int watchdog_recovery;
        int restart_result;
        int64_t now_ns;

        if (clock_gettime(CLOCK_MONOTONIC, &deadline) != 0)
            goto fatal;
        deadline.tv_nsec +=
            (long)(CAMERA_WATCHDOG_CHECK_MS % 1000U) * 1000000L;
        deadline.tv_sec += CAMERA_WATCHDOG_CHECK_MS / 1000U;
        if (deadline.tv_nsec >= 1000000000L) {
            ++deadline.tv_sec;
            deadline.tv_nsec -= 1000000000L;
        }
        if (!recovery_requested_)
            wait_result = pthread_cond_timedwait(&recovery_condition_,
                                                 &recovery_mutex_, &deadline);
        if (recovery_closed_)
            break;
        if (wait_result != 0 && wait_result != ETIMEDOUT)
            goto fatal;
        if (clock_gettime(CLOCK_MONOTONIC, &now) != 0)
            goto fatal;

        now_ns = (int64_t)now.tv_sec * 1000000000LL + now.tv_nsec;
        watchdog_timeout = last_normal_frame_monotonic_ns_ > 0 &&
            now_ns - last_normal_frame_monotonic_ns_ >=
                (int64_t)CAMERA_WATCHDOG_TIMEOUT_MS * 1000000LL;
        if (!recovery_requested_ && !watchdog_timeout)
            continue;
        watchdog_recovery = watchdog_timeout;
        if (awaiting_normal_frame_after_recovery_) {
            fprintf(stderr, "camera 재초기화 후 정상 frame 미수신\n");
            goto fatal;
        }
        recovery_requested_ = 0;
        pthread_mutex_unlock(&recovery_mutex_);

        fprintf(stderr, "camera application-level 재초기화 시도%s\n",
                watchdog_recovery ? " (watchdog)" : "");
        stop_camera();
        pthread_mutex_lock(&recovery_mutex_);
        recovery_requested_ = 0;
        awaiting_normal_frame_after_recovery_ = 1;
        pthread_mutex_unlock(&recovery_mutex_);

        try {
            restart_result = start_camera();
        } catch (const std::exception &error) {
            fprintf(stderr, "camera 재초기화 예외: %s\n", error.what());
            restart_result = -1;
        }
        if (restart_result != 0) {
            fprintf(stderr, "camera application-level 재초기화 실패\n");
            stop_camera();
            fatal_error_.store(true);
            request_runtime_failure();
            return;
        }
        fprintf(stderr, "camera application-level 재초기화 성공\n");
        pthread_mutex_lock(&recovery_mutex_);
    }
    pthread_mutex_unlock(&recovery_mutex_);
    return;

fatal:
    fatal_error_.store(true);
    pthread_mutex_unlock(&recovery_mutex_);
    request_runtime_failure();
}

int CameraCapture::start(void)
{
    pthread_condattr_t attribute;
    int attribute_initialized = 0;
    int mutex_initialized = 0;
    int condition_initialized = 0;

    fatal_error_.store(false);
    recovery_requested_ = 0;
    recovery_closed_ = 0;
    last_normal_frame_monotonic_ns_ = 0;
    awaiting_normal_frame_after_recovery_ = 0;
    if (pthread_mutex_init(&recovery_mutex_, NULL) != 0)
        goto fail;
    mutex_initialized = 1;
    if (pthread_condattr_init(&attribute) != 0)
        goto fail;
    attribute_initialized = 1;
    if (pthread_condattr_setclock(&attribute, CLOCK_MONOTONIC) != 0 ||
        pthread_cond_init(&recovery_condition_, &attribute) != 0)
        goto fail;
    condition_initialized = 1;
    pthread_condattr_destroy(&attribute);
    attribute_initialized = 0;
    recovery_sync_initialized_ = 1;
    if (start_camera() != 0)
        goto fail;
    if (pthread_create(&recovery_thread_, NULL, recovery_thread_entry, this) !=
        0)
        goto fail;
    recovery_thread_started_ = 1;
    return 0;

fail:
    stop_camera();
    if (attribute_initialized)
        pthread_condattr_destroy(&attribute);
    if (condition_initialized)
        pthread_cond_destroy(&recovery_condition_);
    if (mutex_initialized)
        pthread_mutex_destroy(&recovery_mutex_);
    recovery_sync_initialized_ = 0;
    return -1;
}

void CameraCapture::stop(void)
{
    if (recovery_sync_initialized_) {
        pthread_mutex_lock(&recovery_mutex_);
        recovery_closed_ = 1;
        pthread_cond_signal(&recovery_condition_);
        pthread_mutex_unlock(&recovery_mutex_);
        if (recovery_thread_started_) {
            pthread_join(recovery_thread_, NULL);
            recovery_thread_started_ = 0;
        }
    }
    stop_camera();
    if (recovery_sync_initialized_) {
        pthread_cond_destroy(&recovery_condition_);
        pthread_mutex_destroy(&recovery_mutex_);
        recovery_sync_initialized_ = 0;
    }
}

int CameraCapture::failed(void) const
{
    return fatal_error_.load() ? 1 : 0;
}

static std::string ffmpeg_error(int number)
{
    char text[AV_ERROR_MAX_STRING_SIZE] = {0};
    av_strerror(number, text, sizeof(text));
    return std::string(text);
}

static void configure_h264_encoder(AVCodecContext *codec, int global_header)
{
    codec->width = CAMERA_WIDTH;
    codec->height = CAMERA_HEIGHT;
    codec->pix_fmt = AV_PIX_FMT_NV12;
    codec->color_range = AVCOL_RANGE_MPEG;
    codec->color_primaries = AVCOL_PRI_BT709;
    codec->color_trc = AVCOL_TRC_BT709;
    codec->colorspace = AVCOL_SPC_BT709;
    codec->time_base = {1, 1000000000};
    codec->framerate = {(int)CAMERA_FRAME_RATE, 1};
    codec->bit_rate = H264_BIT_RATE;
    codec->gop_size = CAMERA_FRAME_RATE;
    if (global_header)
        codec->flags |= AV_CODEC_FLAG_GLOBAL_HEADER;
}

static int validate_h264_encoder(void)
{
    const AVCodec *encoder = avcodec_find_encoder(AV_CODEC_ID_H264);
    AVCodecContext *codec;
    int error_number;

    if (encoder == NULL) {
        fprintf(stderr, "startup H.264 encoder를 찾을 수 없음\n");
        return -1;
    }
    codec = avcodec_alloc_context3(encoder);
    if (codec == NULL)
        return -1;
    configure_h264_encoder(codec, 1);
    error_number = avcodec_open2(codec, encoder, NULL);
    if (error_number < 0)
        fprintf(stderr, "startup H.264 encoder validation 실패: %s\n",
                ffmpeg_error(error_number).c_str());
    avcodec_free_context(&codec);
    return error_number < 0 ? -1 : 0;
}

static int make_output_path(const struct accident_event *event,
                            char *path, size_t path_size)
{
    if (mkdir(OUTPUT_DIRECTORY, 0755) != 0 && errno != EEXIST) {
        perror("output directory");
        return -1;
    }
    int length = snprintf(path, path_size,
                          "%s/accident_station_%" PRIu32 "_%" PRIu16
                          "_%" PRId64 ".mp4",
                          OUTPUT_DIRECTORY, event->station_id,
                          event->sequence_number, event->detection_time_ns);
    return length >= 0 && (size_t)length < path_size ? 0 : -1;
}

static int write_mp4(const struct frame_view *frames, size_t frame_count,
                     const char *path, EvidenceHandoff *handoff,
                     uint32_t sequence, const uploader_evidence_ready_t &ready)
{
    AVFormatContext *format = NULL;
    AVCodecContext *codec = NULL;
    const AVCodec *encoder;
    AVStream *stream = NULL;
    AVFrame *frame = NULL;
    AVPacket *packet = NULL;
    AVDictionary *mux_options = NULL;
    char capture_start_timestamp[32];
    char temporary_path[512] = {0};
    int header_written = 0;
    int result = -1;
    int error_number = 0;

    if (frame_count == 0 || !EvidenceHandoff::can_publish(ready))
        return -1;
    ai_progress(AI_WORKER);
    int path_length = snprintf(temporary_path, sizeof(temporary_path),
                               "%s.part", path);
    if (path_length < 0 || (size_t)path_length >= sizeof(temporary_path))
        return -1;
    if (unlink(temporary_path) != 0 && errno != ENOENT)
        return -1;

    error_number = avformat_alloc_output_context2(&format, NULL, "mp4",
                                                   temporary_path);
    if (error_number < 0 || format == NULL)
        goto cleanup;
    snprintf(capture_start_timestamp, sizeof(capture_start_timestamp),
             "%" PRId64, frames[0].timestamp_ns);
    if (av_dict_set(&format->metadata, "capture_start_timestamp_ns",
                    capture_start_timestamp, 0) < 0 ||
        av_dict_set(&mux_options, "movflags", "use_metadata_tags", 0) < 0)
        goto cleanup;

    encoder = avcodec_find_encoder(AV_CODEC_ID_H264);
    if (encoder == NULL)
        goto cleanup;
    codec = avcodec_alloc_context3(encoder);
    if (codec == NULL)
        goto cleanup;
    configure_h264_encoder(codec,
                           format->oformat->flags & AVFMT_GLOBALHEADER);
    if ((error_number = avcodec_open2(codec, encoder, NULL)) < 0)
        goto ffmpeg_failure;
    stream = avformat_new_stream(format, NULL);
    if (stream == NULL)
        goto cleanup;
    stream->time_base = codec->time_base;
    if ((error_number =
             avcodec_parameters_from_context(stream->codecpar, codec)) < 0)
        goto ffmpeg_failure;
    if (!(format->oformat->flags & AVFMT_NOFILE) &&
        (error_number = avio_open(&format->pb, temporary_path,
                                  AVIO_FLAG_WRITE)) < 0)
        goto ffmpeg_failure;
    if ((error_number = avformat_write_header(format, &mux_options)) < 0)
        goto ffmpeg_failure;
    header_written = 1;

    frame = av_frame_alloc();
    packet = av_packet_alloc();
    if (frame == NULL || packet == NULL)
        goto cleanup;
    frame->format = codec->pix_fmt;
    frame->width = codec->width;
    frame->height = codec->height;
    frame->color_range = codec->color_range;
    frame->color_primaries = codec->color_primaries;
    frame->color_trc = codec->color_trc;
    frame->colorspace = codec->colorspace;
    if ((error_number = av_frame_get_buffer(frame, 32)) < 0)
        goto ffmpeg_failure;

    for (size_t i = 0; i <= frame_count; ++i) {
        ai_progress(AI_WORKER);
        AVFrame *input = i < frame_count ? frame : NULL;
        if (input != NULL) {
            if (frames[i].size != NV12_FRAME_SIZE ||
                av_frame_make_writable(frame) < 0)
                goto cleanup;
            for (unsigned int row = 0; row < CAMERA_HEIGHT; ++row)
                memcpy(frame->data[0] + row * frame->linesize[0],
                       frames[i].data + row * CAMERA_WIDTH, CAMERA_WIDTH);
            const uint8_t *uv =
                frames[i].data + CAMERA_WIDTH * CAMERA_HEIGHT;
            for (unsigned int row = 0; row < CAMERA_HEIGHT / 2U; ++row)
                memcpy(frame->data[1] + row * frame->linesize[1],
                       uv + row * CAMERA_WIDTH, CAMERA_WIDTH);
            frame->pts = frames[i].timestamp_ns - frames[0].timestamp_ns;
        }
        if ((error_number = avcodec_send_frame(codec, input)) < 0)
            goto ffmpeg_failure;
        while ((error_number = avcodec_receive_packet(codec, packet)) >= 0) {
            av_packet_rescale_ts(packet, codec->time_base, stream->time_base);
            packet->stream_index = stream->index;
            error_number = av_interleaved_write_frame(format, packet);
            av_packet_unref(packet);
            ai_progress(AI_WORKER);
            if (error_number < 0)
                goto ffmpeg_failure;
        }
        if (error_number != AVERROR(EAGAIN) && error_number != AVERROR_EOF)
            goto ffmpeg_failure;
    }

    ai_progress(AI_WORKER);
    error_number = av_write_trailer(format);
    header_written = 0;
    if (error_number < 0)
        goto ffmpeg_failure;
    if (format->pb != NULL &&
        (error_number = avio_closep(&format->pb)) < 0)
        goto ffmpeg_failure;
    ai_progress(AI_WORKER);
    if (handoff->publish(temporary_path, sequence, ready) != 0) {
        perror("MP4 및 인계 기록 commit");
        goto cleanup;
    }
    result = 0;
    goto cleanup;

ffmpeg_failure:
    fprintf(stderr, "FFmpeg 실패: %s\n", ffmpeg_error(error_number).c_str());

cleanup:
    if (header_written) {
        (void)av_write_trailer(format);
        result = -1;
    }
    av_packet_free(&packet);
    av_frame_free(&frame);
    avcodec_free_context(&codec);
    av_dict_free(&mux_options);
    if (format != NULL) {
        if (format->pb != NULL) {
            (void)avio_closep(&format->pb);
            result = -1;
        }
        avformat_free_context(format);
    }
    if (result != 0 && temporary_path[0] != '\0' &&
        !EvidenceHandoff::has_record(ready) && unlink(temporary_path) != 0 && errno != ENOENT)
        perror("MP4 temporary file cleanup");
    return result;
}

static uint64_t monotonic_timestamp_ns(void)
{
    struct timespec now;
    if (clock_gettime(CLOCK_MONOTONIC, &now) != 0)
        return 0;
    return (uint64_t)now.tv_sec * 1000000000ULL + (uint64_t)now.tv_nsec;
}

static const char *uploader_time_sync_status(uint32_t state)
{
    switch (state) {
    case TIME_SYNC_SYNCED:
        return "SYNCED";
    case TIME_SYNC_UNKNOWN:
        return "UNKNOWN";
    case TIME_SYNC_SYNCING:
    case TIME_SYNC_UNSYNCED:
    case TIME_SYNC_ERROR:
        return "UNRELIABLE";
    default:
        return NULL;
    }
}

// One deadline covers connect, request and response, including partial messages.
static int evidence_ipc_exchange(const char *path, ipc_header_t *send_header,
                                 const void *request, size_t request_size,
                                 ipc_header_t *receive_header, void *response,
                                 size_t response_size, unsigned timeout_ms)
{
    int fd = socket(AF_UNIX, SOCK_STREAM | SOCK_NONBLOCK | SOCK_CLOEXEC, 0);
    if (fd < 0)
        return -1;
    const uint64_t deadline = monotonic_timestamp_ns() +
                              (uint64_t)timeout_ms * 1000000ULL;
    auto wait_ready = [&](short events) {
        for (;;) {
            const uint64_t now = monotonic_timestamp_ns();
            if (now >= deadline || termination_requested())
                return false;
            struct pollfd descriptor = {fd, events, 0};
            int remaining_ms = (int)((deadline - now + 999999ULL) / 1000000ULL);
            int result = poll(&descriptor, 1, remaining_ms > 100 ? 100 : remaining_ms);
            if (result > 0)
                return true;
            if (result < 0 && errno != EINTR)
                return false;
        }
    };
    auto transfer = [&](void *data, size_t size, bool sending) {
        size_t done = 0;
        while (done < size) {
            if (monotonic_timestamp_ns() >= deadline || termination_requested())
                return false;
            ssize_t count = sending
                ? send(fd, (char *)data + done, size - done, MSG_NOSIGNAL)
                : recv(fd, (char *)data + done, size - done, 0);
            if (count > 0) {
                done += (size_t)count;
            } else if (count < 0 && errno == EINTR) {
                continue;
            } else if (count < 0 && (errno == EAGAIN || errno == EWOULDBLOCK)) {
                if (!wait_ready(sending ? POLLOUT : POLLIN))
                    return false;
            } else {
                return false;
            }
        }
        return true;
    };

    struct sockaddr_un address = {};
    address.sun_family = AF_UNIX;
    int path_length = snprintf(address.sun_path, sizeof(address.sun_path), "%s", path);
    bool connected = false;
    if (path_length >= 0 && (size_t)path_length < sizeof(address.sun_path)) {
        connected = connect(fd, (struct sockaddr *)&address, sizeof(address)) == 0;
        if (!connected && errno == EINPROGRESS && wait_ready(POLLOUT)) {
            int error = 0;
            socklen_t size = sizeof(error);
            connected = getsockopt(fd, SOL_SOCKET, SO_ERROR, &error, &size) == 0 &&
                        error == 0;
        }
        // AF_UNIX EAGAIN means a full listener backlog: retry a new connection later.
    }
    send_header->length = (uint32_t)request_size;
    bool success = connected &&
        transfer(send_header, sizeof(*send_header), true) &&
        transfer(const_cast<void *>(request), request_size, true) &&
        transfer(receive_header, sizeof(*receive_header), false) &&
        receive_header->length == response_size &&
        transfer(response, response_size, false);
    close(fd);
    return success ? 0 : -1;
}

static int query_time_sync(uint32_t sequence, time_sync_status_t *status)
{
    time_sync_request_t request = {};
    ipc_header_t send_header = {};
    ipc_header_t receive_header = {};
    memset(status, 0, sizeof(*status));
    request.version = TIME_SYNC_PROTOCOL_VERSION;
    send_header.type = IPC_MSG_TIME_SYNC_REQUEST;
    send_header.flags = IPC_FLAG_NONE;
    send_header.sequence = sequence;
    send_header.timestamp = monotonic_timestamp_ns();
    if (evidence_ipc_exchange(TIME_SYNC_SOCKET_PATH, &send_header,
                              &request, sizeof(request), &receive_header,
                              status, sizeof(*status), 2000) != 0 ||
        receive_header.type != IPC_MSG_TIME_SYNC_STATUS ||
        receive_header.sequence != sequence ||
        status->version != TIME_SYNC_PROTOCOL_VERSION ||
        uploader_time_sync_status(status->state) == NULL ||
        !std::isfinite(status->system_offset_ms))
        return -1;
    return 0;
}

static int deliver_evidence_to_uploader(uint32_t sequence,
                                        const uploader_evidence_ready_t *ready)
{
    uploader_response_t response = {};
    ipc_header_t send_header = {};
    ipc_header_t receive_header = {};
    send_header.type = IPC_MSG_CAMERA_READY;
    send_header.flags = IPC_FLAG_ACK_REQUIRED;
    send_header.sequence = sequence;
    send_header.timestamp = monotonic_timestamp_ns();
    if (evidence_ipc_exchange(UPLOADER_SOCKET_PATH, &send_header,
                              ready, sizeof(*ready), &receive_header,
                              &response, sizeof(response), 5000) != 0 ||
        receive_header.type != UPLOADER_MSG_RESPONSE ||
        receive_header.sequence != sequence ||
        response.version != UPLOADER_PROTOCOL_VERSION ||
        response.code != UPLOADER_RESPONSE_OK)
        return -1;
    printf("Uploader CAMERA_READY 완료: action_id=%s, video=%s\n",
           ready->action_id, ready->video_path);
    return 0;
}

// Only completed-file metadata is queued; capture and encoding never wait for IPC.
static void prepare_evidence_time(uint32_t sequence,
                                  uploader_evidence_ready_t *ready)
{
    time_sync_status_t status = {};
    if (query_time_sync(sequence, &status) == 0) {
        ready->chrony_offset_ms = status.system_offset_ms;
        snprintf(ready->time_sync_status, sizeof(ready->time_sync_status), "%s",
                 uploader_time_sync_status(status.state));
    } else {
        ready->chrony_offset_ms = 0.0;
        snprintf(ready->time_sync_status, sizeof(ready->time_sync_status), "UNRELIABLE");
        fprintf(stderr, "Time Sync 조회 실패: action_id=%s, UNRELIABLE로 인계\n",
                ready->action_id);
    }
}


static int resolve_final_video_path(const char *path, char *resolved,
                                    size_t resolved_size)
{
    const std::string input(path);
    const size_t slash = input.find_last_of('/');
    const std::string parent = slash == std::string::npos ? "." : input.substr(0, slash);
    const std::string name = slash == std::string::npos ? input : input.substr(slash + 1);
    char *absolute = realpath(parent.c_str(), NULL);
    if (absolute == NULL || name.empty()) {
        free(absolute);
        return -1;
    }
    int length = snprintf(resolved, resolved_size, "%s/%s", absolute, name.c_str());
    free(absolute);
    return length >= 0 && static_cast<size_t>(length) < resolved_size ? 0 : -1;
}

static int make_evidence_payload(uploader_evidence_ready_t *ready_out,
                                       const struct accident_event *event,
                                       const char *video_path,
                                       const char *vehicle_id,
                                       int64_t clip_start_time_ns,
                                       int64_t clip_end_time_ns)
{
    uploader_evidence_ready_t ready;
    uint32_t action_id = (event->station_id << 16) |
                         (uint32_t)event->sequence_number;

    memset(&ready, 0, sizeof(ready));
    ready.version = UPLOADER_PROTOCOL_VERSION;
    ready.event_time_ns = event->detection_time_ns;
    ready.clip_start_time_ns = clip_start_time_ns;
    ready.clip_end_time_ns = clip_end_time_ns;

    if (snprintf(ready.action_id, sizeof(ready.action_id), "%" PRIu32,
                 action_id) >= (int)sizeof(ready.action_id) ||
        snprintf(ready.accident_vehicle_id,
                 sizeof(ready.accident_vehicle_id), "%s",
                 vehicle_id) >=
            (int)sizeof(ready.accident_vehicle_id) ||
        snprintf(ready.vehicle_id, sizeof(ready.vehicle_id), "%s",
                 vehicle_id) >= (int)sizeof(ready.vehicle_id) ||
        resolve_final_video_path(video_path, ready.video_path,
                                 sizeof(ready.video_path)) != 0)
        return -1;

    *ready_out = ready;
    return 0;
}

static void *video_worker_thread(void *argument)
{
    struct worker_context *context = (struct worker_context *)argument;
    struct accident_event event;

    EvidenceHandoff handoff(prepare_evidence_time, deliver_evidence_to_uploader,
                            ai_handoff_progress);
    if (handoff.start(OUTPUT_DIRECTORY, ai_restore_progress) != 0) {
        request_runtime_failure();
        return NULL;
    }

    while (event_queue_pop(context->queue, &event)) {
        struct video_snapshot snapshot;
        int64_t end_ns;
        char output_path[256];
        uploader_evidence_ready_t ready;

        int64_t available_end_ns;
        int64_t begin_ns = event.detection_time_ns - ACCIDENT_VIDEO_BEFORE_NS;
        end_ns = event.detection_time_ns + ACCIDENT_VIDEO_AFTER_NS;
        if (ring_wait_for_snapshot_end(context->ring, end_ns,
                                       &available_end_ns,
                                       ACCIDENT_VIDEO_AFTER_NS +
                                       FRAME_COVERAGE_TOLERANCE_NS) != 0) {
            fprintf(stderr, "event drop: 사고 후 2초 저장 끝점의 frame이 없음\n");
            continue;
        }
        if (ring_copy_snapshot(context->ring, begin_ns, end_ns,
                               &snapshot) != 0) {
            fprintf(stderr, "event drop: 10초 snapshot을 확보하지 못함\n");
            continue;
        }

        if (make_output_path(&event, output_path, sizeof(output_path)) != 0 ||
            make_evidence_payload(&ready, &event, output_path, context->vehicle_id,
                                  snapshot.frames[0].timestamp_ns,
                                  snapshot.frames[snapshot.count - 1].timestamp_ns) != 0 ||
            write_mp4(snapshot.frames, snapshot.count, output_path, &handoff,
                      (event.station_id << 16) | event.sequence_number, ready) != 0) {
            fprintf(stderr, "사고차량 영상 저장 실패\n");
            snapshot_destroy(context->ring, &snapshot);
        } else {
            snapshot_destroy(context->ring, &snapshot);
            printf("사고차량 영상 저장 완료: %s\n", output_path);
        }
    }
    return NULL;
}

int main(void)
{
    const char *vehicle_id = getenv("UPLOADER_VEHICLE_ID");
    struct ring_buffer ring;
    struct event_queue queue;
    struct worker_context worker_context;
    pthread_t receiver_thread;
    pthread_t worker_thread;
    int signal_initialized = 0;
    int ring_initialized = 0;
    int queue_initialized = 0;
    int result = EXIT_FAILURE;

    if (vehicle_id == NULL || vehicle_id[0] == '\0') {
        fprintf(stderr, "UPLOADER_VEHICLE_ID 환경변수가 설정되지 않음\n");
        return EXIT_FAILURE;
    }
    if (ace_watchdog_start(&ai_watchdog, ai_watchdog_names, 5) != 0) {
        perror("AI watchdog 시작");
        return EXIT_FAILURE;
    }
    if (validate_h264_encoder() != 0 || shutdown_signal_init() != 0)
        return EXIT_FAILURE;
    signal_initialized = 1;
    runtime_failure_requested.store(false);
    if (ring_init(&ring, RING_CAPACITY) != 0)
        goto cleanup;
    ring_initialized = 1;
    if (event_queue_init(&queue) != 0)
        goto cleanup;
    queue_initialized = 1;

    {
        CameraCapture camera(&ring);
        if (camera.start() != 0) {
            fprintf(stderr, "카메라 시작 실패\n");
            goto cleanup;
        }
        worker_context.queue = &queue;
        worker_context.ring = &ring;
        worker_context.vehicle_id = vehicle_id;
        if (pthread_create(&worker_thread, NULL, video_worker_thread,
                           &worker_context) != 0) {
            camera.stop();
            goto cleanup;
        }
        if (pthread_create(&receiver_thread, NULL, event_receiver_thread,
                           &queue) != 0) {
            event_queue_close(&queue);
            pthread_join(worker_thread, NULL);
            camera.stop();
            goto cleanup;
        }

        pthread_join(receiver_thread, NULL);
        ring_wake_waiters(&ring);
        pthread_join(worker_thread, NULL);
        camera.stop();
        result = camera.failed() || runtime_failure_requested.load()
                     ? EXIT_FAILURE
                     : EXIT_SUCCESS;
    }

cleanup:
    if (queue_initialized)
        event_queue_destroy(&queue);
    if (ring_initialized)
        ring_destroy(&ring);
    if (signal_initialized)
        shutdown_signal_destroy();
    ace_watchdog_stop(&ai_watchdog);
    return result;
}
