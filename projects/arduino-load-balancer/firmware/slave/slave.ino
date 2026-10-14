/*
 * 분산 합성곱 시스템의 Slave Arduino 공통 코드입니다.
 * Master의 TWI 작업을 두 slot에 저장해 처리하고 결과를 ACK까지 보관합니다.
 * Timer2는 시연 지연의 시간 기준, Timer1은 남은 부하를 보여주는 서보 PWM이며 주소는 SLAVE_ADDRESS로 지정합니다.
 */

/* Shared worker firmware; compile separately with SLAVE_ADDRESS=1..4. */
#include <Arduino.h>
#include <avr/io.h>
#include <avr/interrupt.h>
#include <util/atomic.h>
#include <LoadBalancerCore.h>
#ifndef SLAVE_ADDRESS
#define SLAVE_ADDRESS 1
#endif
#if SLAVE_ADDRESS < 1 || SLAVE_ADDRESS > 4
#error "SLAVE_ADDRESS must be 1..4"
#endif
#ifndef DEMO_MUL_MS
#define DEMO_MUL_MS 2000UL
#endif
#ifndef DEMO_ADD_MS
#define DEMO_ADD_MS 1000UL
#endif

enum SlotState : uint8_t { Free, Ready, Processing, Result };
struct Slot {
    uint8_t state;
    uint16_t sequence;
    uint8_t patch;
    int8_t input[9], filter[9];
    int32_t result;
};
static volatile Slot slots[2];
static volatile uint8_t ready_order[2], ready_head, ready_count;
static volatile uint8_t result_order[2], result_head, result_count;
static volatile uint8_t receive_packet[lb::JobBytes], receive_count, tx_index;
static volatile bool receive_valid, tx_valid;
static volatile uint8_t transmit_packet[lb::ResultBytes];
static volatile uint16_t total_load;
static volatile uint32_t tick_ms;
static volatile int8_t broadcast_filter[9];

/**
 * 기능: Slave의 1 ms 시간 기준을 누적합니다.
 * 입력: Timer2 compare match interrupt.
 * 출력: tick_ms를 1 증가시킵니다.
 * 처리 구조: 연산 시연 지연 함수가 이 누적값의 atomic snapshot으로 경과 시간을 계산합니다.
 */
ISR(TIMER2_COMPA_vect) { ++tick_ms; }
/**
 * 기능: Slave Timer2의 1 ms 시스템 tick을 일관되게 읽습니다.
 * 입력: 없음. tick_ms 누적값을 사용합니다.
 * 출력: uint32 ms 시각.
 * 처리 구조: 8비트 AVR에서 여러 바이트를 읽는 동안 ISR이 변경하지 않도록 atomic snapshot을 사용합니다.
 */
static uint32_t clock_ms() { uint32_t t; ATOMIC_BLOCK(ATOMIC_RESTORESTATE) { t = tick_ms; } return t; }
/**
 * 기능: 현재 Slave 미처리 부하를 PWM 출력에 반영합니다.
 * 입력: 없음. total_load 공유값을 사용합니다.
 * 출력: OCR1A에 servo_ticks가 계산한 compare를 기록합니다.
 * 처리 구조: 부하와 16비트 PWM 레지스터 접근을 하나의 atomic 구간에서 처리합니다.
 */
static void update_servo() {
    ATOMIC_BLOCK(ATOMIC_RESTORESTATE) { OCR1A = lb::servo_ticks(total_load); }
}
/**
 * 기능: 시연 연산 단계의 시간 지연을 수행합니다.
 * 입력: ms: 기다릴 millisecond 수.
 * 출력: 지정 시간이 지나면 반환합니다.
 * 처리 구조: Timer2 경과 시간을 unsigned 차이로 비교하고 대기 중에도 서보를 갱신하며 ISR 수신을 허용합니다.
 */
static void wait_ms(uint32_t ms) {
    uint32_t start = clock_ms();
    while ((uint32_t)(clock_ms() - start) < ms) update_servo();
}
/**
 * 기능: Slave의 결과 준비 상태를 Master 완료선에 표시합니다.
 * 입력: 없음. result_count를 읽습니다.
 * 출력: 결과가 있으면 D2 LOW 유지, 없으면 입력으로 해제합니다.
 * 처리 구조: PORT는 LOW로 두고 DDR만 바꿔 HIGH를 직접 구동하지 않는 방식으로 결과가 ACK될 때까지 알립니다.
 */
