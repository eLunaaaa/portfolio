#define _POSIX_C_SOURCE 200809L

#include "uploader_spool.h"
#include "uploader_watchdog.h"

#include <ctype.h>
#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/statvfs.h>
#include <sys/types.h>
#include <time.h>
#include <unistd.h>

#define INCOMING_PREFIX ".incoming-"

/**
 * @brief 현재 spool commit 호출이 만든 것으로 확인된 임시 파일만 정리함.
 * @return 알려진 파일과 디렉터리를 모두 정리하면 0, 하나라도 실패하면 -1.
 * @note 재귀 삭제를 사용하지 않아 예상하지 못한 파일을 함께 지우지 않음.
 */
static int cleanup_temporary_directory(
    const char *directory,
    const char *video_part,
    const char *metadata_part,
    const char *video,
    const char *metadata)
{
    const char *paths[] = {video_part, metadata_part, video, metadata};
    size_t index;
    int failed = 0;

    for (index = 0U; index < sizeof(paths) / sizeof(paths[0]); index++) {
        if (paths[index] != NULL && paths[index][0] != '\0' &&
            unlink(paths[index]) != 0 && errno != ENOENT) {
            failed = 1;
        }
    }
    if (directory != NULL && directory[0] != '\0' &&
        rmdir(directory) != 0 && errno != ENOENT) {
        failed = 1;
    }
    return failed == 0 ? 0 : -1;
}

/* =========================================================================
 * #### 경로 생성과 spool 디렉터리 준비
 * ========================================================================= */

/**
 * @brief snprintf로 만든 경로가 고정 buffer에서 잘리지 않았는지 확인함.
 * @return 경로 생성 성공 시 0, 길이 초과 또는 formatting 실패 시 -1.
 */
static int path_format(
    char *output,
    size_t output_size,
    char *error,
    size_t error_size,
    const char *format,
    const char *first,
    const char *second)
{
    int length = snprintf(
        output,
        output_size,
        format,
        first,
        second);

    if (length < 0 ||
        (size_t)length >= output_size) {
        snprintf(error, error_size, "path exceeds limit");
        return -1;
    }

    return 0;
}

/**
 * @brief spool root의 중간 디렉터리를 포함해 mkdir(0750)로 생성함.
 * @return 생성되거나 이미 존재하면 0, 잘못된 경로 또는 mkdir 실패 시 -1.
 */
static int create_directories(
    const char *path,
    char *error,
    size_t error_size)
{
    char copy[UPLOADER_PATH_SIZE];
    char *cursor;
    size_t length;

    length = strlen(path);

    if (length == 0U || length >= sizeof(copy)) {
        snprintf(error, error_size, "spool root path is invalid");
        return -1;
    }

    memcpy(copy, path, length + 1U);

    for (cursor = copy + 1; *cursor != '\0'; cursor++) {
        if (*cursor != '/') {
            continue;
        }

        *cursor = '\0';

        if (mkdir(copy, 0750) != 0 &&
            errno != EEXIST) {
            snprintf(
                error,
                error_size,
                "mkdir %s: %s",
                copy,
                strerror(errno));

            return -1;
        }

        *cursor = '/';
    }

    if (mkdir(copy, 0750) != 0 &&
        errno != EEXIST) {
        snprintf(
            error,
            error_size,
            "mkdir %s: %s",
            copy,
            strerror(errno));

        return -1;
    }

    return 0;
}

/* =========================================================================
 * #### 입력 파일 검증과 영속성 보장 helper
 * ========================================================================= */

/**
 * @brief 경로가 존재하는 비어 있지 않은 일반 파일인지 검사함.
 * @param information 성공 시 파일 snapshot을 돌려받음.
 * @return 유효한 파일이면 0, stat/형식/크기 오류면 -1.
 */
static int validate_regular_file(
    const char *path,
    struct stat *information,
    char *error,
    size_t error_size)
{
    if (stat(path, information) != 0) {
        snprintf(
            error,
            error_size,
            "stat %s: %s",
            path,
            strerror(errno));

        return -1;
    }

    if (!S_ISREG(information->st_mode)) {
        snprintf(error, error_size, "%s is not a regular file", path);
        return -1;
    }

    if (information->st_size <= 0) {
        snprintf(error, error_size, "%s is empty", path);
        return -1;
    }

    return 0;
}

