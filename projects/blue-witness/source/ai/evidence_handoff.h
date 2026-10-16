#ifndef ACE_EVIDENCE_HANDOFF_H
#define ACE_EVIDENCE_HANDOFF_H

#include <cerrno>
#include <chrono>
#include <cmath>
#include <condition_variable>
#include <cstdint>
#include <cstddef>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <dirent.h>
#include <exception>
#include <fcntl.h>
#include <list>
#include <mutex>
#include <string>
#include <sys/stat.h>
#include <thread>
#include <unistd.h>

/* A record is committed only after the encoded MP4 has been closed and fsynced.
 * Its presence also permits recovery of the completed .part -> .mp4 rename.
 * The wire payload is saved unchanged before its first delivery to Uploader. */
class EvidenceHandoff {
public:
    using PrepareTime = void (*)(uint32_t, uploader_evidence_ready_t *);
    using Deliver = int (*)(uint32_t, const uploader_evidence_ready_t *);
    using Progress = void (*)();

    EvidenceHandoff(PrepareTime prepare_time, Deliver deliver, Progress progress)
        : prepare_time_(prepare_time), deliver_(deliver), progress_(progress) {}

    int start(const char *directory, Progress restore_progress = nullptr)
    {
        const int created = mkdir(directory, 0755);
        if (created != 0 && errno != EEXIST)
            return -1;
        char *absolute = realpath(directory, nullptr);
        if (absolute == nullptr)
            return -1;
        directory_ = absolute;
        free(absolute);
        if (created == 0) {
            const size_t slash = directory_.find_last_of('/');
            const std::string parent = slash == 0 ? "/" : directory_.substr(0, slash);
            if (!sync_directory_path(parent))
                return -1;
        }
        try {
            if (restore(restore_progress) != 0)
                return -1;
            thread_ = std::thread(&EvidenceHandoff::run, this);
        } catch (const std::exception &error) {
            fprintf(stderr, "Uploader handoff 시작 실패: %s\n", error.what());
            return -1;
        }
        return 0;
    }

    ~EvidenceHandoff()
    {
        {
            std::lock_guard<std::mutex> lock(mutex_);
            closed_ = true;
            available_.notify_all();
        }
        if (thread_.joinable())
            thread_.join();
    }

    static bool has_record(const uploader_evidence_ready_t &ready)
    {
        struct stat status;
        return lstat(record_path(ready).c_str(), &status) == 0;
    }

    static bool can_publish(const uploader_evidence_ready_t &ready)
    {
        struct stat status;
        if (lstat(ready.video_path, &status) == 0 || errno != ENOENT)
            return false;
        if (lstat(record_path(ready).c_str(), &status) == 0 || errno != ENOENT)
            return false;
        return true;
    }

    int publish(const char *part_path, uint32_t sequence,
                const uploader_evidence_ready_t &ready)
    {
        if (!can_publish(ready) || !sync_file(part_path))
            return -1;
        Pending item{};
        memcpy(item.record.magic, "ACEHD01", 8);
        item.record.bytes = sizeof(Record);
        item.record.sequence = sequence;
        item.record.ready = ready;
        snprintf(item.record.ready.time_sync_status,
                 sizeof(item.record.ready.time_sync_status), "UNRELIABLE");
        item.record.ready.chrony_offset_ms = 0.0;
        if (!save_record(item.record) && !has_record(item.record.ready))
            return -1;
        /* A recoverable completed part remains queued if rename/fsync fails. */
        (void)finish_publish(item);
        try {
            std::lock_guard<std::mutex> lock(mutex_);
            pending_.push_back(item);
            available_.notify_one();
        } catch (const std::exception &error) {
            fprintf(stderr, "Uploader 인계 RAM 등록 실패; 디스크 기록 유지: %s\n",
                    error.what());
            return -1;
        }
        return 0;
    }

private:
    struct Record {
        char magic[8];
        uint32_t bytes;
        uint32_t sequence;
        uint32_t time_queried;
        uint32_t checksum;
        uploader_evidence_ready_t ready;
    };
    struct Pending {
        Record record;
        bool time_durable = false;
        bool published = false;
        bool failure_logged = false;
    };
    std::string directory_;
    PrepareTime prepare_time_;
    Deliver deliver_;
    Progress progress_;
    std::mutex mutex_;
    std::condition_variable available_;
    std::list<Pending> pending_;
    bool closed_ = false;
    std::thread thread_;

