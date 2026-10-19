/* 파일 역할: ONNX Runtime 기반 차량 검출과 추론 이벤트 통신을 담당하는 팀 공통 모듈. 프레임 전처리·검출 후처리·종료 신호를 관리한다. */
/*
 * 사고 영상 AI 프로세스 기반 구현
 *
 * 전체 코드는 기존 프로젝트와 맞추기 위해 C/POSIX 스타일을 유지한다.
 * libcamera와 ONNX Runtime의 공식 C++ API를 사용하는 카메라/추론 경계에만
 * C++ 클래스, smart pointer, vector를 사용하고 나머지는 C/POSIX 형태를 유지한다.
 *
 * 전체 실행 흐름:
 *   libcamera callback -> 20초 NV12 ring buffer 갱신
 *   Filter IPC receiver-> 사고 event를 FIFO queue에 추가
 *   inference worker   -> 10초 snapshot 생성 -> YOLOX ONNX 추론
 *                      -> 최근 3회 중 2회 target 검출 시 LED 점등 및 MP4 저장
 *                      -> Time Sync 조회 후 Uploader에 최종 경로 전달
 */
#include <libcamera/control_ids.h>
#include <libcamera/formats.h>
#include <libcamera/libcamera.h>
#include <libcamera/orientation.h>
#include <gpiod.h>
#include <linux/dma-buf.h>
#include <onnxruntime_cxx_api.h>