/**
 * @brief rename으로 바뀐 디렉터리 항목을 저장장치에 반영하도록 fsync함.
 * @return open/fsync/close가 모두 성공하면 0, 실패하면 -1.
 */
static int fsync_directory(
    const char *directory,
    char *error,
    size_t error_size)
{
    int directory_fd;
    int result;

    directory_fd = open(
        directory,
        O_RDONLY | O_DIRECTORY);

    if (directory_fd < 0) {
        snprintf(
            error,
            error_size,
            "open directory %s: %s",
            directory,
            strerror(errno));

        return -1;
    }

    result = fsync(directory_fd);

    if (result != 0) {
        snprintf(
            error,
            error_size,
            "fsync directory %s: %s",
            directory,
            strerror(errno));

        close(directory_fd);
        return -1;
    }

    if (close(directory_fd) != 0) {
        snprintf(
            error,
            error_size,
            "close directory %s: %s",
            directory,
            strerror(errno));

        return -1;
    }

    return 0;
}

/**
 * @brief partial write와 EINTR를 처리하며 요청한 byte를 모두 기록함.
 * @return 전체 기록 성공 시 0, write 실패 또는 0-byte write 시 -1.
 */
static int write_all(
    int file_descriptor,
    const unsigned char *buffer,
    size_t size,
    char *error,
    size_t error_size)
{
    size_t remaining = size;
    const unsigned char *cursor = buffer;

    while (remaining > 0U) {
        ssize_t written = write(
            file_descriptor,
            cursor,
            remaining);

        if (written < 0 && errno == EINTR) {
            continue;
        }

        if (written <= 0) {
            snprintf(
                error,
                error_size,
                "write spool file: %s",
                written < 0
                    ? strerror(errno)
                    : "zero-byte write");

            return -1;
        }

        cursor += (size_t)written;
        remaining -= (size_t)written;
        uploader_watchdog_progress(UPLOADER_LOCAL_PROGRESS_MS);
    }

    return 0;
}

/**
 * @brief 복사 전후 원본의 크기와 mtime이 달라졌는지 확인함.
 * @return 복사 도중 원본이 변경됐으면 1, 동일하면 0.
 */
static int source_changed(
    const struct stat *before,
    const struct stat *after)
{
    return before->st_size != after->st_size ||
        before->st_mtim.tv_sec != after->st_mtim.tv_sec ||
        before->st_mtim.tv_nsec != after->st_mtim.tv_nsec;
}

/**
 * @brief 원본을 64KiB씩 새 파일로 복사하고 fsync한 뒤 원본 안정성을 재확인함.
 * @return 안전한 복사 완료 시 0, open/read/write/fsync/변경 탐지 실패 시 -1.
 * @note O_EXCL로 기존 임시 파일 덮어쓰기를 차단함.
 */
static int copy_file_and_sync(
    const char *source_path,
    const char *destination_path,
    char *error,
    size_t error_size)
{
    unsigned char buffer[UPLOADER_SPOOL_COPY_CHUNK_SIZE];

    struct stat before;
    struct stat after;

    int source_fd = -1;
    int destination_fd = -1;
    int result = -1;

    uploader_watchdog_progress(UPLOADER_LOCAL_PROGRESS_MS);
    source_fd = open(source_path, O_RDONLY);

    if (source_fd < 0) {
        snprintf(
            error,
            error_size,
            "open source %s: %s",
            source_path,
            strerror(errno));

        goto cleanup;
    }

    if (fstat(source_fd, &before) != 0) {
        snprintf(
            error,
            error_size,
            "fstat source: %s",
            strerror(errno));

        goto cleanup;
    }

    destination_fd = open(
        destination_path,
        O_WRONLY | O_CREAT | O_EXCL,
        0640);

    if (destination_fd < 0) {
        snprintf(
            error,
            error_size,
            "create destination %s: %s",
            destination_path,
            strerror(errno));

        goto cleanup;
    }

    for (;;) {
        ssize_t read_size = read(
            source_fd,
            buffer,
            sizeof(buffer));

        if (read_size < 0 && errno == EINTR) {
            continue;
        }

        if (read_size < 0) {
            snprintf(
                error,
                error_size,
                "read source: %s",
                strerror(errno));

            goto cleanup;
        }

        if (read_size == 0) {
            break;
        }

        if (write_all(
                destination_fd,
                buffer,
                (size_t)read_size,
                error,
                error_size) != 0) {
            goto cleanup;
        }
    }

    if (fsync(destination_fd) != 0) {
        snprintf(
            error,
            error_size,
            "fsync destination: %s",
            strerror(errno));

        goto cleanup;
    }

    if (fstat(source_fd, &after) != 0) {
        snprintf(
            error,
            error_size,
            "second fstat source: %s",
            strerror(errno));

        goto cleanup;
    }

    if (source_changed(&before, &after)) {
        snprintf(
            error,
            error_size,
            "source changed while being copied");

        goto cleanup;
    }

    result = 0;

cleanup:
    if (destination_fd >= 0) {
        if (close(destination_fd) != 0 &&
            result == 0) {
            snprintf(
                error,
                error_size,
                "close destination: %s",
                strerror(errno));

            result = -1;
        }
    }

    if (source_fd >= 0) {
        close(source_fd);
    }

    return result;
}