static void signal_results() {
    /* Open-drain style: never drive HIGH into another board. */
    PORTD &= (uint8_t)~(1 << PD2);
    if (result_count) DDRD |= (1 << PD2);
    else DDRD &= (uint8_t)~(1 << PD2);
}
/**
 * 기능: 완전하게 수신한 TWI 명령을 Slave 상태에 반영합니다.
 * 입력: 수신 packet/count/valid 공유 상태.
 * 출력: 필터 설정, 결과 ACK·slot 해제 또는 새 Ready 작업 추가.
 * 처리 구조: 명령별 길이·작업 번호를 검사합니다. 중복·빈 slot을 확인한 뒤 input/filter snapshot과 부하를 저장하고 FIFO에 넣습니다.
 */
static void commit_receive() {
    if (!receive_valid) return;
    if (receive_packet[0] == lb::CmdFilter && receive_count == 10) {
        for (uint8_t i = 0; i < 9; ++i) broadcast_filter[i] = (int8_t)receive_packet[1 + i];
        return;
    }
    if (receive_packet[0] == lb::CmdAck && receive_count == 4 && result_count) {
        uint8_t slot = result_order[result_head];
        uint16_t sequence = (uint16_t)receive_packet[1] | ((uint16_t)receive_packet[2] << 8);
        if (slots[slot].sequence == sequence && slots[slot].patch == receive_packet[3]) {
            slots[slot].state = Free; result_head = (result_head + 1) % 2; --result_count;
            signal_results();
        }
        return;
    }
    if (receive_packet[0] != lb::CmdJob || receive_count != lb::JobBytes ||
        receive_packet[3] >= lb::Patches) return;
    uint16_t sequence = (uint16_t)receive_packet[1] | ((uint16_t)receive_packet[2] << 8);
    uint8_t patch_id = receive_packet[3];
    int8_t free_slot = -1;
    for (uint8_t i = 0; i < 2; ++i) {
        if (slots[i].state == Free) free_slot = (int8_t)i;
        else if (slots[i].sequence == sequence && slots[i].patch == patch_id) return;
    }
    if (free_slot < 0 || ready_count >= 2) return;
    int8_t input[9], filter[9];
    for (uint8_t i = 0; i < 9; ++i) {
        input[i] = (int8_t)receive_packet[4 + i]; filter[i] = (int8_t)receive_packet[13 + i];
        slots[free_slot].input[i] = input[i]; slots[free_slot].filter[i] = filter[i];
    }
    slots[free_slot].sequence = sequence; slots[free_slot].patch = patch_id;
    slots[free_slot].state = Ready;
    total_load += lb::operation_load(input, filter);
    ready_order[(ready_head + ready_count) % 2] = (uint8_t)free_slot; ++ready_count;
}
/**
 * 기능: Slave TWI의 수신·응답 상태를 하드웨어 status에 따라 처리합니다.
 * 입력: TWSR status와 TWDR 데이터.
 * 출력: RX 누적/명령 commit, TX snapshot과 다음 바이트, ACK 상태.
 * 처리 구조: SLA+W/general call→data→STOP은 수신 경로, SLA+R→전송 ACK/NACK는 결과 응답 경로로 분리합니다.
 */
