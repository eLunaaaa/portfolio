/*
 * 기능 검산용 테스트 파일입니다.
 * 고정·경계·오류 입력 또는 대역 환경에서 함수 결과를 기대값과 비교합니다.
 */

#include <cassert>
#include <climits>
#include <cstdio>
#include <random>
#include "LoadBalancerCore.h"

/**
 * 기능: zero-skip 부하·32비트 연산, byte order codec, PWM 상·하한을 검사합니다.
 * 입력: 함수 내부의 고정 표본·대역 환경.
 * 출력: 정상 조건은 assertion 통과, 차이는 assertion/테스트 실패.
 * 처리 구조: 정상·경계·오류 조건을 구성해 기능의 기대 결과와 비교합니다.
 */
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
/**
 * 기능: 원형 FIFO의 빈 상태·포화·순서·인덱스 순환을 반복 검사합니다.
 * 입력: 함수 내부의 고정 표본·대역 환경.
 * 출력: 정상 조건은 assertion 통과, 차이는 assertion/테스트 실패.
 * 처리 구조: 정상·경계·오류 조건을 구성해 기능의 기대 결과와 비교합니다.
 */
static void test_queue() {
    lb::Queue q{}; lb::Job j{};
    assert(!q.pop(j));
    for (int round = 0; round < 20; ++round) {
        for (uint8_t i = 0; i < lb::QueueCapacity; ++i) assert(q.push({0,i}));
        assert(!q.push({0,99}));
        for (uint8_t i = 0; i < lb::QueueCapacity; ++i) { assert(q.pop(j)); assert(j.patch == i); }
    }
}
/**
 * 기능: 지정 출력 위치 하나의 기준 합성곱을 계산합니다.
 * 입력: input: 5×5 signed 배열, filter: 3×3 signed 배열, index: 출력 위치 0~8.
 * 출력: 해당 출력 위치의 signed int32 기준 결과.
 * 처리 구조: index의 행·열에서 시작하는 3×3 영역을 직접 곱해 더하고 스케줄러의 결과와 비교합니다.
 */
static int32_t reference(const int8_t input[25], const int8_t filter[9], int index) {
    int32_t sum = 0;
    for (int y = 0; y < 3; ++y) for (int x = 0; x < 3; ++x)
        sum += (int32_t)input[(index / 3 + y) * 5 + index % 3 + x] * filter[y * 3 + x];
    return sum;
}
/**
 * 기능: 두 행렬·필터의 동시 배정과 무작위 완료 순서를 100회 기준 계산과 비교합니다.
 * 입력: 함수 내부의 고정 표본·대역 환경.
 * 출력: 정상 조건은 assertion 통과, 차이는 assertion/테스트 실패.
 * 처리 구조: 정상·경계·오류 조건을 구성해 기능의 기대 결과와 비교합니다.
 */
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
/**
 * 기능: 이 파일의 기능 검산을 실행합니다.
 * 입력: 고정 표본·경계값과 필요 시 대역 환경.
 * 출력: 검사가 모두 맞으면 성공 로그와 정상 종료, 다르면 assertion 실패.
 * 처리 구조: 테스트 함수 또는 프로토콜 시나리오를 순서대로 실행합니다.
 */
int main() {
    test_math_protocol(); test_queue(); test_pipeline();
    lb::Scheduler wrap; wrap.init();
    int8_t zero_input[25] = {}, zero_filter[9] = {}; uint16_t sequence;
    assert(wrap.accept(zero_input,zero_filter,sequence) && sequence == 0);
    wrap.next_sequence = 0;
    assert(wrap.accept(zero_input,zero_filter,sequence) && sequence == 1);
    puts("Math, signed protocol, PWM, queue and 100 two-matrix pipeline rounds passed");
}