/* =========================================================================
 * #### 디스크 여유량과 공개 spool 작업 ID 검증
 * ========================================================================= */

/**
 * @brief 일반 사용자가 실제 사용할 수 있는 filesystem 여유 byte를 계산함.
 * @param available 성공 시 f_bavail × f_frsize 결과를 돌려받음.
 * @return statvfs 성공 시 0, 실패 시 -1.
 */
static int available_space(
    const char *path,
    uint64_t *available,
    char *error,
    size_t error_size)
{
    struct statvfs file_system;

    if (statvfs(path, &file_system) != 0) {
        snprintf(
            error,
            error_size,
            "statvfs %s: %s",
            path,
            strerror(errno));

        return -1;
    }

    *available =
        (uint64_t)file_system.f_bavail *
        (uint64_t)file_system.f_frsize;

    return 0;
}

/**
 * @brief job ID가 안전한 디렉터리 이름으로 사용할 수 있는지 검사함.
 * @return 영문/숫자/밑줄/하이픈과 길이 조건을 만족하면 0, 아니면 -1.
 * @note 슬래시와 점을 거부하여 spool root 밖으로 나가는 경로 조작을 방지함.
 */
int spool_validate_job_id(
    const char *job_id,
    char *error,
    size_t error_size)
{
    size_t index;
    size_t length;

    if (job_id == NULL) {
        snprintf(error, error_size, "job_id is missing");
        return -1;
    }

    length = strlen(job_id);

    if (length == 0U ||
        length >= UPLOADER_JOB_ID_SIZE) {
        snprintf(error, error_size, "job_id length is invalid");
        return -1;
    }

    for (index = 0U; index < length; index++) {
        unsigned char character =
            (unsigned char)job_id[index];

        if (!isalnum(character) &&
            character != '_' &&
            character != '-') {
            snprintf(
                error,
                error_size,
                "job_id contains invalid character");

            return -1;
        }
    }

    return 0;
}

/* =========================================================================
 * #### 원자적 spool commit
 * ========================================================================= */

/**
 * @brief MP4와 metadata를 함께 장애 안전한 최종 job 디렉터리로 공개함.
 * @param minimum_free_bytes 복사 완료 뒤에도 남겨야 하는 예약 여유량.
 * @param result 최종 경로, 크기, 여유량, 멱등 재사용 여부를 돌려받음.
 * @return commit 또는 동일한 기존 작업 확인 성공 시 0, 그 외 실패 시 -1.
 * @note .part 복사와 fsync를 끝낸 뒤 디렉터리 rename으로 두 파일을 한 번에 공개함.
 */