/* 공용 C protocol header의 C11 wire-size assertion을 C++에서도 유지한다. */
#define _Static_assert static_assert
extern "C" {
#include "ipc.h"
#include "protocol.h"
#include "inference_protocol.h"
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

#include <algorithm>
#include <array>
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

/* 카메라와 영상 저장 규격: 현재 단계에서는 고정값으로 사용한다. */
#define CAMERA_WIDTH 640U
#define CAMERA_HEIGHT 480U
#define CAMERA_FRAME_RATE 30U
#define NV12_FRAME_SIZE (CAMERA_WIDTH * CAMERA_HEIGHT * 3U / 2U)
#define RING_CAPACITY 640U
#define SNAPSHOT_POOL_RESERVE 320U
#define FRAME_POOL_CAPACITY \
    (RING_CAPACITY + SNAPSHOT_POOL_RESERVE)
#define RING_RETENTION_NS 20000000000LL
#define ACCIDENT_VIDEO_BEFORE_NS 8000000000LL
#define ACCIDENT_VIDEO_AFTER_NS 2000000000LL
#define H264_BIT_RATE 2500000LL
#define OUTPUT_DIRECTORY "recordings"
/* 500ms를 ns로 표현한 구간 허용 오차다. */
#define FRAME_COVERAGE_TOLERANCE_NS 500000000LL
/* Worker가 처리하기 전까지 대기시킬 수 있는 최대 사고 event 개수다. */
#define EVENT_QUEUE_CAPACITY 4U
#define TIME_SYNC_SOCKET_PATH "/tmp/time-sync.sock"
#define LED_GPIO_OFFSET 17U
#define CAMERA_WATCHDOG_TIMEOUT_MS 1000U
#define CAMERA_WATCHDOG_CHECK_MS 200U
/* 2-class YOLOX-Tiny의 입출력 계약과 공통 영상 선별 설정. */
#define MODEL_PATH "vehicle_detector.onnx"
#define MODEL_INPUT_NAME "images"
#define MODEL_OUTPUT_NAME "raw_predictions"
#define MODEL_INPUT_WIDTH 640U
#define MODEL_INPUT_HEIGHT 480U
/* 2-class 계약: class 0은 아이오닉, class 1은 나머지 차량이다. */
#define MODEL_CLASS_COUNT 2U
#define MODEL_OUTPUT_FIELDS (5U + MODEL_CLASS_COUNT)
#define MODEL_PREDICTION_COUNT 6300U
#include "inference_config.h"

static volatile sig_atomic_t shutdown_requested = 0;
static std::atomic_bool runtime_failure_requested(false);
static int shutdown_pipe[2] = {-1, -1};
static int event_server_fd = -1;

/* 실제 NV12 memory와 Ring/Snapshot ownership refcount를 보관한다. */
struct frame_buffer_storage {
    uint8_t *data;
    size_t refcount;
};

/* 모든 frame memory와 refcount 0 buffer의 free stack을 startup에 준비한다. */
struct frame_pool {
    struct frame_buffer_storage *buffers;
    struct frame_buffer_storage **free_buffers;
    size_t capacity;
    size_t free_count;
};

/* Ring의 한 칸은 Frame Pool buffer에 대한 Ring ownership을 하나 가진다. */
struct frame_slot {
    struct frame_buffer_storage *storage;
    int64_t timestamp_ns;
    int valid;
};

/* Snapshot이 refcount로 lifetime을 보장하는 읽기용 frame view다. */
struct frame_view {
    const uint8_t *data;
    size_t size;
    int64_t timestamp_ns;
    struct frame_buffer_storage *storage;
};

/*
 * 사고 event 하나를 처리하는 동안 Frame Pool buffer를 참조한다.
 * Frame 본체를 복사하지 않고 view metadata와 buffer refcount만 보관한다.
 */
struct video_snapshot {
    struct frame_view *frames;
    size_t count;
};

/*
 * head  : 다음 camera frame이 기록될 slot 위치
 * count : 현재 유효한 slot 개수
 * pool  : Ring과 단일 active Snapshot이 공유하는 frame memory
 * mutex : ring 상태와 pool refcount/free stack을 함께 보호
 * frame_available: 사고 시각 직후 frame을 기다리는 worker를 깨움
 */
struct ring_buffer {
    struct frame_slot *slots;
    struct frame_pool pool;
    size_t capacity;
    size_t head;
    size_t count;
    pthread_mutex_t mutex;
    pthread_cond_t frame_available;
};

/* 2-of-3 최종 target 판정과 frame 처리 중 발생한 runtime 오류를 구분한다. */
enum inference_result {
    INFERENCE_FOUND,
    INFERENCE_NOT_FOUND,
    INFERENCE_ERROR
};

class YoloXDetector;

/*
 * Event queue는 크기 4의 고정 배열로 구성한다.
 * receiver가 tail에 추가하고 worker가 head에서 꺼내므로 FIFO가 보장된다.
 * 대기 event가 4개인 상태에서 새 event가 들어오면 가장 오래된 head를 버린다.
 * head와 tail은 배열 끝에 도달하면 0으로 돌아간다.
 * available condition variable을 사용하여 queue가 비었을 때 busy-wait하지 않는다.
 * 연속 event도 queue에 저장하지만 영상은 미리 예약하지 않으며, worker가 꺼낸
 * 시점에 필요한 10초 구간이 ring에 없으면 해당 event만 drop한다.
 */
struct event_queue {
    inference_event_t events[EVENT_QUEUE_CAPACITY];
    size_t head;
    size_t tail;
    size_t count;
    int closed;
    pthread_mutex_t mutex;
    pthread_cond_t available;
};

/* Worker가 사용하는 queue/ring/LED, ONNX session과 로컬 차량 ID의 참조 묶음이다. */
struct worker_context {
    struct event_queue *queue;
    struct ring_buffer *ring;
    struct led_controller *led;
    YoloXDetector *detector;
    const char *vehicle_id;
};

/* GPIO17 LED를 하나의 전용 pthread에서 deadline 기반으로 관리한다. */
struct led_controller {
    struct gpiod_chip *chip;
    struct gpiod_line_request *request;
    unsigned int offset;
    pthread_t thread;
    pthread_mutex_t mutex;
    pthread_cond_t condition;
    struct timespec deadline;
    int thread_started;
    int active;
    int closed;
};

static int termination_requested(void)
{
    return shutdown_requested || runtime_failure_requested.load();
}

/* Runtime fatal을 self-pipe로 receiver에 알려 main의 join 경로를 종료시킨다. */
static void request_runtime_failure(void)
{
    uint8_t notification = 1;

    runtime_failure_requested.store(true);
    if (shutdown_pipe[1] >= 0) {
        ssize_t write_result = write(shutdown_pipe[1], &notification, sizeof(notification));
        (void)write_result;
    }
}

/* Signal handler에서는 종료 flag 설정과 async-signal-safe write만 수행한다. */
static void shutdown_signal_handler(int signal_number)
{
    uint8_t notification = 1;
    int saved_errno = errno;

    (void)signal_number;
    shutdown_requested = 1;
    if (shutdown_pipe[1] >= 0) {
        ssize_t write_result = write(shutdown_pipe[1], &notification, sizeof(notification));
        (void)write_result;
    }

    errno = saved_errno;
}

static int shutdown_signal_init(void)
{
    struct sigaction action;
    int flags;

    if (pipe(shutdown_pipe) != 0) {
        perror("shutdown pipe");
        return -1;
    }

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
    {
        sigset_t signals;

        sigemptyset(&signals);
        sigaddset(&signals, SIGINT);
        sigaddset(&signals, SIGTERM);
        pthread_sigmask(SIG_BLOCK, &signals, NULL);

        memset(&action, 0, sizeof(action));
        action.sa_handler = SIG_DFL;
        sigemptyset(&action.sa_mask);
        sigaction(SIGINT, &action, NULL);
        sigaction(SIGTERM, &action, NULL);
    }
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

    /* Pipe를 닫는 짧은 구간에는 handler가 실행되지 않도록 종료 signal을 막는다. */
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



static void *led_controller_thread(void *argument)
{
    struct led_controller *led = (struct led_controller *)argument;

    pthread_mutex_lock(&led->mutex);
    while (!led->closed) {
        int wait_result;

        ai_progress(AI_LED);
        while (!led->active && !led->closed) {
            struct timespec deadline = ai_idle_deadline();
            int error = pthread_cond_timedwait(&led->condition, &led->mutex, &deadline);
            if (error != 0 && error != ETIMEDOUT) {
                pthread_mutex_unlock(&led->mutex);
                request_runtime_failure();
                return NULL;
            }
            ai_progress(AI_LED);
        }

        if (led->closed)
            break;

        wait_result = pthread_cond_timedwait(&led->condition,
                                             &led->mutex,
                                             &led->deadline);
        if (wait_result == 0)
            continue;

        if (wait_result != ETIMEDOUT)
            fprintf(stderr, "LED condition timed wait 실패: %s\n",
                    strerror(wait_result));

        /* Timeout 뒤 잠금을 다시 얻기 전에 연장된 점등 기한을 재확인한다. */
        if (wait_result == ETIMEDOUT) {
            struct timespec now;

            if (clock_gettime(CLOCK_MONOTONIC, &now) != 0)
                perror("LED CLOCK_MONOTONIC");
            else if (now.tv_sec < led->deadline.tv_sec ||
                     (now.tv_sec == led->deadline.tv_sec &&
                      now.tv_nsec < led->deadline.tv_nsec))
                continue;
        }

        if (gpiod_line_request_set_value(led->request,
                                         led->offset,
                                         GPIOD_LINE_VALUE_INACTIVE) != 0)
            perror("GPIO17 LED OFF");
        led->active = 0;
    }

    if (gpiod_line_request_set_value(led->request,
                                     led->offset,
                                     GPIOD_LINE_VALUE_INACTIVE) != 0)
        perror("GPIO17 종료 LED OFF");
    led->active = 0;
    pthread_mutex_unlock(&led->mutex);
    return NULL;
}

static int led_controller_init(struct led_controller *led)
{
    struct gpiod_line_settings *settings = NULL;
    struct gpiod_line_config *line_config = NULL;
    struct gpiod_request_config *request_config = NULL;
    pthread_condattr_t condition_attribute;
    unsigned int offset = LED_GPIO_OFFSET;
    int mutex_initialized = 0;
    int condition_attribute_initialized = 0;
    int condition_initialized = 0;
    int result = -1;

    memset(led, 0, sizeof(*led));
    led->offset = offset;

    if (pthread_mutex_init(&led->mutex, NULL) != 0)
        goto cleanup;
    mutex_initialized = 1;

    if (pthread_condattr_init(&condition_attribute) != 0)
        goto cleanup;
    condition_attribute_initialized = 1;

    if (pthread_condattr_setclock(&condition_attribute, CLOCK_MONOTONIC) != 0 ||
        pthread_cond_init(&led->condition, &condition_attribute) != 0)
        goto cleanup;
    condition_initialized = 1;

    /* 대회 장비는 Raspberry Pi 5와 현재 OS/커널 구성을 고정해서 사용한다.
     * 이 환경에서 RP1 GPIO controller는 /dev/gpiochip0이다. */
    led->chip = gpiod_chip_open("/dev/gpiochip0");
    if (led->chip == NULL) {
        perror("GPIO chip open");
        goto cleanup;
    }

    settings = gpiod_line_settings_new();
    line_config = gpiod_line_config_new();
    request_config = gpiod_request_config_new();
    if (settings == NULL || line_config == NULL || request_config == NULL)
        goto gpio_failure;

    if (gpiod_line_settings_set_direction(
            settings, GPIOD_LINE_DIRECTION_OUTPUT) != 0 ||
        gpiod_line_settings_set_output_value(
            settings, GPIOD_LINE_VALUE_INACTIVE) != 0 ||
        gpiod_line_config_add_line_settings(
            line_config, &offset, 1, settings) != 0)
        goto gpio_failure;

    gpiod_request_config_set_consumer(request_config, "inference-led");
    led->request = gpiod_chip_request_lines(led->chip,
                                            request_config,
                                            line_config);
    if (led->request == NULL)
        goto gpio_failure;

    if (pthread_create(&led->thread, NULL, led_controller_thread, led) != 0) {
        fprintf(stderr, "LED thread 생성 실패\n");
        goto cleanup;
    }
    led->thread_started = 1;
    result = 0;
    goto cleanup;

gpio_failure:
    perror("GPIO17 LED 초기화");

cleanup:
    gpiod_request_config_free(request_config);
    gpiod_line_config_free(line_config);
    gpiod_line_settings_free(settings);

    if (condition_attribute_initialized)
        pthread_condattr_destroy(&condition_attribute);

    if (result != 0) {
        if (led->request != NULL)
            gpiod_line_request_release(led->request);
        if (led->chip != NULL)
            gpiod_chip_close(led->chip);
        if (condition_initialized)
            pthread_cond_destroy(&led->condition);
        if (mutex_initialized)
            pthread_mutex_destroy(&led->mutex);
        memset(led, 0, sizeof(*led));
        fprintf(stderr, "LED controller 시작 실패\n");
    }

    return result;
}

/* FOUND 시 즉시 LED를 켜고 마지막 FOUND 기준 5초로 deadline을 갱신한다. */
static int led_controller_trigger(struct led_controller *led)
{
    int result = 0;

    pthread_mutex_lock(&led->mutex);
    if (led->closed || clock_gettime(CLOCK_MONOTONIC, &led->deadline) != 0) {
        result = -1;
    } else {
        /* LED 점등 시간은 5초로 고정한다. */
        led->deadline.tv_sec += 5;
        if (gpiod_line_request_set_value(led->request,
                                         led->offset,
                                         GPIOD_LINE_VALUE_ACTIVE) != 0) {
            perror("GPIO17 LED ON");
            result = -1;
        } else {
            led->active = 1;
            pthread_cond_signal(&led->condition);
        }
    }
    pthread_mutex_unlock(&led->mutex);

    return result;
}

static void led_controller_destroy(struct led_controller *led)
{
    pthread_mutex_lock(&led->mutex);
    led->closed = 1;
    pthread_cond_signal(&led->condition);
    pthread_mutex_unlock(&led->mutex);

    if (led->thread_started)
        pthread_join(led->thread, NULL);

    /* Thread 종료 경로에서도 LOW를 쓰지만, release 직전 상태를 한 번 더 보장한다. */
    if (led->request != NULL) {
        if (gpiod_line_request_set_value(led->request,
                                         led->offset,
                                         GPIOD_LINE_VALUE_INACTIVE) != 0)
            perror("GPIO17 release 전 LED OFF");
        gpiod_line_request_release(led->request);
    }
    if (led->chip != NULL)
        gpiod_chip_close(led->chip);

    pthread_cond_destroy(&led->condition);
    pthread_mutex_destroy(&led->mutex);
    memset(led, 0, sizeof(*led));
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
    size_t i;

    for (i = 0; i < pool->capacity; ++i)
        free(pool->buffers[i].data);
    free(pool->free_buffers);
    free(pool->buffers);
    memset(pool, 0, sizeof(*pool));
}

/* Startup 이후의 pool ownership/refcount 변경은 ring mutex 안에서만 수행한다. */
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

static int frame_pool_add_reference_locked(
    struct frame_buffer_storage *storage)
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
    if (storage->refcount == 0) {
        pool->free_buffers[pool->free_count++] = storage;
    }

    return 0;
}

/*
 * Ring slot과 단일 active Snapshot이 공유할 NV12 memory를 모두 미리 할당한다.
 * Ring 640개 외의 reserve 320개는 Snapshot이 참조 중인 slot 교체에 사용한다.
 * Queue에 대기 중인 event별 buffer는 별도로 예약하지 않는다.
 */
static int ring_init(struct ring_buffer *ring, size_t capacity)
{
    pthread_condattr_t condition_attribute;
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
    if (pthread_condattr_init(&condition_attribute) != 0) {
        pthread_mutex_destroy(&ring->mutex);
        goto fail;
    }
    if (pthread_condattr_setclock(&condition_attribute, CLOCK_MONOTONIC) != 0 ||
        pthread_cond_init(&ring->frame_available, &condition_attribute) != 0) {
        pthread_condattr_destroy(&condition_attribute);
        pthread_mutex_destroy(&ring->mutex);
        goto fail;
    }
    pthread_condattr_destroy(&condition_attribute);

    return 0;

fail:
    frame_pool_destroy(&ring->pool);
    free(ring->slots);
    memset(ring, 0, sizeof(*ring));
    return -1;
}

/* 프로세스 종료 시 ring이 소유한 모든 frame 메모리를 반환한다. */
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

/*
 * libcamera callback이 호출하는 ring producer 함수다.
 * Unix epoch ns 차이가 20초를 넘은 frame을 먼저 제외한 후 head slot을 재사용한다.
 */
static int ring_push(struct ring_buffer *ring, const uint8_t *data,
                     size_t size, int64_t timestamp_ns)
{
    struct frame_slot *slot;
    struct frame_buffer_storage *replacement;

    if (data == NULL || size != NV12_FRAME_SIZE)
        return -1;

    pthread_mutex_lock(&ring->mutex);
    while (ring->count > 0) {
        /* head에서 count만큼 뒤로 이동하면 가장 오래된 유효 slot이 나온다. */
        size_t oldest = (ring->head + ring->capacity - ring->count) %
                        ring->capacity;
        if (timestamp_ns - ring->slots[oldest].timestamp_ns <=
            RING_RETENTION_NS)
            break;
        ring->slots[oldest].valid = 0;
        --ring->count;
    }

    /* Snapshot이 참조 중인 buffer는 덮어쓰지 않고 free buffer로 교체한다. */
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

/*
 * 기준 시각 직후 500ms 안의 첫 FrameWallClock을 찾는다.
 * 사고 후 2초와 500ms 여유까지 기다린 뒤 같은 구간을 AI와 저장에 사용한다.
 */
static int ring_wait_for_snapshot_end(struct ring_buffer *ring,
                                      int64_t detection_time_ns,
                                      int64_t *end_ns,
                                      int64_t wait_ns = FRAME_COVERAGE_TOLERANCE_NS)
{
    struct timespec deadline;
    int wait_result;
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
        size_t i;

        for (i = 0; i < ring->count; ++i) {
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

        wait_result = pthread_cond_timedwait(&ring->frame_available,
                                             &ring->mutex,
                                             &deadline);
        if (wait_result != 0)
            goto done;
    }

done:
    pthread_mutex_unlock(&ring->mutex);
    return result;
}

/* 종료 시 새 frame을 기다리는 worker가 termination 조건을 확인하게 한다. */
static void ring_wake_waiters(struct ring_buffer *ring)
{
    pthread_mutex_lock(&ring->mutex);
    pthread_cond_broadcast(&ring->frame_available);
    pthread_mutex_unlock(&ring->mutex);
}

/*
 * 호출자가 이미 ring mutex를 획득한 상태에서만 사용하는 내부 함수다.
 * Unix epoch ns인 begin_ns~end_ns 범위의 frame을 오래된 순서로 찾아 확인한다.
 *   1) 첫 frame이 begin에서 허용 오차 이상 늦지 않은가
 *   2) 인접 frame 사이에 허용 오차보다 큰 공백이 없는가
 *   3) 마지막 frame이 end 직전까지 도달했는가
 * first_out은 ring의 oldest 기준 상대 index이며 snapshot 복사 시작점으로 사용한다.
 */
static int ring_range_is_complete_locked(const struct ring_buffer *ring,
                                         int64_t begin_ns,
                                         int64_t end_ns,
                                         size_t *first_out,
                                         size_t *count_out)
{
    size_t oldest;
    size_t first = 0;
    size_t selected = 0;
    size_t i;
    int64_t previous_timestamp_ns = 0;

    *first_out = 0;
    *count_out = 0;

    /* 물리적인 배열 index와 무관하게 timestamp 오름차순으로 순회한다. */
    oldest = (ring->head + ring->capacity - ring->count) % ring->capacity;
    for (i = 0; i < ring->count; ++i) {
        const struct frame_slot *slot =
            &ring->slots[(oldest + i) % ring->capacity];

        if (!slot->valid || slot->timestamp_ns < begin_ns ||
            slot->timestamp_ns > end_ns)
            continue;

        if (selected == 0) {
            /* 첫 선택 frame은 begin 직후의 가장 가까운 frame이어야 한다. */
            first = i;
            if (slot->timestamp_ns >
                begin_ns + FRAME_COVERAGE_TOLERANCE_NS)
                return 0;
        } else if (slot->timestamp_ns - previous_timestamp_ns >
                   FRAME_COVERAGE_TOLERANCE_NS) {
            /* 중간에 frame이 충분히 긴 시간 누락됐다면 전체 구간으로 보지 않는다. */
            return 0;
        }

        previous_timestamp_ns = slot->timestamp_ns;
        ++selected;
    }

    /* 마지막 frame도 end 직전 허용 범위까지 도달해야 한다. */
    if (selected == 0 ||
        previous_timestamp_ns < end_ns - FRAME_COVERAGE_TOLERANCE_NS)
        return 0;

    *first_out = first;
    *count_out = selected;
    return 1;
}

/*
 * 선택 구간의 Frame Pool refcount를 획득해 Snapshot lifetime을 보장한다.
 * Mutex 안에서는 frame 본체를 복사하지 않고 pointer/metadata만 기록한다.
 */
static int ring_copy_snapshot(struct ring_buffer *ring,
                              int64_t begin_ns,
                              int64_t end_ns,
                              struct video_snapshot *snapshot)
{
    size_t oldest;
    size_t first;
    size_t count;
    size_t i;
    int complete;

    memset(snapshot, 0, sizeof(*snapshot));

    pthread_mutex_lock(&ring->mutex);
    complete = ring_range_is_complete_locked(ring, begin_ns, end_ns,
                                             &first, &count);
    if (!complete) {
        pthread_mutex_unlock(&ring->mutex);
        return -1;
    }

    /* Snapshot은 frame 본체 대신 view 배열만 할당한다. */
    if (count > SIZE_MAX / sizeof(*snapshot->frames)) {
        pthread_mutex_unlock(&ring->mutex);
        return -1;
    }

    snapshot->frames =
        (struct frame_view *)calloc(count, sizeof(*snapshot->frames));
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

/* AI/인코딩 처리가 끝난 event의 snapshot 전체를 반환한다. */
static void snapshot_destroy(struct ring_buffer *ring,
                             struct video_snapshot *snapshot)
{
    size_t i;

    pthread_mutex_lock(&ring->mutex);
    for (i = 0; i < snapshot->count; ++i) {
        if (frame_pool_release_locked(&ring->pool,
                                      snapshot->frames[i].storage) != 0)
            fprintf(stderr, "snapshot frame refcount 반환 실패\n");
    }
    pthread_mutex_unlock(&ring->mutex);

    free(snapshot->frames);
    memset(snapshot, 0, sizeof(*snapshot));
}

/* FIFO queue의 mutex와 condition variable을 초기화한다. */
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

/*
 * 더 이상 새 event가 들어오지 않음을 알린다.
 * broadcast는 event를 기다리며 잠든 worker가 종료 조건을 확인하도록 깨운다.
 */
static void event_queue_close(struct event_queue *queue)
{
    pthread_mutex_lock(&queue->mutex);
    queue->closed = 1;
    pthread_cond_broadcast(&queue->available);
    pthread_mutex_unlock(&queue->mutex);
}

/* Receiver 전용 producer 동작: 새 event를 FIFO tail에 저장한다. */
static int event_queue_push(struct event_queue *queue,
                            const inference_event_t *event)
{
    inference_event_t dropped_event;
    int event_dropped = 0;

    pthread_mutex_lock(&queue->mutex);
    if (queue->closed) {
        pthread_mutex_unlock(&queue->mutex);
        return -1;
    }

    /* 처리 중인 event가 아니라 queue에서 가장 오래 기다린 head만 제거한다. */
    if (queue->count == EVENT_QUEUE_CAPACITY) {
        dropped_event = queue->events[queue->head];
        queue->head = (queue->head + 1) % EVENT_QUEUE_CAPACITY;
        --queue->count;
        event_dropped = 1;
    }

    queue->events[queue->tail] = *event;
    queue->tail = (queue->tail + 1) % EVENT_QUEUE_CAPACITY;
    ++queue->count;

    /* 잠든 worker 한 개를 깨워 새 event를 소비하게 한다. */
    pthread_cond_signal(&queue->available);
    pthread_mutex_unlock(&queue->mutex);

    if (event_dropped) {
        fprintf(stderr,
                "event queue full: 가장 오래된 event drop "
                "(station=%" PRIu32 ", detection_ns=%" PRId64 ")\n",
                dropped_event.station_id,
                dropped_event.detection_time);
    }

    return 0;
}

/*
 * Worker 전용 consumer 동작이다.
 * 반환값 1은 event 획득, 0은 queue가 닫혔고 남은 event도 없다는 뜻이다.
 */
static int event_queue_pop(struct event_queue *queue,
                           inference_event_t *event)
{
    pthread_mutex_lock(&queue->mutex);
    /* Spurious wakeup에 대비해 조건을 while로 다시 검사한다. */
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

    /* head에서 하나를 제거하므로 입력 순서와 처리 순서가 같다. */
    *event = queue->events[queue->head];
    queue->head = (queue->head + 1) % EVENT_QUEUE_CAPACITY;
    --queue->count;
    pthread_mutex_unlock(&queue->mutex);

    return 1;
}

/* Thread join 이후 queue 동기화 객체를 정리한다. */
static void event_queue_destroy(struct event_queue *queue)
{
    pthread_cond_destroy(&queue->available);
    pthread_mutex_destroy(&queue->mutex);
    memset(queue, 0, sizeof(*queue));
}

/*
 * 공용 IPC server/client fd와 shutdown self-pipe를 함께 poll한다.
 * 실제 accept와 Event 수신은 ipc_server_accept()/ipc_recv()가 담당한다.
 */
static int wait_for_socket_or_shutdown(int socket_fd)
{
    struct pollfd descriptors[2];
    int poll_result;

    for (;;) {
        if (termination_requested())
            return 0;

        descriptors[0].fd = socket_fd;
        descriptors[0].events = POLLIN;
        descriptors[0].revents = 0;
        descriptors[1].fd = shutdown_pipe[0];
        descriptors[1].events = POLLIN;
        descriptors[1].revents = 0;

        poll_result = poll(descriptors, 2, 1000);
        if (poll_result < 0 && errno == EINTR)
            continue;
        if (poll_result < 0) {
            perror("event socket poll");
            return -1;
        }

        ai_progress(AI_RECEIVER);
        if (termination_requested() || (descriptors[1].revents & POLLIN))
            return 0;
        if (descriptors[0].revents & POLLIN)
            return 1;
        if (descriptors[0].revents & (POLLERR | POLLHUP | POLLNVAL)) {
            fprintf(stderr, "event socket poll 상태 오류\n");
            return -1;
        }
    }
}

enum event_read_result {
    EVENT_READ_FATAL = -1,
    EVENT_READ_SUCCESS = 0,
    EVENT_READ_DISCONNECTED = 1,
    EVENT_READ_SHUTDOWN = 2
};

static enum event_read_result
read_inference_event(inference_event_t *event)
{
    ipc_header_t header;
    int client_fd;
    int receive_result;
    int wait_result;

    if (event_server_fd < 0) {
        event_server_fd = ipc_server_create(INFERENCE_SOCKET_PATH);
        if (event_server_fd < 0) {
            event_server_fd = -1;
            return EVENT_READ_FATAL;
        }

        printf("사고 event socket 대기: %s\n", INFERENCE_SOCKET_PATH);
    }

    wait_result = wait_for_socket_or_shutdown(event_server_fd);
    if (wait_result == 0)
        return EVENT_READ_SHUTDOWN;
    if (wait_result < 0)
        return EVENT_READ_FATAL;

    client_fd = ipc_server_accept(event_server_fd);
    if (client_fd < 0) {
        if (termination_requested())
            return EVENT_READ_SHUTDOWN;
        return EVENT_READ_FATAL;
    }

    memset(event, 0, sizeof(*event));
    memset(&header, 0, sizeof(header));
    receive_result = ai_receive_event(client_fd, &header, event, sizeof(*event),
                                      shutdown_pipe[0], termination_requested);
    ipc_close(client_fd);

    if (receive_result != IPC_SUCCESS) {
        if (termination_requested())
            return EVENT_READ_SHUTDOWN;
        fprintf(stderr, "Filter inference event 수신 실패: %d\n",
                receive_result);
        return EVENT_READ_DISCONNECTED;
    }
    if (header.type != IPC_MSG_INFERENCE_EVENT ||
        header.length != sizeof(*event) ||
        header.sequence != event->sequence_number) {
        fprintf(stderr,
                "Filter inference event 계약 불일치: type=%u length=%u\n",
                header.type, header.length);
        return EVENT_READ_DISCONNECTED;
    }
    if (event->detection_time <= 0 ||
        event->detection_time > INT64_MAX - ACCIDENT_VIDEO_AFTER_NS -
                                      FRAME_COVERAGE_TOLERANCE_NS) {
        fprintf(stderr, "Filter inference event 사고 시각 범위 오류\n");
        return EVENT_READ_DISCONNECTED;
    }

    printf("사고 event 수신: station=%" PRIu32
           ", sequence=%" PRIu16 ", detection_ns=%" PRId64 "\n",
           event->station_id,
           event->sequence_number,
           event->detection_time);

    return EVENT_READ_SUCCESS;
}

/*
 * Filter 하나와 Inference 하나의 고정 1:1 공용 IPC다. Event 대기에 timeout을
 * 두지 않으며 disconnect/잘못된 payload는 해당 client만 닫고 재연결을 기다린다.
 */
static void *event_receiver_thread(void *argument)
{
    struct event_queue *queue = (struct event_queue *)argument;
    inference_event_t event;

    while (!termination_requested()) {
        enum event_read_result read_result = read_inference_event(&event);

        if (read_result == EVENT_READ_DISCONNECTED)
            continue;
        if (read_result == EVENT_READ_SHUTDOWN)
            break;
        if (read_result == EVENT_READ_FATAL) {
            request_runtime_failure();
            break;
        }

        if (event_queue_push(queue, &event) != 0) {
            fprintf(stderr, "사고 event queue 저장 실패\n");
            request_runtime_failure();
            break;
        }
    }

    if (event_server_fd >= 0) {
        ipc_close(event_server_fd);
        event_server_fd = -1;
        unlink(INFERENCE_SOCKET_PATH);
    }

    /* 종료 요청/수신 오류 이후 worker가 queue를 모두 비우고 종료하게 한다. */
    event_queue_close(queue);
    return NULL;
}

/* Confidence filtering과 NMS 이후 원본 frame 좌표계로 복원된 detection이다. */
struct detection {
    float x;
    float y;
    float width;
    float height;
    float score;
    int class_id;
};

/* YOLOX grid/stride decode 직후 model input 좌표계의 cx/cy/width/height다. */
struct decoded_box {
    float center_x;
    float center_y;
    float width;
    float height;
};

/* 현재 정책은 station 1을 아이오닉(class 0)에 연결하고 나머지는 거부한다. */
static int target_class_from_station_id(uint32_t station_id)
{
    if (station_id == 1)
        return 0;

    return -1;
}

/*
 * ONNX Runtime object와 전처리/postprocessing 재사용 buffer를 소유한다.
 * 객체와 session은 프로세스 시작 시 한 번만 만들고 worker가 모든 frame에 재사용한다.
 */
class YoloXDetector {
public:
    YoloXDetector();

    int initialize(void);
    enum inference_result run(const struct video_snapshot *snapshot,
                              int target_class);

private:
    static uint8_t clamp_to_byte(int value);
    int validate_model_contract(void);
    int preprocess_frame(const struct frame_view *frame, float *scale_out);
    void decode_raw_predictions(const float *raw);
    void filter_and_nms(const float *raw, float scale);
    static float detection_iou(const struct detection &first,
                               const struct detection &second);
    int run_frame(const struct frame_view *frame, int target_class,
                  int *frame_target_found);

    std::unique_ptr<Ort::Env> environment_;
    std::unique_ptr<Ort::SessionOptions> session_options_;
    std::unique_ptr<Ort::Session> session_;
    std::unique_ptr<Ort::MemoryInfo> memory_info_;
    std::vector<uint8_t> bgr_buffer_;
    std::vector<uint8_t> letterbox_buffer_;
    std::vector<float> input_buffer_;
    std::vector<struct decoded_box> decoded_boxes_;
    std::vector<struct detection> candidates_;
    std::vector<struct detection> detections_;
    std::vector<size_t> nms_indexes_;
    std::vector<uint8_t> nms_suppressed_;
};

YoloXDetector::YoloXDetector()
    : bgr_buffer_(CAMERA_WIDTH * CAMERA_HEIGHT * 3U),
      letterbox_buffer_(MODEL_INPUT_WIDTH * MODEL_INPUT_HEIGHT * 3U),
      input_buffer_(MODEL_INPUT_WIDTH * MODEL_INPUT_HEIGHT * 3U),
      decoded_boxes_(MODEL_PREDICTION_COUNT)
{
    candidates_.reserve(MODEL_PREDICTION_COUNT);
    detections_.reserve(MODEL_PREDICTION_COUNT);
    nms_indexes_.reserve(MODEL_PREDICTION_COUNT);
    nms_suppressed_.reserve(MODEL_PREDICTION_COUNT);
}

int YoloXDetector::initialize(void)
{
    /* 모델 부재는 compile 문제가 아니라 process 시작 시의 명확한 runtime 오류다. */
    if (access(MODEL_PATH, R_OK) != 0) {
        fprintf(stderr, "ONNX model을 읽을 수 없음: %s\n", MODEL_PATH);
        return -1;
    }

    try {
        environment_ = std::make_unique<Ort::Env>(ORT_LOGGING_LEVEL_WARNING,
                                                   "vehicle_detector");
        session_options_ = std::make_unique<Ort::SessionOptions>();
        session_ = std::make_unique<Ort::Session>(*environment_,
                                                  MODEL_PATH,
                                                  *session_options_);
        memory_info_ = std::make_unique<Ort::MemoryInfo>(
            Ort::MemoryInfo::CreateCpu(OrtArenaAllocator, OrtMemTypeDefault));

        return validate_model_contract();
    } catch (const Ort::Exception &error) {
        fprintf(stderr, "ONNX Runtime 초기화 실패: %s\n", error.what());
        return -1;
    } catch (const std::exception &error) {
        fprintf(stderr, "ONNX detector 초기화 실패: %s\n", error.what());
        return -1;
    }
}

int YoloXDetector::validate_model_contract(void)
{
    /* Export 계약과 다른 모델이 조용히 실행되지 않도록 이름/type/shape를 모두 검사한다. */
    static const std::array<int64_t, 4> expected_input_shape = {
        1, 3, MODEL_INPUT_HEIGHT, MODEL_INPUT_WIDTH
    };
    static const std::array<int64_t, 3> expected_output_shape = {
        1, MODEL_PREDICTION_COUNT, MODEL_OUTPUT_FIELDS
    };
    Ort::AllocatorWithDefaultOptions allocator;
    std::vector<int64_t> input_shape;
    std::vector<int64_t> output_shape;

    if (session_->GetInputCount() != 1 || session_->GetOutputCount() != 1) {
        fprintf(stderr, "ONNX tensor 개수 불일치: input=%zu, output=%zu\n",
                session_->GetInputCount(), session_->GetOutputCount());
        return -1;
    }

    auto input_name = session_->GetInputNameAllocated(0, allocator);
    auto output_name = session_->GetOutputNameAllocated(0, allocator);
    if (input_name == nullptr || strcmp(input_name.get(), MODEL_INPUT_NAME) != 0) {
        fprintf(stderr, "ONNX input tensor name 불일치: expected=%s, actual=%s\n",
                MODEL_INPUT_NAME, input_name == nullptr ? "(null)" : input_name.get());
        return -1;
    }
    if (output_name == nullptr ||
        strcmp(output_name.get(), MODEL_OUTPUT_NAME) != 0) {
        fprintf(stderr, "ONNX output tensor name 불일치: expected=%s, actual=%s\n",
                MODEL_OUTPUT_NAME,
                output_name == nullptr ? "(null)" : output_name.get());
        return -1;
    }

    /* TensorTypeAndShapeInfo는 TypeInfo 내부 데이터를 참조하므로,
     * dtype과 shape 검사가 끝날 때까지 TypeInfo의 lifetime을 유지한다. */
    Ort::TypeInfo input_type_info = session_->GetInputTypeInfo(0);
    auto input_tensor_info = input_type_info.GetTensorTypeAndShapeInfo();
    ONNXTensorElementDataType input_element_type =
        input_tensor_info.GetElementType();
    if (input_element_type != ONNX_TENSOR_ELEMENT_DATA_TYPE_FLOAT) {
        fprintf(stderr,
                "ONNX input dtype 불일치: expected=%d(float32), actual=%d\n",
                (int)ONNX_TENSOR_ELEMENT_DATA_TYPE_FLOAT,
                (int)input_element_type);
        return -1;
    }
    input_shape = input_tensor_info.GetShape();
    if (!std::equal(input_shape.begin(), input_shape.end(),
                    expected_input_shape.begin(), expected_input_shape.end())) {
        fprintf(stderr, "ONNX input shape 불일치: [1,3,480,640]이 필요함\n");
        return -1;
    }

    Ort::TypeInfo output_type_info = session_->GetOutputTypeInfo(0);
    auto output_tensor_info = output_type_info.GetTensorTypeAndShapeInfo();
    ONNXTensorElementDataType output_element_type =
        output_tensor_info.GetElementType();
    if (output_element_type != ONNX_TENSOR_ELEMENT_DATA_TYPE_FLOAT) {
        fprintf(stderr,
                "ONNX output dtype 불일치: expected=%d(float32), actual=%d\n",
                (int)ONNX_TENSOR_ELEMENT_DATA_TYPE_FLOAT,
                (int)output_element_type);
        return -1;
    }
    output_shape = output_tensor_info.GetShape();
    if (!std::equal(output_shape.begin(), output_shape.end(),
                    expected_output_shape.begin(), expected_output_shape.end())) {
        fprintf(stderr, "ONNX output shape 불일치: [1,6300,7]이 필요함\n");
        return -1;
    }

    return 0;
}

uint8_t YoloXDetector::clamp_to_byte(int value)
{
    if (value < 0)
        return 0;
    if (value > 255)
        return 255;

    return (uint8_t)value;
}

int YoloXDetector::preprocess_frame(const struct frame_view *frame,
                                    float *scale_out)
{
    const uint8_t *y_plane;
    const uint8_t *uv_plane;
    unsigned int resized_width;
    unsigned int resized_height;
    float scale;
    unsigned int y;
    unsigned int x;

    if (frame == NULL || frame->data == NULL ||
        frame->size != NV12_FRAME_SIZE || scale_out == NULL) {
        fprintf(stderr, "YOLOX preprocessing 입력 frame이 잘못됨\n");
        return -1;
    }

    y_plane = frame->data;
    uv_plane = frame->data + CAMERA_WIDTH * CAMERA_HEIGHT;

    /* Camera에서 보장한 Rec.709 limited-range NV12를 packed BGR로 변환한다. */
    for (y = 0; y < CAMERA_HEIGHT; ++y) {
        for (x = 0; x < CAMERA_WIDTH; ++x) {
            size_t pixel = (size_t)y * CAMERA_WIDTH + x;
            size_t chroma = (size_t)(y / 2U) * CAMERA_WIDTH + (x & ~1U);
            int luminance = std::max(0, (int)y_plane[pixel] - 16);
            int blue_chroma = (int)uv_plane[chroma] - 128;
            int red_chroma = (int)uv_plane[chroma + 1] - 128;
            int red = (298 * luminance + 459 * red_chroma + 128) >> 8;
            int green = (298 * luminance - 55 * blue_chroma -
                         136 * red_chroma + 128) >> 8;
            int blue = (298 * luminance + 541 * blue_chroma + 128) >> 8;

            bgr_buffer_[pixel * 3U] = clamp_to_byte(blue);
            bgr_buffer_[pixel * 3U + 1U] = clamp_to_byte(green);
            bgr_buffer_[pixel * 3U + 2U] = clamp_to_byte(red);
        }
    }

    scale = std::min((float)MODEL_INPUT_HEIGHT / CAMERA_HEIGHT,
                     (float)MODEL_INPUT_WIDTH / CAMERA_WIDTH);
    resized_width = (unsigned int)(CAMERA_WIDTH * scale);
    resized_height = (unsigned int)(CAMERA_HEIGHT * scale);
    if (resized_width == 0 || resized_height == 0 ||
        resized_width > MODEL_INPUT_WIDTH || resized_height > MODEL_INPUT_HEIGHT) {
        fprintf(stderr, "YOLOX preprocessing resize 크기가 잘못됨\n");
        return -1;
    }

    /* Python reference처럼 uint8 BGR canvas를 먼저 114로 채운다. */
    std::fill(letterbox_buffer_.begin(), letterbox_buffer_.end(), 114U);

    if (resized_width == CAMERA_WIDTH && resized_height == CAMERA_HEIGHT) {
        memcpy(letterbox_buffer_.data(), bgr_buffer_.data(),
               bgr_buffer_.size());
    } else {
        float horizontal_scale = (float)CAMERA_WIDTH / resized_width;
        float vertical_scale = (float)CAMERA_HEIGHT / resized_height;

        /* Python reference의 INTER_LINEAR에 대응하는 half-pixel bilinear resize다. */
        for (y = 0; y < resized_height; ++y) {
            float source_y = ((float)y + 0.5f) * vertical_scale - 0.5f;
            int y0 = (int)floorf(source_y);
            int y1;
            float y_weight;

            if (y0 < 0)
                y0 = 0;
            y1 = std::min(y0 + 1, (int)CAMERA_HEIGHT - 1);
            y_weight = std::max(0.0f, source_y - floorf(source_y));

            for (x = 0; x < resized_width; ++x) {
                float source_x = ((float)x + 0.5f) * horizontal_scale - 0.5f;
                int x0 = (int)floorf(source_x);
                int x1;
                float x_weight;
                size_t destination = (size_t)y * MODEL_INPUT_WIDTH + x;
                unsigned int channel;

                if (x0 < 0)
                    x0 = 0;
                x1 = std::min(x0 + 1, (int)CAMERA_WIDTH - 1);
                x_weight = std::max(0.0f, source_x - floorf(source_x));

                for (channel = 0; channel < 3U; ++channel) {
                    float top_left = bgr_buffer_[
                        ((size_t)y0 * CAMERA_WIDTH + x0) * 3U + channel];
                    float top_right = bgr_buffer_[
                        ((size_t)y0 * CAMERA_WIDTH + x1) * 3U + channel];
                    float bottom_left = bgr_buffer_[
                        ((size_t)y1 * CAMERA_WIDTH + x0) * 3U + channel];
                    float bottom_right = bgr_buffer_[
                        ((size_t)y1 * CAMERA_WIDTH + x1) * 3U + channel];
                    float top = top_left + (top_right - top_left) * x_weight;
                    float bottom = bottom_left +
                                   (bottom_right - bottom_left) * x_weight;
                    float value = top + (bottom - top) * y_weight;

                    letterbox_buffer_[destination * 3U + channel] =
                        clamp_to_byte((int)lroundf(value));
                }
            }
        }
    }

    /* HWC uint8 BGR을 normalization 없이 FP32 NCHW로 변환한다. */
    for (y = 0; y < MODEL_INPUT_HEIGHT; ++y) {
        for (x = 0; x < MODEL_INPUT_WIDTH; ++x) {
            size_t source = ((size_t)y * MODEL_INPUT_WIDTH + x) * 3U;
            size_t destination = (size_t)y * MODEL_INPUT_WIDTH + x;

            input_buffer_[destination] = (float)letterbox_buffer_[source];
            input_buffer_[MODEL_INPUT_WIDTH * MODEL_INPUT_HEIGHT +
                          destination] = (float)letterbox_buffer_[source + 1U];
            input_buffer_[2U * MODEL_INPUT_WIDTH * MODEL_INPUT_HEIGHT +
                          destination] = (float)letterbox_buffer_[source + 2U];
        }
    }

    *scale_out = scale;
    return 0;
}

void YoloXDetector::decode_raw_predictions(const float *raw)
{
    /* Export 순서와 동일하게 stride 8/16/32의 grid를 각각 row-major로 순회한다. */
    static const unsigned int strides[] = {8U, 16U, 32U};
    size_t prediction = 0;
    unsigned int stride_index;

    for (stride_index = 0;
         stride_index < sizeof(strides) / sizeof(strides[0]);
         ++stride_index) {
        unsigned int stride = strides[stride_index];
        unsigned int grid_height = MODEL_INPUT_HEIGHT / stride;
        unsigned int grid_width = MODEL_INPUT_WIDTH / stride;
        unsigned int grid_y;
        unsigned int grid_x;

        for (grid_y = 0; grid_y < grid_height; ++grid_y) {
            for (grid_x = 0; grid_x < grid_width; ++grid_x) {
                const float *row = raw + prediction * MODEL_OUTPUT_FIELDS;

                decoded_boxes_[prediction].center_x =
                    (row[0] + grid_x) * stride;
                decoded_boxes_[prediction].center_y =
                    (row[1] + grid_y) * stride;
                decoded_boxes_[prediction].width = expf(row[2]) * stride;
                decoded_boxes_[prediction].height = expf(row[3]) * stride;
                ++prediction;
            }
        }
    }
}

float YoloXDetector::detection_iou(const struct detection &first,
                                   const struct detection &second)
{
    /* x/y/width/height 형식의 두 box가 겹치는 비율을 NMS용으로 계산한다. */
    float intersection_x1 = std::max(first.x, second.x);
    float intersection_y1 = std::max(first.y, second.y);
    float intersection_x2 = std::min(first.x + first.width,
                                     second.x + second.width);
    float intersection_y2 = std::min(first.y + first.height,
                                     second.y + second.height);
    float intersection_width = std::max(0.0f, intersection_x2 - intersection_x1);
    float intersection_height = std::max(0.0f, intersection_y2 - intersection_y1);
    float intersection = intersection_width * intersection_height;
    float union_area = first.width * first.height +
                       second.width * second.height - intersection;

    return union_area > 0.0f ? intersection / union_area : 0.0f;
}

void YoloXDetector::filter_and_nms(const float *raw, float scale)
{
    /* objectness와 두 class 중 최고 확률의 곱이 설정 threshold 이상일 때만 남긴다. */
    candidates_.clear();
    detections_.clear();

    for (size_t prediction = 0;
         prediction < MODEL_PREDICTION_COUNT;
         ++prediction) {
        const float *row = raw + prediction * MODEL_OUTPUT_FIELDS;
        int class_id = 0;
        float class_score = row[5];

        for (unsigned int class_index = 1;
             class_index < MODEL_CLASS_COUNT;
             ++class_index) {
            if (row[5U + class_index] > class_score) {
                class_score = row[5U + class_index];
                class_id = (int)class_index;
            }
        }

        float score = row[4] * class_score;
        if (score >= INFERENCE_CONFIDENCE_THRESHOLD) {
            const struct decoded_box &box = decoded_boxes_[prediction];
            candidates_.push_back({
                (box.center_x - box.width / 2.0f) / scale,
                (box.center_y - box.height / 2.0f) / scale,
                box.width / scale,
                box.height / scale,
                score,
                class_id
            });
        }
    }

    /* 아이오닉과 나머지 차량이 서로를 지우지 않도록 두 class별 독립 NMS를 수행한다. */
    for (int class_id = 0; class_id < (int)MODEL_CLASS_COUNT; ++class_id) {
        size_t kept_position;

        nms_indexes_.clear();
        for (size_t index = 0; index < candidates_.size(); ++index)
            if (candidates_[index].class_id == class_id)
                nms_indexes_.push_back(index);

        std::stable_sort(nms_indexes_.begin(), nms_indexes_.end(),
                         [this](size_t first, size_t second) {
                             return candidates_[first].score >
                                    candidates_[second].score;
                         });
        nms_suppressed_.assign(nms_indexes_.size(), 0);

        for (kept_position = 0;
             kept_position < nms_indexes_.size();
             ++kept_position) {
            const struct detection *kept;

            if (nms_suppressed_[kept_position])
                continue;
            kept = &candidates_[nms_indexes_[kept_position]];
            detections_.push_back(*kept);
            for (size_t candidate_position = kept_position + 1;
                 candidate_position < nms_indexes_.size();
                 ++candidate_position) {
                if (!nms_suppressed_[candidate_position] &&
                    detection_iou(
                        *kept,
                        candidates_[nms_indexes_[candidate_position]]) >
                        INFERENCE_NMS_IOU_THRESHOLD)
                    nms_suppressed_[candidate_position] = 1;
            }
        }
    }

    std::stable_sort(detections_.begin(), detections_.end(),
                     [](const struct detection &first,
                        const struct detection &second) {
                         return first.score > second.score;
                     });
}

int YoloXDetector::run_frame(const struct frame_view *frame,
                             int target_class,
                             int *frame_target_found)
{
    ai_progress(AI_WORKER);
    /* 한 frame을 전처리하고 기존 session으로 실행한 뒤 decode/filter/NMS한다. */
    static const std::array<int64_t, 4> input_shape = {
        1, 3, MODEL_INPUT_HEIGHT, MODEL_INPUT_WIDTH
    };
    const char *input_names[] = {MODEL_INPUT_NAME};
    const char *output_names[] = {MODEL_OUTPUT_NAME};
    float scale;

    *frame_target_found = 0;
    if (preprocess_frame(frame, &scale) != 0)
        return -1;

    try {
        Ort::Value input_tensor = Ort::Value::CreateTensor<float>(
            *memory_info_, input_buffer_.data(), input_buffer_.size(),
            input_shape.data(), input_shape.size());
        ai_progress(AI_WORKER);
        std::vector<Ort::Value> outputs = session_->Run(
            Ort::RunOptions{nullptr}, input_names, &input_tensor, 1,
            output_names, 1);

        if (outputs.size() != 1 || !outputs[0].IsTensor()) {
            fprintf(stderr, "ONNX inference output tensor가 잘못됨\n");
            return -1;
        }

        Ort::TensorTypeAndShapeInfo output_info =
            outputs[0].GetTensorTypeAndShapeInfo();
        std::vector<int64_t> output_shape = output_info.GetShape();
        if (output_info.GetElementType() != ONNX_TENSOR_ELEMENT_DATA_TYPE_FLOAT ||
            output_shape != std::vector<int64_t>({1, MODEL_PREDICTION_COUNT,
                                                  MODEL_OUTPUT_FIELDS})) {
            fprintf(stderr, "ONNX inference output 계약 불일치\n");
            return -1;
        }

        ai_progress(AI_WORKER);
        const float *raw = outputs[0].GetTensorData<float>();
        decode_raw_predictions(raw);
        filter_and_nms(raw, scale);

        for (const struct detection &item : detections_) {
            if (item.class_id == target_class) {
                *frame_target_found = 1;
                break;
            }
        }
    } catch (const Ort::Exception &error) {
        fprintf(stderr, "ONNX inference 실행 실패: %s\n", error.what());
        return -1;
    } catch (const std::exception &error) {
        fprintf(stderr, "YOLOX frame 처리 실패: %s\n", error.what());
        return -1;
    }

    return 0;
}

enum inference_result YoloXDetector::run(
    const struct video_snapshot *snapshot, int target_class)
{
    std::array<int, 3> recent_detections = {0, 0, 0};
    size_t recent_index = 0;
    size_t recent_count = 0;
    unsigned int recent_positive_count = 0;
    size_t i;

    if (snapshot == NULL || snapshot->frames == NULL || snapshot->count == 0) {
        fprintf(stderr, "YOLOX inference snapshot이 비어 있음\n");
        return INFERENCE_ERROR;
    }

    /* 최근 3회 중 2회 target 검출이 확정되면 나머지 frame을 생략한다. */
    for (i = INFERENCE_FRAME_STEP - 1;
         i < snapshot->count;
         i += INFERENCE_FRAME_STEP) {
        int frame_target_found;

        if (run_frame(&snapshot->frames[i], target_class,
                      &frame_target_found) != 0)
            return INFERENCE_ERROR;

        if (recent_count == recent_detections.size())
            recent_positive_count -= recent_detections[recent_index];
        else
            ++recent_count;
        recent_detections[recent_index] = frame_target_found;
        recent_positive_count += frame_target_found;
        recent_index = (recent_index + 1) % recent_detections.size();

        if (recent_count == recent_detections.size() &&
            recent_positive_count >= 2U)
            return INFERENCE_FOUND;
    }

    return INFERENCE_NOT_FOUND;
}

static enum inference_result
run_vehicle_inference(YoloXDetector *detector,
                      const struct video_snapshot *snapshot,
                      int target_class)
{
    /* Worker와 ONNX Runtime wrapper 사이의 판정 경계를 유지한다. */
    return detector->run(snapshot, target_class);
}

/*
 * libcamera API와 callback 수명을 한곳에 제한하기 위한 최소 C++ wrapper다.
 * Ring, queue, worker, inference, encoder 로직은 이 클래스에 포함하지 않는다.
 */
class CameraCapture {
public:
    explicit CameraCapture(struct ring_buffer *ring);
    ~CameraCapture();

    int start(void);
    void stop(void);
    int failed(void) const;

private:
    struct mapped_plane {
        /* mmap 해제에는 page-aligned base와 전체 mapped_size가 필요하다. */
        int fd;
        void *base;
        size_t mapped_size;
        const uint8_t *data;
        size_t data_size;
    };

    struct mapped_buffer {
        /* 어느 libcamera FrameBuffer에 대한 mapping인지 연결한다. */
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
    /* Stride/padding을 제거한 한 장의 packed NV12를 만드는 재사용 buffer다. */
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
    /* mmap offset은 page 경계에 정렬되어야 한다. */
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

        /* NV12가 한 plane 또는 Y/UV 두 plane으로 제공되는 경우를 모두 저장한다. */
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
            fprintf(stderr, "camera buffer mapping 등록 실패: %s\n",
                    error.what());
            unmap_mapping(&mapping);
            unmap_buffers();
            return -1;
        }
    }

    return 0;
}

void CameraCapture::unmap_buffers(void)
{
    /* map_buffers()에서 성공적으로 등록한 모든 mapping을 해제한다. */
    for (auto &mapping : mappings_)
        for (auto &plane : mapping.planes)
            munmap(plane.base, plane.mapped_size);
    mappings_.clear();
}

int CameraCapture::copy_nv12(const libcamera::FrameBuffer *buffer,
                             const libcamera::FrameMetadata &metadata,
                             uint8_t *dst)
{
    const struct mapped_buffer *mapping = NULL;
    libcamera::Span<const libcamera::FrameMetadata::Plane> metadata_planes =
        metadata.planes();
    int synchronized_fds[2];
    size_t synchronized_count = 0;
    size_t plane_limit;
    int copy_result = -1;
    unsigned int row;

    for (const auto &item : mappings_) {
        if (item.buffer == buffer) {
            mapping = &item;
            break;
        }
    }

    if (mapping == NULL || mapping->planes.empty() || stride_ < CAMERA_WIDTH)
        return -1;

    if (mapping->planes.size() >= 2) {
        /* 할당 length와 이번 frame의 bytesused를 모두 확인한다. */
        if (mapping->planes[0].data_size < stride_ * CAMERA_HEIGHT ||
            mapping->planes[1].data_size < stride_ * CAMERA_HEIGHT / 2U ||
            metadata_planes.size() < 2 ||
            metadata_planes[0].bytesused < stride_ * CAMERA_HEIGHT ||
            metadata_planes[1].bytesused < stride_ * CAMERA_HEIGHT / 2U) {
            fprintf(stderr, "camera NV12 multi-plane bytesused 부족\n");
            return -1;
        }
        plane_limit = 2;
    } else {
        if (mapping->planes[0].data_size <
                stride_ * CAMERA_HEIGHT * 3U / 2U ||
            metadata_planes.empty() ||
            metadata_planes[0].bytesused <
                stride_ * CAMERA_HEIGHT * 3U / 2U) {
            fprintf(stderr, "camera NV12 single-plane bytesused 부족\n");
            return -1;
        }
        plane_limit = 1;
    }

    /* 같은 fd를 공유하는 plane은 하나의 DMA-BUF CPU read 구간으로 묶는다. */
    for (size_t i = 0; i < plane_limit; ++i) {
        struct dma_buf_sync sync;
        int duplicate_fd = 0;

        for (size_t previous = 0; previous < synchronized_count; ++previous)
            if (synchronized_fds[previous] == mapping->planes[i].fd) {
                duplicate_fd = 1;
                break;
            }
        if (duplicate_fd)
            continue;

        memset(&sync, 0, sizeof(sync));
        sync.flags = DMA_BUF_SYNC_START | DMA_BUF_SYNC_READ;
        if (ioctl(mapping->planes[i].fd, DMA_BUF_IOCTL_SYNC, &sync) != 0) {
            perror("DMA-BUF CPU read START");
            goto sync_end;
        }
        synchronized_fds[synchronized_count++] = mapping->planes[i].fd;
    }

    if (mapping->planes.size() >= 2) {
        /* Multi-plane NV12: Y와 UV plane의 시작 주소가 각각 존재한다. */
        for (row = 0; row < CAMERA_HEIGHT; ++row)
            memcpy(dst + row * CAMERA_WIDTH,
                   mapping->planes[0].data + row * stride_, CAMERA_WIDTH);
        for (row = 0; row < CAMERA_HEIGHT / 2U; ++row)
            memcpy(dst + CAMERA_WIDTH * CAMERA_HEIGHT + row * CAMERA_WIDTH,
                   mapping->planes[1].data + row * stride_, CAMERA_WIDTH);
    } else {
        /* Single-plane NV12: Y 바로 뒤에 UV가 이어진다. */
        for (row = 0; row < CAMERA_HEIGHT; ++row)
            memcpy(dst + row * CAMERA_WIDTH,
                   mapping->planes[0].data + row * stride_, CAMERA_WIDTH);
        for (row = 0; row < CAMERA_HEIGHT / 2U; ++row)
            memcpy(dst + CAMERA_WIDTH * CAMERA_HEIGHT + row * CAMERA_WIDTH,
                   mapping->planes[0].data + stride_ * CAMERA_HEIGHT +
                   row * stride_, CAMERA_WIDTH);
    }
    copy_result = 0;

sync_end:
    while (synchronized_count > 0) {
        struct dma_buf_sync sync;

        memset(&sync, 0, sizeof(sync));
        sync.flags = DMA_BUF_SYNC_END | DMA_BUF_SYNC_READ;
        --synchronized_count;
        if (ioctl(synchronized_fds[synchronized_count],
                  DMA_BUF_IOCTL_SYNC, &sync) != 0) {
            perror("DMA-BUF CPU read END");
            copy_result = -1;
        }
    }

    return copy_result;
}

/* Copy까지 성공한 정상 frame만 camera watchdog의 수신 시각으로 기록한다. */
void CameraCapture::record_normal_frame(void)
{
    struct timespec now;
    int64_t now_ns;

    if (clock_gettime(CLOCK_MONOTONIC, &now) != 0) {
        perror("camera watchdog CLOCK_MONOTONIC");
        return;
    }
    now_ns = (int64_t)now.tv_sec * 1000000000LL + now.tv_nsec;

    pthread_mutex_lock(&recovery_mutex_);
    last_normal_frame_monotonic_ns_ = now_ns;
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
            /*
             * 카메라 시작 직후 안정화 전 frame은 Ring에 넣지 않는다.
             * Request 자체는 callback 마지막에서 정상적으로 재등록한다.
             */
        } else if (metadata.status != libcamera::FrameMetadata::FrameSuccess) {
            fprintf(stderr, "camera frame metadata 오류: %d\n",
                    (int)metadata.status);
        } else {
            auto frame_wall_clock =
                request->metadata().get(libcamera::controls::FrameWallClock);

            /*
             * Callback 실행 시각이 아닌 capture 시각의 FrameWallClock을 쓴다.
             * Wall-clock backward discontinuity 처리는 현재 구현 범위 밖이다.
             */
            if (!frame_wall_clock) {
                fprintf(stderr,
                        "camera frame에 FrameWallClock metadata가 없음\n");
            } else if (copy_nv12(frame_buffer, metadata, scratch_) == 0) {
                /* Ring 저장 결과와 분리해 실제 camera frame 수신만 감시한다. */
                record_normal_frame();
                if (ring_push(ring_, scratch_, NV12_FRAME_SIZE,
                              *frame_wall_clock) != 0)
                    fprintf(stderr, "camera frame ring 저장 실패\n");
                else
                    ai_progress(AI_CAPTURE);
            }
        }
    }

    /* 같은 camera buffer를 재사용해 다음 frame capture를 계속한다. */
    request->reuse(libcamera::Request::ReuseBuffers);
    if (running_.load() && camera_->queueRequest(request) != 0) {
        fprintf(stderr, "capture request 재등록 실패\n");
        request_camera_reinitialization();
    }
}

