#define _POSIX_C_SOURCE 200809L

#include "uploader_sha256.h"
#include "uploader_watchdog.h"

#include <errno.h>
#include <stdio.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

typedef struct {
    uint32_t state[8];
    uint64_t total_bytes;
    unsigned char block[64];
    size_t block_used;
} sha256_context_t;

static const uint32_t round_constant[64] = {
    0x428a2f98U, 0x71374491U, 0xb5c0fbcfU, 0xe9b5dba5U,
    0x3956c25bU, 0x59f111f1U, 0x923f82a4U, 0xab1c5ed5U,
    0xd807aa98U, 0x12835b01U, 0x243185beU, 0x550c7dc3U,
    0x72be5d74U, 0x80deb1feU, 0x9bdc06a7U, 0xc19bf174U,
    0xe49b69c1U, 0xefbe4786U, 0x0fc19dc6U, 0x240ca1ccU,
    0x2de92c6fU, 0x4a7484aaU, 0x5cb0a9dcU, 0x76f988daU,
    0x983e5152U, 0xa831c66dU, 0xb00327c8U, 0xbf597fc7U,
    0xc6e00bf3U, 0xd5a79147U, 0x06ca6351U, 0x14292967U,
    0x27b70a85U, 0x2e1b2138U, 0x4d2c6dfcU, 0x53380d13U,
    0x650a7354U, 0x766a0abbU, 0x81c2c92eU, 0x92722c85U,
    0xa2bfe8a1U, 0xa81a664bU, 0xc24b8b70U, 0xc76c51a3U,
    0xd192e819U, 0xd6990624U, 0xf40e3585U, 0x106aa070U,
    0x19a4c116U, 0x1e376c08U, 0x2748774cU, 0x34b0bcb5U,
    0x391c0cb3U, 0x4ed8aa4aU, 0x5b9cca4fU, 0x682e6ff3U,
    0x748f82eeU, 0x78a5636fU, 0x84c87814U, 0x8cc70208U,
    0x90befffaU, 0xa4506cebU, 0xbef9a3f7U, 0xc67178f2U
};

/* =========================================================================
 * #### SHA-256 압축 함수 primitive
 * ========================================================================= */

/**
 * @brief 32-bit 값을 오른쪽으로 순환 회전함.
 * @return 회전된 32-bit 값.
 */
static uint32_t rotate_right(uint32_t value, unsigned int bits)
{
    return (value >> bits) | (value << (32U - bits));
}

/**
 * 64-byte 입력 block 하나를 SHA-256 압축 함수에 반영함.
 * 모든 정수 조합은 명시적으로 big-endian으로 처리하여 ARM과 Host에서 같은
 * 결과를 만들며, heap이나 전역 변경 가능 상태를 사용하지 않음.
 */
static void transform_block(
    sha256_context_t *context,
    const unsigned char block[64])
{
    uint32_t schedule[64];
    uint32_t a;
    uint32_t b;
    uint32_t c;
    uint32_t d;
    uint32_t e;
    uint32_t f;
    uint32_t g;
    uint32_t h;
    unsigned int index;

    for (index = 0U; index < 16U; index++) {
        size_t offset = (size_t)index * 4U;

        schedule[index] =
            ((uint32_t)block[offset] << 24U) |
            ((uint32_t)block[offset + 1U] << 16U) |
            ((uint32_t)block[offset + 2U] << 8U) |
            (uint32_t)block[offset + 3U];
    }

    for (index = 16U; index < 64U; index++) {
        uint32_t s0 =
            rotate_right(schedule[index - 15U], 7U) ^
            rotate_right(schedule[index - 15U], 18U) ^
            (schedule[index - 15U] >> 3U);
        uint32_t s1 =
            rotate_right(schedule[index - 2U], 17U) ^
            rotate_right(schedule[index - 2U], 19U) ^
            (schedule[index - 2U] >> 10U);

        schedule[index] =
            schedule[index - 16U] + s0 +
            schedule[index - 7U] + s1;
    }

    a = context->state[0];
    b = context->state[1];
    c = context->state[2];
    d = context->state[3];
    e = context->state[4];
    f = context->state[5];
    g = context->state[6];
    h = context->state[7];

    for (index = 0U; index < 64U; index++) {
        uint32_t sum1 =
            rotate_right(e, 6U) ^
            rotate_right(e, 11U) ^
            rotate_right(e, 25U);
        uint32_t choose = (e & f) ^ ((~e) & g);
        uint32_t temporary1 =
            h + sum1 + choose + round_constant[index] + schedule[index];
        uint32_t sum0 =
            rotate_right(a, 2U) ^
            rotate_right(a, 13U) ^
            rotate_right(a, 22U);
        uint32_t majority = (a & b) ^ (a & c) ^ (b & c);
        uint32_t temporary2 = sum0 + majority;

        h = g;
        g = f;
        f = e;
        e = d + temporary1;
        d = c;
        c = b;
        b = a;
        a = temporary1 + temporary2;
    }

    context->state[0] += a;
    context->state[1] += b;
    context->state[2] += c;
    context->state[3] += d;
    context->state[4] += e;
    context->state[5] += f;
    context->state[6] += g;
    context->state[7] += h;
}