int spool_commit(
    const char *spool_root,
    const char *job_id,
    const char *source_video,
    const char *source_metadata,
    uint64_t minimum_free_bytes,
    spool_result_t *result,
    char *error,
    size_t error_size)
{
    struct stat video_information;
    struct stat metadata_information;
    struct stat existing_information;

    char temporary_directory[UPLOADER_PATH_SIZE];
    char video_part[UPLOADER_PATH_SIZE];
    char metadata_part[UPLOADER_PATH_SIZE];
    char temporary_video[UPLOADER_PATH_SIZE];
    char temporary_metadata[UPLOADER_PATH_SIZE];

    uint64_t required_bytes;
    uint64_t required_with_reserve;
    int temporary_created = 0;
    int operation_result = -1;

    memset(result, 0, sizeof(*result));
    result->failure = SPOOL_FAILURE_TEMPORARY_STORAGE;
    memset(temporary_directory, 0, sizeof(temporary_directory));
    memset(video_part, 0, sizeof(video_part));
    memset(metadata_part, 0, sizeof(metadata_part));
    memset(temporary_video, 0, sizeof(temporary_video));
    memset(temporary_metadata, 0, sizeof(temporary_metadata));

    if (spool_validate_job_id(
            job_id,
            error,
            error_size) != 0) {
        result->failure = SPOOL_FAILURE_INVALID_INPUT;
        return -1;
    }

    if (create_directories(
            spool_root,
            error,
            error_size) != 0) {
        return -1;
    }

    if (path_format(
            result->job_directory,
            sizeof(result->job_directory),
            error,
            error_size,
            "%s/%s",
            spool_root,
            job_id) != 0 ||
        path_format(
            result->video_path,
            sizeof(result->video_path),
            error,
            error_size,
            "%s/%s",
            result->job_directory,
            "video.mp4") != 0 ||
        path_format(
            result->metadata_path,
            sizeof(result->metadata_path),
            error,
            error_size,
            "%s/%s",
            result->job_directory,
            "metadata.json") != 0) {
        result->failure = SPOOL_FAILURE_INVALID_INPUT;
        return -1;
    }

    if (stat(result->job_directory, &existing_information) == 0) {
        if (!S_ISDIR(existing_information.st_mode) ||
            validate_regular_file(
                result->video_path,
                &video_information,
                error,
                error_size) != 0 ||
            validate_regular_file(
                result->metadata_path,
                &metadata_information,
                error,
                error_size) != 0) {
            result->failure = SPOOL_FAILURE_SOURCE_NOT_READY;
            return -1;
        }

        result->already_present = 1;
        result->published = 1;
        result->failure = SPOOL_FAILURE_NONE;
        result->source_bytes =
            (uint64_t)video_information.st_size +
            (uint64_t)metadata_information.st_size;

        return 0;
    }

    if (errno != ENOENT) {
        snprintf(
            error,
            error_size,
            "stat final job directory: %s",
            strerror(errno));

        return -1;
    }

    if (validate_regular_file(
            source_video,
            &video_information,
            error,
            error_size) != 0 ||
        validate_regular_file(
            source_metadata,
            &metadata_information,
            error,
            error_size) != 0) {
        result->failure = SPOOL_FAILURE_SOURCE_NOT_READY;
        return -1;
    }

    required_bytes =
        (uint64_t)video_information.st_size +
        (uint64_t)metadata_information.st_size;

    if (required_bytes >
        UINT64_MAX - minimum_free_bytes) {
        result->failure = SPOOL_FAILURE_INVALID_INPUT;
        snprintf(error, error_size, "disk requirement overflow");
        return -1;
    }

    required_with_reserve =
        required_bytes + minimum_free_bytes;

    if (available_space(
            spool_root,
            &result->available_bytes,
            error,
            error_size) != 0) {
        return -1;
    }

    if (result->available_bytes < required_with_reserve) {
        result->failure = SPOOL_FAILURE_INSUFFICIENT_SPACE;
        snprintf(
            error,
            error_size,
            "insufficient spool space: "
            "available=%llu required_copy=%llu reserve=%llu",
            (unsigned long long)result->available_bytes,
            (unsigned long long)required_bytes,
            (unsigned long long)minimum_free_bytes);

        return -1;
    }

    result->source_bytes = required_bytes;

    if (snprintf(
            temporary_directory,
            sizeof(temporary_directory),
            "%s/.incoming-%s-%ld",
            spool_root,
            job_id,
            (long)getpid()) >=
        (int)sizeof(temporary_directory)) {
        snprintf(error, error_size, "temporary path exceeds limit");
        return -1;
    }

    if (mkdir(temporary_directory, 0750) != 0) {
        snprintf(
            error,
            error_size,
            "mkdir temporary directory: %s",
            strerror(errno));

        return -1;
    }
    temporary_created = 1;

    if (path_format(
            video_part,
            sizeof(video_part),
            error,
            error_size,
            "%s/%s",
            temporary_directory,
            "video.mp4.part") != 0 ||
        path_format(
            metadata_part,
            sizeof(metadata_part),
            error,
            error_size,
            "%s/%s",
            temporary_directory,
            "metadata.json.part") != 0 ||
        path_format(
            temporary_video,
            sizeof(temporary_video),
            error,
            error_size,
            "%s/%s",
            temporary_directory,
            "video.mp4") != 0 ||
        path_format(
            temporary_metadata,
            sizeof(temporary_metadata),
            error,
            error_size,
            "%s/%s",
            temporary_directory,
            "metadata.json") != 0) {
        goto cleanup;
    }

    if (copy_file_and_sync(
            source_video,
            video_part,
            error,
            error_size) != 0 ||
        copy_file_and_sync(
            source_metadata,
            metadata_part,
            error,
            error_size) != 0) {
        goto cleanup;
    }

    if (rename(video_part, temporary_video) != 0 ||
        rename(metadata_part, temporary_metadata) != 0) {
        snprintf(
            error,
            error_size,
            "rename completed file: %s",
            strerror(errno));

        goto cleanup;
    }

    if (fsync_directory(
            temporary_directory,
            error,
            error_size) != 0) {
        goto cleanup;
    }

    /*
     * 완성된 두 파일을 포함한 디렉터리 전체를 원자적으로 공개한다.
     */
    if (rename(
            temporary_directory,
            result->job_directory) != 0) {
        snprintf(
            error,
            error_size,
            "publish spool directory: %s",
            strerror(errno));

        goto cleanup;
    }
    result->published = 1;

    if (fsync_directory(
            spool_root,
            error,
            error_size) != 0) {
        /* 공개가 끝난 완성 spool은 오류가 나도 삭제하지 않는다. */
        goto cleanup;
    }

    result->already_present = 0;
    result->failure = SPOOL_FAILURE_NONE;
    operation_result = 0;

cleanup:
    if (operation_result != 0 && temporary_created != 0 &&
        result->published == 0) {
        result->cleanup_performed = 1;
        result->cleanup_succeeded = cleanup_temporary_directory(
            temporary_directory,
            video_part,
            metadata_part,
            temporary_video,
            temporary_metadata) == 0;
    }
    return operation_result;
}