int CameraCapture::start_camera(void)
{
    libcamera::StreamConfiguration *cfg;
    libcamera::CameraConfiguration::Status validation_status;
    struct timespec watchdog_start;

    if (scratch_ == NULL)
        return -1;

    /* CameraManager 시작 후 첫 번째 사용 가능한 카메라를 획득한다. */
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
    /* ISP 출력 형식을 640x480 Rec.709 limited-range NV12로 요청한다. */
    cfg = &configuration_->at(0);
    cfg->size = {CAMERA_WIDTH, CAMERA_HEIGHT};
    cfg->pixelFormat = libcamera::formats::NV12;
    cfg->colorSpace = libcamera::ColorSpace::Rec709;
    cfg->bufferCount = 6;
    configuration_->orientation = libcamera::Orientation::Rotate180;

    /* validate가 조정한 뒤에도 180도 회전과 기존 영상 계약이 유지되는지 확인한다. */
    validation_status = configuration_->validate();
    if (configuration_->orientation != libcamera::Orientation::Rotate180) {
        fprintf(stderr, "카메라가 180도 orientation을 지원하지 않음\n");
        return -1;
    }
    if (validation_status == libcamera::CameraConfiguration::Invalid) {
        fprintf(stderr, "카메라 설정 validation 실패 (Rotate180 포함)\n");
        return -1;
    }
    if (cfg->size != libcamera::Size(CAMERA_WIDTH, CAMERA_HEIGHT) ||
        cfg->pixelFormat != libcamera::formats::NV12) {
        fprintf(stderr, "카메라가 640x480 NV12 설정을 지원하지 않음\n");
        return -1;
    }
    if (cfg->colorSpace != libcamera::ColorSpace::Rec709) {
        fprintf(stderr, "카메라가 Rec.709 color space 설정을 지원하지 않음\n");
        return -1;
    }
    if (camera_->configure(configuration_.get()) != 0)
        return -1;

    stream_ = cfg->stream();
    stride_ = cfg->stride;
    /* Capture request가 순환 사용할 libcamera buffer들을 할당하고 mmap한다. */
    allocator_ = std::make_unique<libcamera::FrameBufferAllocator>(camera_);
    if (allocator_->allocate(stream_) < 0 || map_buffers() != 0)
        return -1;

    for (const auto &buffer : allocator_->buffers(stream_)) {
        std::unique_ptr<libcamera::Request> request = camera_->createRequest();

        if (!request || request->addBuffer(stream_, buffer.get()) != 0)
            return -1;
        requests_.push_back(std::move(request));
    }

    /* 완료 signal을 ring producer callback에 연결한다. */
    camera_->requestCompleted.connect(this, &CameraCapture::request_complete);
    libcamera::ControlList controls(camera_->controls());
    /* 33333us frame duration으로 약 30fps를 요청한다. */
    controls.set(libcamera::controls::FrameDurationLimits,
                 libcamera::Span<const int64_t, 2>({33333, 33333}));
    if (camera_->start(&controls) != 0)
        return -1;

    /* 모든 request를 최초 queueing한 뒤 callback이 계속 재등록한다. */
    running_.store(true);
    for (auto &request : requests_) {
        if (camera_->queueRequest(request.get()) != 0)
            return -1;
    }

    /* 첫 정상 frame이 아직 없어도 camera 시작 시점부터 1초를 계산한다. */
    if (clock_gettime(CLOCK_MONOTONIC, &watchdog_start) != 0) {
        perror("camera watchdog 시작 시각");
        return -1;
    }
    pthread_mutex_lock(&recovery_mutex_);
    last_normal_frame_monotonic_ns_ =
        (int64_t)watchdog_start.tv_sec * 1000000000LL +
        watchdog_start.tv_nsec;
    pthread_mutex_unlock(&recovery_mutex_);

    return 0;
}