/* =========================================================================
 * #### SHA-256 context 입력과 마무리
 * ========================================================================= */

/**
 * @brief FIPS 180-4 초기 상수와 길이 상태로 context를 초기화함.
 * @return 반환값 없음.
 */
static void initialize_context(sha256_context_t *context)
{
    memset(context, 0, sizeof(*context));
    context->state[0] = 0x6a09e667U;
    context->state[1] = 0xbb67ae85U;
    context->state[2] = 0x3c6ef372U;
    context->state[3] = 0xa54ff53aU;
    context->state[4] = 0x510e527fU;
    context->state[5] = 0x9b05688cU;
    context->state[6] = 0x1f83d9abU;
    context->state[7] = 0x5be0cd19U;
}

/** 입력을 내부 64-byte block으로 나누어 누적하고 길이 overflow를 검사함. */
/**
 * @brief 임의 길이 입력을 context buffer에 누적하고 완성 block을 변환함.
 * @return 입력 반영 성공 시 0, 전체 길이 overflow 시 -1.
 */
static int update_context(
    sha256_context_t *context,
    const unsigned char *data,
    size_t data_size)
{
    size_t offset = 0U;

    if ((uint64_t)data_size > UINT64_MAX - context->total_bytes) {
        return -1;
    }

    context->total_bytes += (uint64_t)data_size;

    while (offset < data_size) {
        size_t available = sizeof(context->block) - context->block_used;
        size_t remaining = data_size - offset;
        size_t copy_size = remaining < available ? remaining : available;

        memcpy(
            context->block + context->block_used,
            data + offset,
            copy_size);

        context->block_used += copy_size;
        offset += copy_size;

        if (context->block_used == sizeof(context->block)) {
            transform_block(context, context->block);
            context->block_used = 0U;
        }
    }

    return 0;
}

/**
 * SHA-256 padding과 원문 bit 길이를 반영하고 32-byte digest를 생성함.
 * padding 자체는 total_bytes에 포함하지 않아 원문 길이가 정확히 기록됨.
 */
/**
 * @brief padding과 bit length를 추가해 최종 32-byte digest를 생성함.
 * @return digest 생성 성공 시 0, 내부 길이 처리 실패 시 -1.
 */
static int finalize_context(
    sha256_context_t *context,
    unsigned char digest[32])
{
    uint64_t bit_length;
    unsigned int index;

    if (context->total_bytes > UINT64_MAX / 8U) {
        return -1;
    }

    bit_length = context->total_bytes * 8U;
    context->block[context->block_used++] = 0x80U;

    if (context->block_used > 56U) {
        memset(
            context->block + context->block_used,
            0,
            sizeof(context->block) - context->block_used);
        transform_block(context, context->block);
        context->block_used = 0U;
    }

    memset(
        context->block + context->block_used,
        0,
        56U - context->block_used);

    for (index = 0U; index < 8U; index++) {
        context->block[63U - index] =
            (unsigned char)(bit_length >> (index * 8U));
    }

    transform_block(context, context->block);

    for (index = 0U; index < 8U; index++) {
        digest[index * 4U] =
            (unsigned char)(context->state[index] >> 24U);
        digest[index * 4U + 1U] =
            (unsigned char)(context->state[index] >> 16U);
        digest[index * 4U + 2U] =
            (unsigned char)(context->state[index] >> 8U);
        digest[index * 4U + 3U] =
            (unsigned char)context->state[index];
    }

    return 0;
}