ISR(TWI_vect) {
    uint8_t status = TWSR & 0xf8;
    switch (status) {
    case 0x60: case 0x70:
        receive_count = 0; receive_valid = true; break;
    case 0x80: case 0x90:
        if (receive_count < sizeof(receive_packet)) receive_packet[receive_count++] = TWDR;
        else { (void)TWDR; receive_valid = false; }
        break;
    case 0xa0:
        commit_receive(); break;
    case 0xa8:
        for (uint8_t i = 0; i < lb::ResultBytes; ++i) transmit_packet[i] = 0;
        tx_valid = result_count != 0;
        if (tx_valid) {
            uint8_t slot = result_order[result_head];
            uint8_t packet[lb::ResultBytes] = {lb::ResultHeader};
            lb::put16(packet + 1, slots[slot].sequence); packet[3] = slots[slot].patch;
            lb::put32(packet + 4, slots[slot].result);
            for (uint8_t i = 0; i < lb::ResultBytes; ++i) transmit_packet[i] = packet[i];
        }
        tx_index = 1; TWDR = transmit_packet[0]; break;
    case 0xb8:
        TWDR = tx_index < lb::ResultBytes ? transmit_packet[tx_index++] : 0; break;
    case 0xc0: case 0xc8:
        tx_valid = false; break; // Result stays until an explicit ACK packet.
    case 0x00:
        TWCR = (1 << TWINT) | (1 << TWSTO) | (1 << TWEN) | (1 << TWIE) | (1 << TWEA);
        return;
    default: break;
    }
    TWCR = (1 << TWINT) | (1 << TWEN) | (1 << TWIE) | (1 << TWEA);
}
/**
 * 기능: 합성곱 Slave의 TWI·시간 기준·부하 서보를 설정합니다.
 * 입력: SLAVE_ADDRESS macro: 자신의 I²C 주소 1~4.
 * 출력: Timer1 50 Hz PWM, Timer2 1 ms tick, 주소별 TWI ISR, 완료선 해제.
 * 처리 구조: GPIO→Timer1/2→USART→TWI 주소/general call 순으로 설정하고 interrupt를 허용합니다.
 */
void setup() {
    DDRD &= (uint8_t)~(1 << PD2); PORTD &= (uint8_t)~(1 << PD2);
    DDRB |= (1 << PB1);
    TCCR1A = (1 << WGM11) | (1 << COM1A1);
    TCCR1B = (1 << WGM13) | (1 << WGM12) | (1 << CS11) | (1 << CS10);
    ICR1 = 4999; OCR1A = lb::servo_ticks(0);
    TCCR2A = (1 << WGM21); TCCR2B = (1 << CS22); OCR2A = 249; TCNT2 = 0;
    TIMSK2 = (1 << OCIE2A);
    UBRR0H = 0; UBRR0L = 16; UCSR0A = (1 << U2X0);
    UCSR0B = (1 << TXEN0); UCSR0C = (1 << UCSZ01) | (1 << UCSZ00);
    TWAR = (SLAVE_ADDRESS << 1) | (1 << TWGCE);
    TWCR = (1 << TWEN) | (1 << TWIE) | (1 << TWEA);
    sei();
}
/**
 * 기능: Ready 작업을 하나씩 연산하고 결과를 ACK 대기 상태로 보관합니다.
 * 입력: Ready FIFO의 작업과 slot별 input/filter snapshot.
 * 출력: signed 합성곱 결과, Result FIFO·완료선·PWM 갱신.
 * 처리 구조: atomic으로 Ready→Processing 전환 후 zero-skip과 단계별 지연을 수행합니다. 완료 결과를 Result slot에 기록해 Master ACK까지 유지합니다.
 */
void loop() {
    int8_t slot = -1;
    ATOMIC_BLOCK(ATOMIC_RESTORESTATE) {
        if (ready_count) {
            slot = (int8_t)ready_order[ready_head]; ready_head = (ready_head + 1) % 2; --ready_count;
            slots[slot].state = Processing;
        }
    }
    if (slot < 0) { update_servo(); return; }
    int8_t input[9], filter[9];
    for (uint8_t i = 0; i < 9; ++i) { input[i] = slots[slot].input[i]; filter[i] = slots[slot].filter[i]; }
    int32_t sum = 0; bool first = true;
    for (uint8_t i = 0; i < 9; ++i) {
        if (!input[i] || !filter[i]) continue;
        int32_t product = (int32_t)input[i] * filter[i];
        wait_ms(DEMO_MUL_MS);
        ATOMIC_BLOCK(ATOMIC_RESTORESTATE) { total_load -= 2; }
        if (!first) {
            sum += product; wait_ms(DEMO_ADD_MS);
            ATOMIC_BLOCK(ATOMIC_RESTORESTATE) { --total_load; }
        } else { sum = product; first = false; }
        update_servo();
    }
    ATOMIC_BLOCK(ATOMIC_RESTORESTATE) {
        slots[slot].result = sum; slots[slot].state = Result;
        result_order[(result_head + result_count) % 2] = (uint8_t)slot; ++result_count;
        signal_results();
    }
    update_servo();
}