    static std::string record_path(const uploader_evidence_ready_t &ready)
    {
        return std::string(ready.video_path) + ".handoff";
    }

    static uint32_t checksum(const Record &record)
    {
        uint32_t value = 2166136261U;
        const auto *bytes = reinterpret_cast<const unsigned char *>(&record);
        for (size_t i = 0; i < sizeof(record); ++i) {
            const bool checksum_byte = i >= offsetof(Record, checksum) &&
                                       i < offsetof(Record, checksum) + sizeof(record.checksum);
            value = (value ^ (checksum_byte ? 0U : bytes[i])) * 16777619U;
        }
        return value;
    }

    static bool sync_file(const char *path)
    {
        int fd = open(path, O_RDONLY | O_CLOEXEC | O_NOFOLLOW);
        if (fd < 0)
            return false;
        struct stat status;
        bool ok = fstat(fd, &status) == 0 && S_ISREG(status.st_mode) &&
                  status.st_size > 0 && fsync(fd) == 0;
        if (close(fd) != 0)
            ok = false;
        return ok;
    }

    static bool sync_directory_path(const std::string &path)
    {
        int fd = open(path.c_str(), O_RDONLY | O_DIRECTORY | O_CLOEXEC);
        if (fd < 0)
            return false;
        bool ok = fsync(fd) == 0;
        if (close(fd) != 0)
            ok = false;
        return ok;
    }

    bool sync_directory() const
    {
        return sync_directory_path(directory_);
    }

    bool finish_publish(Pending &item)
    {
        /* Retry the record-directory barrier before exposing the MP4. */
        if (!sync_directory())
            return false;
        const char *video = item.record.ready.video_path;
        struct stat status;
        if (lstat(video, &status) == 0) {
            if (!S_ISREG(status.st_mode) || status.st_size <= 0)
                return false;
        } else {
            const std::string part = std::string(video) + ".part";
            if (errno != ENOENT || !sync_file(part.c_str()) ||
                rename(part.c_str(), video) != 0)
                return false;
        }
        item.published = sync_directory();
        return item.published;
    }

    bool save_record(Record record)
    {
        record.checksum = checksum(record);
        const std::string path = record_path(record.ready);
        const std::string temporary = path + ".new";
        int fd = open(temporary.c_str(), O_WRONLY | O_CREAT | O_TRUNC |
                      O_CLOEXEC | O_NOFOLLOW, 0600);
        if (fd < 0)
            return false;
        const char *data = reinterpret_cast<const char *>(&record);
        size_t written = 0;
        while (written < sizeof(record)) {
            ssize_t count = write(fd, data + written, sizeof(record) - written);
            if (count < 0 && errno == EINTR)
                continue;
            if (count <= 0)
                break;
            written += static_cast<size_t>(count);
        }
        bool ok = written == sizeof(record) && fsync(fd) == 0;
        if (close(fd) != 0)
            ok = false;
        if (ok)
            ok = rename(temporary.c_str(), path.c_str()) == 0 && sync_directory();
        if (!ok)
            fprintf(stderr, "Uploader 인계 기록 저장 실패: %s (%s)\n",
                    path.c_str(), strerror(errno));
        return ok;
    }

    static bool terminated(const char *text, size_t size)
    {
        return memchr(text, '\0', size) != nullptr;
    }