/* =========================================================================
 * #### 영상 파일 안정성 검증과 streaming hash
 * ========================================================================= */

/**
 * @brief 해시 전후 파일이 같은 inode, 크기, mtime을 유지했는지 검사함.
 * @return 동일한 snapshot이면 1, 읽는 중 교체·변경됐으면 0.
 */
static int same_file_snapshot(
    const struct stat *before,
    const struct stat *after)
{
    return before->st_dev == after->st_dev &&
        before->st_ino == after->st_ino &&
        before->st_size == after->st_size &&
        before->st_mtim.tv_sec == after->st_mtim.tv_sec &&
        before->st_mtim.tv_nsec == after->st_mtim.tv_nsec;
}

/**
 * @brief MP4를 64KiB씩 읽어 SHA-256과 실제 byte 수를 계산함.
 * @param digest_hex 64자리 소문자 hex와 마지막 NUL을 돌려받음.
 * @param file_size 해시에 반영된 전체 파일 크기를 돌려받음.
 * @return 정상·비어 있지 않은 안정된 파일이면 0, 파일/읽기/변경 오류 시 -1.
 * @note 영상 전체를 RAM에 적재하지 않음.
 */
int sha256_file(
    const char *path,
    char digest_hex[SHA256_HEX_SIZE],
    uint64_t *file_size,
    char *error,
    size_t error_size)
{
    static const char hexadecimal[] = "0123456789abcdef";

    sha256_context_t context;
    struct stat before;
    struct stat after;
    unsigned char buffer[SHA256_FILE_BUFFER_SIZE];
    unsigned char digest[32];
    FILE *file;
    size_t read_size;
    unsigned int index;
    int file_descriptor;

    if (path == NULL || digest_hex == NULL || file_size == NULL ||
        error == NULL || error_size == 0U) {
        return -1;
    }

    digest_hex[0] = '\0';
    *file_size = 0U;
    error[0] = '\0';

    uploader_watchdog_progress(UPLOADER_LOCAL_PROGRESS_MS);
    file = fopen(path, "rb");

    if (file == NULL) {
        snprintf(error, error_size, "open video for SHA-256: %s", strerror(errno));
        return -1;
    }

    file_descriptor = fileno(file);

    if (file_descriptor < 0 || fstat(file_descriptor, &before) != 0) {
        snprintf(error, error_size, "stat video for SHA-256: %s", strerror(errno));
        fclose(file);
        return -1;
    }

    if (!S_ISREG(before.st_mode) || before.st_size <= 0) {
        snprintf(error, error_size, "video for SHA-256 is empty or invalid");
        fclose(file);
        return -1;
    }

    initialize_context(&context);

    while ((read_size = fread(buffer, 1U, sizeof(buffer), file)) > 0U) {
        uploader_watchdog_progress(UPLOADER_LOCAL_PROGRESS_MS);
        if (update_context(&context, buffer, read_size) != 0) {
            snprintf(error, error_size, "video is too large for SHA-256 length field");
            fclose(file);
            return -1;
        }
    }

    if (ferror(file) != 0) {
        snprintf(error, error_size, "read video for SHA-256: %s", strerror(errno));
        fclose(file);
        return -1;
    }

    if (fstat(file_descriptor, &after) != 0) {
        snprintf(error, error_size, "restat video after SHA-256: %s", strerror(errno));
        fclose(file);
        return -1;
    }

    if (!same_file_snapshot(&before, &after) ||
        context.total_bytes != (uint64_t)before.st_size) {
        snprintf(error, error_size, "video changed while calculating SHA-256");
        fclose(file);
        return -1;
    }

    if (fclose(file) != 0) {
        snprintf(error, error_size, "close video after SHA-256: %s", strerror(errno));
        return -1;
    }

    if (finalize_context(&context, digest) != 0) {
        snprintf(error, error_size, "finalize video SHA-256 failed");
        return -1;
    }

    for (index = 0U; index < sizeof(digest); index++) {
        digest_hex[index * 2U] = hexadecimal[digest[index] >> 4U];
        digest_hex[index * 2U + 1U] = hexadecimal[digest[index] & 0x0FU];
    }

    digest_hex[SHA256_HEX_LENGTH] = '\0';
    *file_size = context.total_bytes;
    return 0;
}