int spool_cleanup_stale_incoming(
    const char *spool_root,
    uint32_t minimum_age_seconds,
    int *removed_count,
    int *failed_count,
    char *error,
    size_t error_size)
{
    DIR *directory;
    struct dirent *entry;
    time_t now = time(NULL);

    *removed_count = 0;
    *failed_count = 0;
    directory = opendir(spool_root);
    if (directory == NULL) {
        if (errno == ENOENT) {
            return 0;
        }
        snprintf(error, error_size, "open spool root: %s", strerror(errno));
        return -1;
    }

    while ((entry = readdir(directory)) != NULL) {
        char path[UPLOADER_PATH_SIZE];
        char video_part[UPLOADER_PATH_SIZE];
        char metadata_part[UPLOADER_PATH_SIZE];
        char video[UPLOADER_PATH_SIZE];
        char metadata[UPLOADER_PATH_SIZE];
        struct stat information;

        uploader_watchdog_progress(UPLOADER_LOCAL_PROGRESS_MS);
        if (strncmp(entry->d_name, INCOMING_PREFIX,
                    sizeof(INCOMING_PREFIX) - 1U) != 0 ||
            snprintf(path, sizeof(path), "%s/%s", spool_root, entry->d_name) >=
                (int)sizeof(path) ||
            lstat(path, &information) != 0 ||
            !S_ISDIR(information.st_mode) ||
            S_ISLNK(information.st_mode) || now == (time_t)-1 ||
            information.st_mtime > now ||
            (uint64_t)(now - information.st_mtime) < minimum_age_seconds) {
            continue;
        }

        if (snprintf(video_part, sizeof(video_part), "%s/video.mp4.part", path) >=
                (int)sizeof(video_part) ||
            snprintf(metadata_part, sizeof(metadata_part), "%s/metadata.json.part", path) >=
                (int)sizeof(metadata_part) ||
            snprintf(video, sizeof(video), "%s/video.mp4", path) >=
                (int)sizeof(video) ||
            snprintf(metadata, sizeof(metadata), "%s/metadata.json", path) >=
                (int)sizeof(metadata)) {
            (*failed_count)++;
            continue;
        }

        if (cleanup_temporary_directory(
                path, video_part, metadata_part, video, metadata) == 0) {
            (*removed_count)++;
        } else {
            /* 예상하지 않은 파일이 있으면 rmdir가 실패하여 보수적으로 보존됨. */
            (*failed_count)++;
        }
    }

    closedir(directory);
    return *failed_count == 0 ? 0 : -1;
}
