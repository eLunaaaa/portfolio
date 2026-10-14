/*
 * 합성곱 분산 처리의 MCU 독립 공통 로직입니다.
 * Master와 Slave가 같은 부하 계산·정수 직렬화·PWM 변환을 공유합니다.
 * Queue는 대기 FIFO, Batch는 행렬별 입력/필터/결과, Worker는 Slave별 미처리 작업, Scheduler는 전체 배정을 관리합니다.
 */

#ifndef LOAD_BALANCER_CORE_H
#define LOAD_BALANCER_CORE_H
#include <stdint.h>
#include <string.h>

namespace lb {
constexpr uint8_t Workers = 4, Patches = 9, QueueCapacity = 16, Credits = 2;
constexpr uint8_t CmdFilter = 0x01, CmdJob = 0x10, CmdAck = 0x20;
constexpr uint8_t JobBytes = 22, ResultBytes = 8, ResultHeader = 0xa5;

/* 작업 참조입니다. slot은 입력 행렬 저장 위치, patch는 그 행렬의 3×3 출력 위치(0~8)입니다. */
struct Job { uint8_t slot, patch; };
/* 아직 Slave에 보내지 않은 작업을 저장하는 원형 FIFO입니다. head는 읽을 위치, count는 대기 개수입니다. */
struct Queue {
    Job data[QueueCapacity];
    uint8_t head, count;
    /**
     * 기능: 원형 작업 큐의 뒤에 작업을 추가합니다.
     * 입력: job: batch slot과 patch index.
     * 출력: 추가하면 true, 용량이 가득 차면 false.
     * 처리 구조: head+count의 순환 위치에 쓰고 count만 증가시킵니다.
     */
    bool push(Job job) {
        if (count >= QueueCapacity) return false;
        data[(head + count) % QueueCapacity] = job; ++count; return true;
    }
    /**
     * 기능: 큐 맨 앞 작업을 제거하지 않고 확인합니다.
     * 입력: job: 앞 작업을 받을 참조.
     * 출력: 작업이 있으면 true와 job, 빈 큐면 false.
     * 처리 구조: head 위치를 복사하며 head/count는 변경하지 않습니다.
     */
    bool peek(Job &job) const {
        if (!count) return false;
        job = data[head]; return true;
    }
    /**
     * 기능: 큐 맨 앞 작업을 꺼내 제거합니다.
     * 입력: job: 꺼낸 작업을 받을 참조.
     * 출력: 성공이면 true, 빈 큐면 false.
     * 처리 구조: peek로 데이터를 확인한 뒤 head를 순환 이동하고 count를 감소시킵니다.
     */
    bool pop(Job &job) {
        if (!peek(job)) return false;
        head = (head + 1) % QueueCapacity; --count; return true;
    }
};
/* 한 입력 행렬의 수명주기를 보관합니다. input/filter를 고정해 두고 패치별 load/done/result를 함께 관리합니다. */
struct Batch {
    bool active;
    uint16_t sequence;
    int8_t input[25], filter[9];
    uint8_t load[9];
    bool done[9];
    int32_t result[9];
    uint8_t completed;
};
/* 한 Slave의 대기 큐·실행 목록·전체 미처리 부하입니다. pending은 이미 전송했지만 결과가 오지 않은 작업입니다. */
struct Worker {
    Queue queue;
    uint16_t total_load;
    Job pending[Credits];
    uint8_t pending_count;
};

/**
 * 기능: zero-skip을 반영한 패치의 가중 연산량을 계산합니다.
 * 입력: patch/filter: signed int8 9개 배열.
 * 출력: 유효 항이 n개일 때 3n-1, n=0일 때 0.
 * 처리 구조: 입력과 필터가 모두 nonzero인 항을 세고 곱셈 2단위·첫 항 이후 덧셈 1단위를 적용합니다.
 */
inline uint8_t operation_load(const int8_t *patch, const int8_t *filter) {
    uint8_t terms = 0;
    for (uint8_t i = 0; i < 9; ++i)
        if (patch[i] && filter[i]) ++terms;
    return terms ? (uint8_t)(3 * terms - 1) : 0; // mul=2, add=1
}
/**
 * 기능: 3×3 패치와 필터의 내적을 32비트로 계산합니다.
 * 입력: patch/filter: signed int8 배열 각각 9개.
 * 출력: signed int32 합성곱 결과.
 * 처리 구조: 0을 포함한 항은 건너뛰고 유효 항의 곱을 int32 누산기에 더합니다.
 */
inline int32_t convolution(const int8_t *patch, const int8_t *filter) {
    int32_t sum = 0;
    for (uint8_t i = 0; i < 9; ++i)
        if (patch[i] && filter[i]) sum += (int32_t)patch[i] * filter[i];
    return sum;
}
/**
 * 기능: 5×5 입력에서 지정한 3×3 패치를 복사합니다.
 * 입력: input: 25개 행 우선 배열, index: 출력 위치 0~8, patch: 9개 출력 공간.
 * 출력: patch에 해당 위치의 3×3 입력 값을 기록합니다.
 * 처리 구조: index를 출력 행·열로 바꾸고 해당 시작점의 3행·3열을 추출합니다.
 */
inline void extract_patch(const int8_t input[25], uint8_t index, int8_t patch[9]) {
    uint8_t row = index / 3, col = index % 3;
    for (uint8_t y = 0; y < 3; ++y)
        for (uint8_t x = 0; x < 3; ++x)
            patch[y * 3 + x] = input[(row + y) * 5 + col + x];
}
/**
 * 기능: 미처리 연산량을 Timer1의 유효 PWM compare 값으로 바꿉니다.
 * 입력: load: 현재 미처리 부하.
 * 출력: 250~500 tick, 즉 4 us tick 기준 1~2 ms 펄스.
 * 처리 구조: 두 작업의 최대 부하 52로 제한한 뒤 선형 변환해 음수·범위 초과 PWM을 방지합니다.
 */
inline uint16_t servo_ticks(uint16_t load) {
    if (load > 52) load = 52; // two patches, max 26 each
    return (uint16_t)(250 + ((uint32_t)load * 250) / 52); // 1..2 ms at 4 us/tick
}
/**
 * 기능: little-endian 2바이트를 unsigned 16비트로 해석합니다.
 * 입력: p: 최소 2바이트의 수신 공간.
 * 출력: uint16 값.
 * 처리 구조: 하위·상위 바이트를 shift/OR로 결합합니다.
 */
inline uint16_t get16(const uint8_t *p) { return (uint16_t)p[0] | ((uint16_t)p[1] << 8); }
/**
 * 기능: unsigned 16비트를 little-endian 배열로 저장합니다.
 * 입력: p: 최소 2바이트 출력 공간, v: 저장할 값.
 * 출력: p의 두 바이트를 갱신합니다.
 * 처리 구조: 하위 바이트부터 차례로 분리해 넣습니다.
 */
inline void put16(uint8_t *p, uint16_t v) { p[0] = (uint8_t)v; p[1] = (uint8_t)(v >> 8); }
/**
 * 기능: little-endian 4바이트를 signed 32비트 값으로 복원합니다.
 * 입력: p: 최소 4바이트 수신 공간.
 * 출력: 음수를 포함한 int32 값.
 * 처리 구조: unsigned 비트 패턴을 조합한 뒤 음수 영역은 범위 안의 산술로 변환해 부호를 복원합니다.
 */
inline int32_t get32(const uint8_t *p) {
    uint32_t v = (uint32_t)p[0] | ((uint32_t)p[1] << 8) |
                 ((uint32_t)p[2] << 16) | ((uint32_t)p[3] << 24);
    return v <= INT32_MAX ? (int32_t)v : -1 - (int32_t)(UINT32_MAX - v);
}
/**
 * 기능: signed 32비트 결과를 little-endian 4바이트로 저장합니다.
 * 입력: p: 4바이트 출력 공간, v: signed 결과값.
 * 출력: p에 결과의 32비트 패턴을 기록합니다.
 * 처리 구조: unsigned 패턴으로 바꾼 뒤 8비트씩 분리합니다.
 */
inline void put32(uint8_t *p, int32_t v) {
    uint32_t bits = (uint32_t)v;
    for (uint8_t i = 0; i < 4; ++i) p[i] = (uint8_t)(bits >> (8 * i));
}

/* 배치 2개와 Worker 4개의 배정을 통합합니다. accept부터 전송·완료·release까지 작업의 소유권을 관리합니다. */
struct Scheduler {
    Batch batches[2];
    Worker workers[Workers];
    uint16_t next_sequence;
    /**
     * 기능: 스케줄러의 배치·Slave·큐 상태를 초기화합니다.
     * 입력: 없음. 호출한 Scheduler 인스턴스를 사용합니다.
     * 출력: 두 배치와 네 Worker, 다음 sequence가 0으로 초기화됩니다.
     * 처리 구조: 시작 상태 전체를 memset으로 비웁니다.
     */
    void init() { memset(this, 0, sizeof(*this)); }
    /**
     * 기능: 새 행렬을 저장할 수 있는 배치 버퍼를 찾습니다.
     * 입력: 없음. batches[2]의 active 상태를 읽습니다.
     * 출력: 빈 slot 번호 0/1, 모두 사용 중이면 -1.
     * 처리 구조: 두 slot을 순서대로 검사하며 active=false인 첫 위치를 선택합니다.
     */
    int8_t free_slot() const {
        for (uint8_t i = 0; i < 2; ++i) if (!batches[i].active) return (int8_t)i;
        return -1;
    }
    /**
     * 기능: 현재 미처리 부하가 가장 적은 Slave를 선택합니다.
     * 입력: 없음. Worker별 queue·pending·total_load 상태를 읽습니다.
     * 출력: 대상 Worker 번호, 모두 queue가 가득 차면 -1.
     * 처리 구조: 큐 공간이 있는 Worker만 비교하며 동률이면 작업 수가 적은 Worker를 선택합니다.
     */
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
    /**
     * 기능: 새 입력·필터를 배치로 보관하고 9개 작업을 배정합니다.
     * 입력: input: int8 25개, filter: int8 9개, sequence: 배치 번호를 받을 참조.
     * 출력: 수용하면 true와 sequence, 배치·큐 공간이 부족하면 false.
     * 처리 구조: 빈 slot·전체 큐 공간을 확인하고 입력/필터를 복사합니다. active sequence 중복을 피해 번호를 주고 패치별 부하를 계산해 Greedy 배정합니다.
     */
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
    /**
     * 기능: 특정 Worker에 전송할 다음 작업을 확인합니다.
     * 입력: worker: Slave 번호 0~3, job: 조회 결과 참조.
     * 출력: 전송 credit와 대기 작업이 있으면 true와 job.
     * 처리 구조: Worker 범위와 pending<2를 검사하고 queue의 head를 제거 없이 읽습니다.
     */
    bool next_job(uint8_t worker, Job &job) const {
        return worker < Workers && workers[worker].pending_count < Credits &&
               workers[worker].queue.peek(job);
    }
    /**
     * 기능: 전송이 끝난 작업을 대기 큐에서 실행 목록으로 옮깁니다.
     * 입력: worker: 전송 대상 Slave 번호.
     * 출력: 이동에 성공하면 true, credit·큐 조건이 맞지 않으면 false.
     * 처리 구조: 전송 성공 뒤 호출하며 queue에서 pop한 job을 pending 배열에 추가합니다.
     */
    bool mark_sent(uint8_t worker) {
        if (worker >= Workers || workers[worker].pending_count >= Credits) return false;
        Job job;
        if (!workers[worker].queue.pop(job)) return false;
        workers[worker].pending[workers[worker].pending_count++] = job;
        return true;
    }
    /**
     * 기능: 수신 결과가 실제 해당 Worker의 미완료 작업인지 확인합니다.
     * 입력: worker: 응답 Slave, sequence: 배치 번호, patch: 패치 번호.
     * 출력: 미완료 pending 작업과 일치하면 true.
     * 처리 구조: active 배치·sequence·patch와 done 여부를 함께 검사해 중복·과거 결과를 구분합니다.
     */
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
    /**
     * 기능: 검증한 결과를 배치에 저장하고 Worker의 부하를 차감합니다.
     * 입력: worker/sequence/patch: 작업 식별자, value: 합성곱 결과.
     * 출력: 반영한 batch slot, 잘못되거나 중복된 결과이면 -1.
     * 처리 구조: expected 검사 후 result/done/완료 개수를 갱신하고 부하와 pending 작업 하나를 제거합니다.
     */
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
    /**
     * 기능: 결과 9개를 모두 출력한 배치 버퍼를 재사용 가능하게 만듭니다.
     * 입력: slot: 해제할 배치 번호 0/1.
     * 출력: 유효한 완료 배치이면 true, 아니면 false.
     * 처리 구조: active와 completed=9를 확인하고 active를 false로 바꿉니다.
     */
    bool release(uint8_t slot) {
        if (slot >= 2 || !batches[slot].active || batches[slot].completed != Patches) return false;
        batches[slot].active = false;
        return true;
    }
};
}
#endif