    bool load_record(const std::string &path, const std::string &video,
                     Record *record)
    {
        int fd = open(path.c_str(), O_RDONLY | O_CLOEXEC | O_NOFOLLOW);
        if (fd < 0)
            return false;
        struct stat status;
        bool ok = fstat(fd, &status) == 0 && S_ISREG(status.st_mode) &&
                  status.st_size == static_cast<off_t>(sizeof(*record));
        size_t received = 0;
        while (ok && received < sizeof(*record)) {
            ssize_t count = read(fd, reinterpret_cast<char *>(record) + received,
                                 sizeof(*record) - received);
            if (count < 0 && errno == EINTR)
                continue;
            if (count <= 0) {
                ok = false;
                break;
            }
            received += static_cast<size_t>(count);
        }
        close(fd);
        const auto &ready = record->ready;
        return ok && received == sizeof(*record) &&
               memcmp(record->magic, "ACEHD01", 8) == 0 &&
               record->bytes == sizeof(*record) &&
               record->time_queried <= 1 && record->checksum == checksum(*record) &&
               ready.version == UPLOADER_PROTOCOL_VERSION &&
               terminated(ready.video_path, sizeof(ready.video_path)) &&
               terminated(ready.action_id, sizeof(ready.action_id)) &&
               terminated(ready.vehicle_id, sizeof(ready.vehicle_id)) &&
               terminated(ready.accident_vehicle_id, sizeof(ready.accident_vehicle_id)) &&
               terminated(ready.time_sync_status, sizeof(ready.time_sync_status)) &&
               std::isfinite(ready.chrony_offset_ms) && video == ready.video_path;
    }

    int restore(Progress restore_progress)
    {
        DIR *directory = opendir(directory_.c_str());
        if (directory == nullptr)
            return -1;
        int result = 0;
        while (dirent *entry = readdir(directory)) {
            progress_();
            if (restore_progress != nullptr)
                restore_progress();
            const std::string name = entry->d_name;
            const std::string suffix = ".mp4.handoff";
            if (name.size() <= suffix.size() ||
                name.compare(name.size() - suffix.size(), suffix.size(), suffix) != 0)
                continue;
            const std::string path = directory_ + "/" + name;
            const std::string video = path.substr(0, path.size() - 8);
            Pending item{};
            if (!load_record(path, video, &item.record)) {
                fprintf(stderr, "손상된 인계 기록 보존; 전송 제외: %s\n", path.c_str());
                continue;
            }
            /* Only this verified completion record permits .part recovery.
             * File/rename failures remain queued without blocking capture. */
            (void)finish_publish(item);
            item.time_durable = item.record.time_queried != 0;
            pending_.push_back(item);
            fprintf(stderr, "Uploader 인계 복원: action_id=%s, video=%s\n",
                    item.record.ready.action_id, video.c_str());
        }
        closedir(directory);
        return result;
    }

    void run()
    {
        std::unique_lock<std::mutex> lock(mutex_);
        while (!closed_) {
            progress_();
            if (pending_.empty()) {
                available_.wait_for(lock, std::chrono::seconds(1));
                continue;
            }
            auto current = pending_.begin();
            Pending &item = *current;
            lock.unlock();
            if (!item.published)
                (void)finish_publish(item);
            if (item.published && !item.record.time_queried) {
                prepare_time_(item.record.sequence, &item.record.ready);
                item.record.time_queried = 1;
                progress_();
            }
            if (item.published && !item.time_durable)
                item.time_durable = save_record(item.record);
            int result = item.published && item.time_durable
                             ? deliver_(item.record.sequence, &item.record.ready) : -1;
            progress_();
            if (result == 0) {
                const std::string path = record_path(item.record.ready);
                /* An ACK is local durable acceptance. A lost ACK is retried
                 * with the identical saved payload; Uploader deduplicates it. */
                if ((unlink(path.c_str()) != 0 && errno != ENOENT) || !sync_directory())
                    result = -1;
            }
            lock.lock();
            if (result == 0) {
                pending_.erase(current);
            } else {
                if (!item.failure_logged) {
                    fprintf(stderr, "Uploader 인계 대기: action_id=%s, video=%s\n",
                            item.record.ready.action_id, item.record.ready.video_path);
                    item.failure_logged = true;
                }
                pending_.splice(pending_.end(), pending_, current);
                available_.wait_for(lock, std::chrono::seconds(1),
                                    [this] { return closed_; });
            }
        }
    }
};

#endif