void CameraCapture::stop_camera(void)
{
    /* running을 먼저 false로 바꿔 callback의 request 재등록을 중단한다. */
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
    CameraCapture *capture = static_cast<CameraCapture *>(argument);

    capture->recovery_loop();
    return NULL;
}

void CameraCapture::recovery_loop(void)
{
    pthread_mutex_lock(&recovery_mutex_);
    while (!recovery_closed_) {
        ai_progress(AI_RECOVERY);
        struct timespec deadline;
        struct timespec now;
        int64_t now_ns;
        int watchdog_timeout;
        int watchdog_recovery;
        int wait_result = 0;
        int restart_result;

        if (clock_gettime(CLOCK_MONOTONIC, &deadline) != 0) {
            perror("camera watchdog deadline");
            fatal_error_.store(true);
            pthread_mutex_unlock(&recovery_mutex_);
            request_runtime_failure();
            return;
        }
        deadline.tv_sec += CAMERA_WATCHDOG_CHECK_MS / 1000U;
        deadline.tv_nsec +=
            (long)(CAMERA_WATCHDOG_CHECK_MS % 1000U) * 1000000L;
        if (deadline.tv_nsec >= 1000000000L) {
            ++deadline.tv_sec;
            deadline.tv_nsec -= 1000000000L;
        }

        if (!recovery_requested_)
            wait_result = pthread_cond_timedwait(&recovery_condition_,
                                                 &recovery_mutex_,
                                                 &deadline);
        if (recovery_closed_)
            break;
        if (wait_result != 0 && wait_result != ETIMEDOUT) {
            fprintf(stderr, "camera recovery timed wait 실패: %s\n",
                    strerror(wait_result));
            fatal_error_.store(true);
            pthread_mutex_unlock(&recovery_mutex_);
            request_runtime_failure();
            return;
        }

        if (clock_gettime(CLOCK_MONOTONIC, &now) != 0) {
            perror("camera watchdog 현재 시각");
            fatal_error_.store(true);
            pthread_mutex_unlock(&recovery_mutex_);
            request_runtime_failure();
            return;
        }
        now_ns = (int64_t)now.tv_sec * 1000000000LL + now.tv_nsec;
        watchdog_timeout =
            last_normal_frame_monotonic_ns_ > 0 &&
            now_ns - last_normal_frame_monotonic_ns_ >=
                (int64_t)CAMERA_WATCHDOG_TIMEOUT_MS * 1000000LL;
        if (!recovery_requested_ && !watchdog_timeout)
            continue;

        watchdog_recovery = watchdog_timeout;
        if (awaiting_normal_frame_after_recovery_) {
            if (watchdog_timeout) {
                fprintf(stderr,
                        "camera watchdog 연속 timeout: process 종료\n");
            } else {
                fprintf(stderr,
                        "camera application-level 재초기화 연속 요청: "
                        "process 종료\n");
            }
            fatal_error_.store(true);
            pthread_mutex_unlock(&recovery_mutex_);
            request_runtime_failure();
            return;
        }

        if (watchdog_timeout)
            fprintf(stderr,
                    "camera frame watchdog timeout: %ums 동안 정상 frame이 "
                    "없음\n",
                    CAMERA_WATCHDOG_TIMEOUT_MS);

        recovery_requested_ = 0;
        pthread_mutex_unlock(&recovery_mutex_);

        /* Application-level libcamera reinit까지만 담당하며 driver reset은 범위 밖이다. */
        if (watchdog_recovery)
            fprintf(stderr,
                    "camera watchdog에 의한 application-level 재초기화 시도\n");
        else
            fprintf(stderr, "camera application-level 재초기화 시도\n");
        stop_camera();

        /* 정지 전 callback이 recovery 성공으로 오인되지 않도록 stop 뒤에 설정한다. */
        pthread_mutex_lock(&recovery_mutex_);
        /* 정지 중 이전 camera callback이 추가한 재초기화 요청도 여기서 비운다. */
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
}

int CameraCapture::start(void)
{
    pthread_condattr_t condition_attribute;
    int condition_attribute_initialized = 0;
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
    if (pthread_condattr_init(&condition_attribute) != 0)
        goto fail;
    condition_attribute_initialized = 1;
    if (pthread_condattr_setclock(&condition_attribute, CLOCK_MONOTONIC) != 0 ||
        pthread_cond_init(&recovery_condition_, &condition_attribute) != 0)
        goto fail;
    condition_initialized = 1;
    pthread_condattr_destroy(&condition_attribute);
    condition_attribute_initialized = 0;
    recovery_sync_initialized_ = 1;

    if (start_camera() != 0)
        goto fail;
    if (pthread_create(&recovery_thread_, NULL, recovery_thread_entry, this) !=
        0) {
        fprintf(stderr, "camera recovery thread 생성 실패\n");
        goto fail;
    }
    recovery_thread_started_ = 1;
    return 0;

fail:
    stop_camera();
    if (condition_attribute_initialized)
        pthread_condattr_destroy(&condition_attribute);
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

/* FFmpeg 음수 error code를 사람이 읽을 수 있는 문자열로 변환한다. */
static std::string ffmpeg_error(int number)
{
    char text[AV_ERROR_MAX_STRING_SIZE] = {0};

    av_strerror(number, text, sizeof(text));
    return std::string(text);
}

/* Startup 검사와 실제 저장이 동일한 H.264 설정을 사용하게 한다. */
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
    printf("encoder_name=%s\n", encoder->name);

    codec = avcodec_alloc_context3(encoder);
    if (codec == NULL)
        return -1;

    configure_h264_encoder(codec, 1);
    error_number = avcodec_open2(codec, encoder, NULL);
    if (error_number < 0) {
        fprintf(stderr, "startup H.264 encoder validation 실패: %s\n",
                ffmpeg_error(error_number).c_str());
        avcodec_free_context(&codec);
        return -1;
    }

    avcodec_free_context(&codec);
    return 0;
}

/*
 * 최종 MP4 저장 디렉터리를 준비하고 detection_time 기반 파일명을 만든다.
 * Raw NV12 경로는 만들지 않으며 이 경로에는 최종 MP4만 기록한다.
 */
static int make_output_path(const inference_event_t *event,
                            char *path, size_t path_size)
{
    int length;

    if (mkdir(OUTPUT_DIRECTORY, 0755) != 0 && errno != EEXIST) {
        perror("output directory");
        return -1;
    }

    length = snprintf(path, path_size, "%s/station_%" PRIu32 "_%" PRId64
                      ".mp4", OUTPUT_DIRECTORY, event->station_id,
                      event->detection_time);
    return length >= 0 && (size_t)length < path_size ? 0 : -1;
}

/*
 * RAM snapshot의 packed NV12 frame들을 H.264로 인코딩하고 MP4로 mux한다.
 * 첫 frame의 절대시각은 metadata에, frame 간 상대 ns는 PTS에 기록한다.
 * H.264 packet을 MP4에 넣을 때 추가 재인코딩은 수행하지 않는다.
 */
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
    size_t i;

    if (frame_count == 0 || !EvidenceHandoff::can_publish(ready))
        return -1;
    ai_progress(AI_WORKER);

    int temporary_path_length =
        snprintf(temporary_path, sizeof(temporary_path), "%s.part", path);
    if (temporary_path_length < 0 ||
        (size_t)temporary_path_length >= sizeof(temporary_path)) {
        fprintf(stderr, "MP4 temporary path가 너무 김\n");
        return -1;
    }
    if (unlink(temporary_path) != 0 && errno != ENOENT) {
        perror("기존 MP4 temporary file 정리");
        return -1;
    }

    /* 파일 확장자에 의존하지 않고 MP4 muxer를 명시적으로 선택한다. */
    error_number = avformat_alloc_output_context2(&format, NULL, "mp4",
                                                   temporary_path);
    if (error_number < 0 || format == NULL)
        goto cleanup;

    snprintf(capture_start_timestamp,
             sizeof(capture_start_timestamp),
             "%" PRId64,
             frames[0].timestamp_ns);
    if (av_dict_set(&format->metadata,
                    "capture_start_timestamp_ns",
                    capture_start_timestamp,
                    0) < 0 ||
        av_dict_set(&mux_options,
                    "movflags",
                    "use_metadata_tags",
                    0) < 0)
        goto cleanup;

    /* Runtime FFmpeg build에 등록된 H.264 encoder를 선택한다. */
    encoder = avcodec_find_encoder(AV_CODEC_ID_H264);
    if (encoder == NULL) {
        fprintf(stderr, "runtime에 H.264 encoder가 없음\n");
        goto cleanup;
    }

    codec = avcodec_alloc_context3(encoder);
    if (codec == NULL)
        goto cleanup;

    /* Camera/ring과 동일한 Rec.709 limited-range NV12 및 2.5Mbps를 설정한다. */
    configure_h264_encoder(codec,
                           format->oformat->flags & AVFMT_GLOBALHEADER);

    if ((error_number = avcodec_open2(codec, encoder, NULL)) < 0)
        goto ffmpeg_failure;

    /* 이미 H.264로 encode될 video stream 하나를 MP4 container에 만든다. */
    stream = avformat_new_stream(format, NULL);
    if (stream == NULL)
        goto cleanup;

    stream->time_base = codec->time_base;
    error_number = avcodec_parameters_from_context(stream->codecpar, codec);
    if (error_number < 0)
        goto ffmpeg_failure;

    /* 완성 전에는 final path가 아닌 같은 filesystem의 temporary path를 연다. */
    if (!(format->oformat->flags & AVFMT_NOFILE) &&
        (error_number = avio_open(&format->pb, temporary_path,
                                  AVIO_FLAG_WRITE)) < 0)
        goto ffmpeg_failure;

    if ((error_number = avformat_write_header(format, &mux_options)) < 0)
        goto ffmpeg_failure;

    header_written = 1;
    /* Encoder 입력 frame과 출력 packet은 전체 encode 동안 재사용한다. */
    frame = av_frame_alloc();
    packet = av_packet_alloc();
    if (frame == NULL || packet == NULL)
        goto cleanup;

    frame->format = codec->pix_fmt;
    frame->width = codec->width;
    frame->height = codec->height;
    /* Encoder 입력 frame에도 동일한 색 정보를 전달해 H.264/MP4에 보존한다. */
    frame->color_range = codec->color_range;
    frame->color_primaries = codec->color_primaries;
    frame->color_trc = codec->color_trc;
    frame->colorspace = codec->colorspace;
    if ((error_number = av_frame_get_buffer(frame, 32)) < 0)
        goto ffmpeg_failure;

    /*
     * 마지막 i == frame_count 반복은 input=NULL을 보내 encoder 내부에 남은
     * 지연 frame들을 flush하기 위한 반복이다.
     */
    for (i = 0; i <= frame_count; ++i) {
        ai_progress(AI_WORKER);
        AVFrame *input = i < frame_count ? frame : NULL;

        if (input != NULL) {
            unsigned int row;
            const uint8_t *uv;

            if (frames[i].size != NV12_FRAME_SIZE ||
                av_frame_make_writable(frame) < 0)
                goto cleanup;

            /* Snapshot은 packed이고 AVFrame에는 stride가 있으므로 row별 복사한다. */
            for (row = 0; row < CAMERA_HEIGHT; ++row)
                memcpy(frame->data[0] + row * frame->linesize[0],
                       frames[i].data + row * CAMERA_WIDTH, CAMERA_WIDTH);

            uv = frames[i].data + CAMERA_WIDTH * CAMERA_HEIGHT;
            for (row = 0; row < CAMERA_HEIGHT / 2U; ++row)
                memcpy(frame->data[1] + row * frame->linesize[1],
                       uv + row * CAMERA_WIDTH, CAMERA_WIDTH);

            /* 첫 frame을 0으로 두고 실제 촬영 시각 차이를 ns PTS에 반영한다. */
            frame->pts = frames[i].timestamp_ns - frames[0].timestamp_ns;
        }

        if ((error_number = avcodec_send_frame(codec, input)) < 0)
            goto ffmpeg_failure;

        /* 한 입력 frame에서 나오는 모든 H.264 packet을 즉시 MP4 muxer에 전달한다. */
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

    /* MP4 index와 trailer까지 기록되어야 정상적으로 재생 가능한 파일이 된다. */
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
    /* 중간 실패를 포함한 모든 경로가 동일한 FFmpeg 자원 정리를 거친다. */
    if (header_written) {
        int trailer_error = av_write_trailer(format);

        if (trailer_error < 0)
            fprintf(stderr, "MP4 cleanup trailer 실패: %s\n",
                    ffmpeg_error(trailer_error).c_str());
        result = -1;
    }

    av_packet_free(&packet);
    av_frame_free(&frame);
    avcodec_free_context(&codec);
    av_dict_free(&mux_options);

    if (format != NULL) {
        if (format->pb != NULL) {
            int close_error = avio_closep(&format->pb);

            if (close_error < 0)
                fprintf(stderr, "MP4 AVIO close 실패: %s\n",
                        ffmpeg_error(close_error).c_str());
            result = -1;
        }
        avformat_free_context(format);
    }
    if (result != 0 && temporary_path[0] != '\0' &&
        !EvidenceHandoff::has_record(ready) && unlink(temporary_path) != 0 && errno != ENOENT)
        perror("MP4 temporary file cleanup");
    return result;
}

/* IPC header의 경과시간 timestamp를 CLOCK_MONOTONIC ns로 만든다. */
static uint64_t monotonic_timestamp_ns(void)
{
    struct timespec now;

    if (clock_gettime(CLOCK_MONOTONIC, &now) != 0)
        return 0;

    return (uint64_t)now.tv_sec * 1000000000ULL +
           (uint64_t)now.tv_nsec;
}

/* Time Sync의 상태를 Uploader가 허용하는 시간 신뢰도 문자열로 변환한다. */
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

/* 실행 중인 Time Sync 프로세스에서 최신 chrony 측정 snapshot을 조회한다. */
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


static const char *accident_vehicle_id_from_station_id(uint32_t station_id)
{
    if (station_id == 1)
        return "CAR_02";

    return NULL;
}

/* Uploader가 다른 working directory에서도 열 수 있도록 최종 경로를 절대화한다. */
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

/* 최종 MP4 공개 전에 원본 사고/clip metadata와 절대 경로를 준비한다. */
static int make_evidence_payload(uploader_evidence_ready_t *ready_out,
                                       const inference_event_t *event,
                                       const struct video_snapshot *snapshot,
                                       const char *video_path,
                                       const char *vehicle_id)
{
    uploader_evidence_ready_t ready;
    const char *accident_vehicle_id;
    uint32_t action_id;

    if (event == NULL || snapshot == NULL || snapshot->frames == NULL ||
        snapshot->count == 0 || video_path == NULL || vehicle_id == NULL)
        return -1;

    accident_vehicle_id =
        accident_vehicle_id_from_station_id(event->station_id);
    if (accident_vehicle_id == NULL) {
        fprintf(stderr, "Uploader용 사고 차량 ID mapping이 없음\n");
        return -1;
    }

    action_id = ((uint32_t)event->station_id << 16) |
                (uint32_t)event->sequence_number;

    memset(&ready, 0, sizeof(ready));

    ready.version = UPLOADER_PROTOCOL_VERSION;
    ready.reserved = 0;
    ready.event_time_ns = event->detection_time;
    ready.clip_start_time_ns = snapshot->frames[0].timestamp_ns;
    ready.clip_end_time_ns =
        snapshot->frames[snapshot->count - 1].timestamp_ns;

    if (snprintf(ready.action_id, sizeof(ready.action_id), "%" PRIu32,
                 action_id) >= (int)sizeof(ready.action_id) ||
        snprintf(ready.accident_vehicle_id,
                 sizeof(ready.accident_vehicle_id), "%s",
                 accident_vehicle_id) >=
            (int)sizeof(ready.accident_vehicle_id) ||
        snprintf(ready.vehicle_id, sizeof(ready.vehicle_id), "%s",
                 vehicle_id) >= (int)sizeof(ready.vehicle_id) ||
        resolve_final_video_path(video_path, ready.video_path,
                                 sizeof(ready.video_path)) != 0) {
        fprintf(stderr, "Uploader payload 문자열 생성 실패\n");
        return -1;
    }

    *ready_out = ready;
    return 0;
}

/*
 * FIFO event consumer다. 단일 ONNX session으로 한 번에 event 하나만 처리한다.
 * 오래된 queued event가 ring의 20초 범위를 벗어났으면 즉시 drop한다.
 */
static void *inference_worker_thread(void *argument)
{
    struct worker_context *context = (struct worker_context *)argument;
    inference_event_t event;

    EvidenceHandoff handoff(prepare_evidence_time, deliver_evidence_to_uploader,
                            ai_handoff_progress);
    if (handoff.start(OUTPUT_DIRECTORY, ai_restore_progress) != 0) {
        request_runtime_failure();
        return NULL;
    }

    while (event_queue_pop(context->queue, &event)) {
        struct video_snapshot snapshot;
        enum inference_result inference;
        int target_class = target_class_from_station_id(event.station_id);
        int64_t begin_ns;
        int64_t end_ns;
        char output_path[256];
        uploader_evidence_ready_t ready;

        /* 유효하지 않은 station은 대용량 10초 snapshot을 만들기 전에 버린다. */
        if (target_class < 0) {
            fprintf(stderr,
                    "event drop: 유효하지 않은 station_id=%" PRIu32 "\n",
                    event.station_id);
            continue;
        }

        /* 사고 전 8초~후 2초를 한 번 고정하고 AI 판정과 인코딩에 함께 사용한다. */
        int64_t available_end_ns;
        begin_ns = event.detection_time - ACCIDENT_VIDEO_BEFORE_NS;
        end_ns = event.detection_time + ACCIDENT_VIDEO_AFTER_NS;
        if (ring_wait_for_snapshot_end(context->ring, end_ns,
                                       &available_end_ns,
                                       ACCIDENT_VIDEO_AFTER_NS +
                                       FRAME_COVERAGE_TOLERANCE_NS) != 0) {
            fprintf(stderr,
                    "event drop: 사고 후 2초 저장 끝점의 camera frame이 없음 "
                    "(station=%" PRIu32 ", detection_ns=%" PRId64 ")\n",
                    event.station_id,
                    event.detection_time);
            continue;
        }

        /* Mutex 안에서 범위를 검사하고 Frame Pool refcount를 획득한다. */
        if (ring_copy_snapshot(context->ring, begin_ns, end_ns, &snapshot) != 0) {
            fprintf(stderr,
                    "event drop: snapshot 생성 전 영상 구간이 사라졌거나 "
                    "메모리 할당에 실패함\n");
            continue;
        }

        /* Sampling 결과가 최근 3회 중 2회 검출이면 추론을 확정하고 종료한다. */
        printf("RAM snapshot frame: %zu\n", snapshot.count);
        inference = run_vehicle_inference(context->detector,
                                          &snapshot,
                                          target_class);

        /* 영상 저장 성공 여부와 무관하게 FOUND 직후 LED 시간을 시작한다. */
        if (inference == INFERENCE_FOUND &&
            led_controller_trigger(context->led) != 0)
            fprintf(stderr, "FOUND LED trigger 실패\n");

        /* FOUND인 경우에만 encoder와 MP4 muxer를 실행한다. */
        if (inference == INFERENCE_ERROR) {
            fprintf(stderr, "사고 영상 inference 실패: 저장하지 않음\n");
        } else if (inference == INFERENCE_NOT_FOUND) {
            printf("target vehicle 미검출: 영상 저장 안 함\n");
        } else {
            if (make_output_path(&event,
                                 output_path,
                                 sizeof(output_path)) != 0 ||
                make_evidence_payload(&ready, &event, &snapshot, output_path,
                                      context->vehicle_id) != 0 ||
                write_mp4(snapshot.frames, snapshot.count, output_path, &handoff,
                          (event.station_id << 16) | event.sequence_number, ready) != 0) {
                fprintf(stderr, "영상 저장 실패\n");
            } else {
                printf("영상 저장 완료: %s\n", output_path);
            }
        }

        /* 검출/저장 결과와 무관하게 Snapshot의 Frame Pool 참조를 반환한다. */
        snapshot_destroy(context->ring, &snapshot);
    }

    return NULL;
}

int main(void)
{
    std::unique_ptr<YoloXDetector> detector;
    const char *vehicle_id = getenv("UPLOADER_VEHICLE_ID");
    struct led_controller led;
    struct ring_buffer ring;
    struct event_queue queue;
    struct worker_context worker_context;
    pthread_t receiver_thread;
    pthread_t worker_thread;
    int led_initialized = 0;
    int ring_initialized = 0;
    int queue_initialized = 0;
    int signal_initialized = 0;
    int result = EXIT_FAILURE;

    /* 서비스가 주입한 로컬 차량 ID를 startup에 한 번 확정한다. */
    if (vehicle_id == NULL || vehicle_id[0] == '\0') {
        fprintf(stderr, "UPLOADER_VEHICLE_ID 환경변수가 설정되지 않음\n");
        return EXIT_FAILURE;
    }

    if (ace_watchdog_start(&ai_watchdog, ai_watchdog_names, 6) != 0) {
        perror("AI watchdog 시작");
        return EXIT_FAILURE;
    }

    /* Camera와 worker를 시작하기 전에 모델과 ONNX session 계약을 확정한다. */
    try {
        detector = std::make_unique<YoloXDetector>();
    } catch (const std::exception &error) {
        fprintf(stderr, "YOLOX detector 생성 실패: %s\n", error.what());
        return EXIT_FAILURE;
    }
    if (detector->initialize() != 0)
        return EXIT_FAILURE;
    if (validate_h264_encoder() != 0)
        return EXIT_FAILURE;

    if (shutdown_signal_init() != 0)
        return EXIT_FAILURE;
    signal_initialized = 1;
    runtime_failure_requested.store(false);

    /* GPIO17을 LOW로 요청하고 LED 전용 thread를 먼저 준비한다. */
    if (led_controller_init(&led) != 0)
        goto cleanup;
    led_initialized = 1;

    /* Camera 시작 전에 20초 ring과 FIFO queue를 준비한다. */
    if (ring_init(&ring, RING_CAPACITY) != 0) {
        fprintf(stderr, "ring buffer 메모리 할당 실패\n");
        goto cleanup;
    }
    ring_initialized = 1;

    if (event_queue_init(&queue) != 0) {
        fprintf(stderr, "event queue 초기화 실패\n");
        goto cleanup;
    }
    queue_initialized = 1;

    {
        CameraCapture camera(&ring);

        /* 카메라는 프로세스 수명 동안 한 번만 시작하고 계속 capture한다. */
        if (camera.start() != 0) {
            fprintf(stderr, "카메라 시작 실패\n");
            goto cleanup;
        }

        worker_context.queue = &queue;
        worker_context.ring = &ring;
        worker_context.led = &led;
        worker_context.detector = detector.get();
        worker_context.vehicle_id = vehicle_id;

        /* Worker를 먼저 시작한다. Queue가 비어 있으므로 condition variable에서 대기한다. */
        if (pthread_create(&worker_thread,
                           NULL,
                           inference_worker_thread,
                           &worker_context) != 0) {
            fprintf(stderr, "inference worker thread 생성 실패\n");
            camera.stop();
            goto cleanup;
        }

        /* Receiver는 공용 IPC를 통해 Filter의 inference Event를 계속 받는다. */
        if (pthread_create(&receiver_thread,
                           NULL,
                           event_receiver_thread,
                           &queue) != 0) {
            fprintf(stderr, "event receiver thread 생성 실패\n");
            event_queue_close(&queue);
            pthread_join(worker_thread, NULL);
            camera.stop();
            goto cleanup;
        }

        /*
         * Socket 오류로 receiver가 종료되면 worker는 queue에 남은 event까지 처리한다.
         * 두 thread가 끝나야 공유 queue/ring과 camera를 안전하게 정리할 수 있다.
         */
        pthread_join(receiver_thread, NULL);
        ring_wake_waiters(&ring);
        pthread_join(worker_thread, NULL);
        camera.stop();
        result = camera.failed() || runtime_failure_requested.load()
                     ? EXIT_FAILURE
                     : EXIT_SUCCESS;
    }

cleanup:
    /* 초기화에 성공한 역순으로 프로세스 공용 자원을 해제한다. */
    if (queue_initialized)
        event_queue_destroy(&queue);
    if (ring_initialized)
        ring_destroy(&ring);
    if (led_initialized)
        led_controller_destroy(&led);
    if (signal_initialized)
        shutdown_signal_destroy();
    ace_watchdog_stop(&ai_watchdog);
    return result;
}
