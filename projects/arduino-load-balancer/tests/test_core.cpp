#include <cassert>
#include <climits>
#include <cstdio>
#include <random>
#include "LoadBalancerCore.h"

static void test_math_protocol() {
    int8_t patch[9], filter[9];
    for (int i = 0; i < 9; ++i) { patch[i] = -128; filter[i] = -128; }
    assert(lb::convolution(patch,filter) == 147456);
    assert(lb::operation_load(patch,filter) == 26);
    filter[0] = 0; assert(lb::operation_load(patch,filter) == 23);
    memset(patch,0,sizeof(patch)); assert(lb::convolution(patch,filter) == 0);
    assert(lb::operation_load(patch,filter) == 0);
    int8_t input[25]; for (int i = 0; i < 25; ++i) input[i] = (int8_t)i;
    lb::extract_patch(input,8,patch);
    assert(patch[0] == 12 && patch[8] == 24);
    for (int32_t n : {INT32_MIN, -147456, -1, 0, 147456, INT32_MAX}) {
        uint8_t bytes[4]; lb::put32(bytes,n); assert(lb::get32(bytes) == n);
    }
    uint8_t bytes[2]; lb::put16(bytes,65535); assert(lb::get16(bytes) == 65535);
    assert(lb::servo_ticks(0) == 250 && lb::servo_ticks(52) == 500);
    assert(lb::servo_ticks(65535) == 500);
}
static void test_queue() {
    lb::Queue q{}; lb::Job j{};
    assert(!q.pop(j));
    for (int round = 0; round < 20; ++round) {
        for (uint8_t i = 0; i < lb::QueueCapacity; ++i) assert(q.push({0,i}));
        assert(!q.push({0,99}));
        for (uint8_t i = 0; i < lb::QueueCapacity; ++i) { assert(q.pop(j)); assert(j.patch == i); }
    }
}
static int32_t reference(const int8_t input[25], const int8_t filter[9], int index) {
    int32_t sum = 0;
    for (int y = 0; y < 3; ++y) for (int x = 0; x < 3; ++x)
        sum += (int32_t)input[(index / 3 + y) * 5 + index % 3 + x] * filter[y * 3 + x];
    return sum;
}
static void test_pipeline() {
    std::mt19937 random(20261014);
    lb::Scheduler s; s.init(); s.next_sequence = 65535;
    for (int round = 0; round < 100; ++round) {
        int8_t inputs[2][25], filters[2][9]; uint16_t sequences[2];
        for (int b = 0; b < 2; ++b) {
            for (int i = 0; i < 25; ++i) inputs[b][i] = round % 4 ? (int8_t)(random() % 255 - 127) : 0;
            for (int i = 0; i < 9; ++i) filters[b][i] = (int8_t)(random() % 255 - 127);
            assert(s.accept(inputs[b],filters[b],sequences[b]));
        }
        assert(!s.accept(inputs[0],filters[0],sequences[0]));
        assert(s.free_slot() == -1);
        int completed = 0;
        while (completed < 18) {
            for (uint8_t w = 0; w < lb::Workers; ++w) {
                lb::Job j;
                while (s.next_job(w,j)) assert(s.mark_sent(w));
                assert(s.workers[w].pending_count <= 2);
            }
            uint8_t worker = (uint8_t)(random() % lb::Workers);
            lb::Worker &w = s.workers[worker];
            if (!w.pending_count) continue;
            lb::Job j = w.pending[random() % w.pending_count];
            uint16_t sequence = s.batches[j.slot].sequence;
            int32_t value = reference(inputs[j.slot],filters[j.slot],j.patch);
            assert(!s.expected(worker,sequence,9));
            assert(s.complete(worker,sequence,j.patch,value) == j.slot);
            assert(s.complete(worker,sequence,j.patch,value) == -1); // replay
            ++completed;
        }
        for (int b = 0; b < 2; ++b) {
            assert(s.batches[b].completed == 9);
            for (int p = 0; p < 9; ++p) assert(s.batches[b].result[p] == reference(inputs[b],filters[b],p));
            assert(s.release(b)); assert(!s.release(b));
        }
        for (const auto &w : s.workers) assert(w.total_load == 0 && w.queue.count == 0 && w.pending_count == 0);
    }
}
int main() {
    test_math_protocol(); test_queue(); test_pipeline();
    lb::Scheduler wrap; wrap.init();
    int8_t zero_input[25] = {}, zero_filter[9] = {}; uint16_t sequence;
    assert(wrap.accept(zero_input,zero_filter,sequence) && sequence == 0);
    wrap.next_sequence = 0;
    assert(wrap.accept(zero_input,zero_filter,sequence) && sequence == 1);
    puts("Math, signed protocol, PWM, queue and 100 two-matrix pipeline rounds passed");
}
