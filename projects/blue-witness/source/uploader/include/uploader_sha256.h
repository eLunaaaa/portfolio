#ifndef UPLOADER_SHA256_H
#define UPLOADER_SHA256_H

/**
 * @file uploader_sha256.h
 * @brief Worker가 영상 파일의 SHA-256을 스트리밍 계산하는 기능.
 * @scope 단일 uploader의 Ingest/Worker 스레드 공용.
 *
 * 구현은 FIPS 180-4의 SHA-256 규격을 기반으로 프로젝트 내부에서 독립 작성했음.
 * OpenSSL이나 외부 명령에 의존하지 않으며 영상 전체를 RAM에 올리지 않음.
 */

#include <stddef.h>
#include <stdint.h>

#define SHA256_HEX_LENGTH 64U
#define SHA256_HEX_SIZE (SHA256_HEX_LENGTH + 1U)
#define SHA256_FILE_BUFFER_SIZE (64U * 1024U)

/**
 * 지정한 일반 파일을 64 KiB씩 읽어 SHA-256을 계산함.
 *
 * digest_hex에는 64자리 소문자 16진수와 마지막 NUL을 기록함.
 * file_size에는 실제로 해시에 반영한 바이트 수를 기록함.
 * 파일 누락, 빈 파일, 읽기 실패 또는 읽는 중 파일 변경은 영구 오류로 반환함.
 * 이 함수는 Queue 상태를 직접 변경하지 않으며 호출자인 Worker가 FAIL을 보고함.
 */
int sha256_file(
    const char *path,
    char digest_hex[SHA256_HEX_SIZE],
    uint64_t *file_size,
    char *error,
    size_t error_size);

#endif
