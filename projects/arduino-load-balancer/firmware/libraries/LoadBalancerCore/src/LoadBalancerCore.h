#ifndef LOAD_BALANCER_CORE_H
#define LOAD_BALANCER_CORE_H
#include <stdint.h>
#include <string.h>

namespace lb {
constexpr uint8_t Workers = 4, Patches = 9, QueueCapacity = 16, Credits = 2;
constexpr uint8_t CmdFilter = 0x01, CmdJob = 0x10, CmdAck = 0x20;
constexpr uint8_t JobBytes = 22, ResultBytes = 8, ResultHeader = 0xa5;

struct Job { uint8_t slot, patch; };
struct Queue {
    Job data[QueueCapacity];
    uint8_t head, count;
    bool push(Job job) {
        if (count >= QueueCapacity) return false;
        data[(head + count) % QueueCapacity] = job; ++count; return true;
    }
    bool peek(Job &job) const {
        if (!count) return false;
        job = data[head]; return true;
    }
    bool pop(Job &job) {
        if (!peek(job)) return false;
        head = (head + 1) % QueueCapacity; --count; return true;
    }
};
struct Batch {
    bool active;
    uint16_t sequence;
    int8_t input[25], filter[9];
    uint8_t load[9];
    bool done[9];
    int32_t result[9];
    uint8_t completed;
};
struct Worker {
    Queue queue;
    uint16_t total_load;
    Job pending[Credits];
    uint8_t pending_count;
};

inline uint8_t operation_load(const int8_t *patch, const int8_t *filter) {
    uint8_t terms = 0;
    for (uint8_t i = 0; i < 9; ++i)
        if (patch[i] && filter[i]) ++terms;
    return terms ? (uint8_t)(3 * terms - 1) : 0; // mul=2, add=1
}
inline int32_t convolution(const int8_t *patch, const int8_t *filter) {
    int32_t sum = 0;
    for (uint8_t i = 0; i < 9; ++i)
        if (patch[i] && filter[i]) sum += (int32_t)patch[i] * filter[i];
    return sum;
}
inline void extract_patch(const int8_t input[25], uint8_t index, int8_t patch[9]) {
    uint8_t row = index / 3, col = index % 3;
    for (uint8_t y = 0; y < 3; ++y)
        for (uint8_t x = 0; x < 3; ++x)
            patch[y * 3 + x] = input[(row + y) * 5 + col + x];
}
inline uint16_t servo_ticks(uint16_t load) {
    if (load > 52) load = 52; // two patches, max 26 each
    return (uint16_t)(250 + ((uint32_t)load * 250) / 52); // 1..2 ms at 4 us/tick
}
inline uint16_t get16(const uint8_t *p) { return (uint16_t)p[0] | ((uint16_t)p[1] << 8); }
inline void put16(uint8_t *p, uint16_t v) { p[0] = (uint8_t)v; p[1] = (uint8_t)(v >> 8); }
inline int32_t get32(const uint8_t *p) {
    uint32_t v = (uint32_t)p[0] | ((uint32_t)p[1] << 8) |
                 ((uint32_t)p[2] << 16) | ((uint32_t)p[3] << 24);
    return v <= INT32_MAX ? (int32_t)v : -1 - (int32_t)(UINT32_MAX - v);
}
inline void put32(uint8_t *p, int32_t v) {
    uint32_t bits = (uint32_t)v;
    for (uint8_t i = 0; i < 4; ++i) p[i] = (uint8_t)(bits >> (8 * i));
}

struct Scheduler {
    Batch batches[2];
    Worker workers[Workers];
    uint16_t next_sequence;
    void init() { memset(this, 0, sizeof(*this)); }
    int8_t free_slot() const {
        for (uint8_t i = 0; i < 2; ++i) if (!batches[i].active) return (int8_t)i;
        return -1;
    }
    int8_t choose_worker() const {
        int8_t best = -1;
        for (uint8_t i = 0; i < Workers; ++i) {
            if (workers[i].queue.count >= QueueCapacity) continue;
            if (best < 0 || workers[i].total_load < workers[best].total_load ||
                (workers[i].total_load == workers[best].total_load &&
                 workers[i].queue.count + workers[i].pending_count <
                 workers[best].queue.count + workers[best].pending_count)) best = (int8_t)i;
        }
        return best;
    }
    bool accept(const int8_t input[25], const int8_t filter[9], uint16_t &sequence) {
        int8_t slot = free_slot();
        if (slot < 0) return false;
        unsigned capacity = 0;
        for (uint8_t i = 0; i < Workers; ++i) capacity += QueueCapacity - workers[i].queue.count;
        if (capacity < Patches) return false;
        Batch &batch = batches[slot];
        memset(&batch, 0, sizeof(batch));
        uint16_t proposed = next_sequence++;
        uint8_t other = (uint8_t)(1 - slot);
        if (batches[other].active && batches[other].sequence == proposed) proposed = next_sequence++;
        batch.active = true; batch.sequence = proposed;
        memcpy(batch.input, input, 25); memcpy(batch.filter, filter, 9);
        for (uint8_t p = 0; p < Patches; ++p) {
            int8_t patch[9]; extract_patch(input, p, patch);
            batch.load[p] = operation_load(patch, filter);
            int8_t worker = choose_worker();
            workers[worker].queue.push(Job{(uint8_t)slot, p});
            workers[worker].total_load += batch.load[p];
        }
        sequence = batch.sequence;
        return true;
    }
    bool next_job(uint8_t worker, Job &job) const {
        return worker < Workers && workers[worker].pending_count < Credits &&
               workers[worker].queue.peek(job);
    }
    bool mark_sent(uint8_t worker) {
        if (worker >= Workers || workers[worker].pending_count >= Credits) return false;
        Job job;
        if (!workers[worker].queue.pop(job)) return false;
        workers[worker].pending[workers[worker].pending_count++] = job;
        return true;
    }
    bool expected(uint8_t worker, uint16_t sequence, uint8_t patch) const {
        if (worker >= Workers || patch >= Patches) return false;
        const Worker &w = workers[worker];
        for (uint8_t i = 0; i < w.pending_count; ++i) {
            Job j = w.pending[i];
            if (j.patch == patch && batches[j.slot].active &&
                batches[j.slot].sequence == sequence && !batches[j.slot].done[patch]) return true;
        }
        return false;
    }
    int8_t complete(uint8_t worker, uint16_t sequence, uint8_t patch, int32_t value) {
        if (!expected(worker, sequence, patch)) return -1;
        Worker &w = workers[worker];
        for (uint8_t i = 0; i < w.pending_count; ++i) {
            Job j = w.pending[i]; Batch &batch = batches[j.slot];
            if (j.patch != patch || batch.sequence != sequence) continue;
            batch.done[patch] = true; batch.result[patch] = value; ++batch.completed;
            w.total_load -= batch.load[patch];
            for (uint8_t n = i + 1; n < w.pending_count; ++n) w.pending[n - 1] = w.pending[n];
            --w.pending_count;
            return (int8_t)j.slot;
        }
        return -1;
    }
    bool release(uint8_t slot) {
        if (slot >= 2 || !batches[slot].active || batches[slot].completed != Patches) return false;
        batches[slot].active = false;
        return true;
    }
};
}
#endif
