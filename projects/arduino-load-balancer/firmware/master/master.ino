/*
 * 분산 합성곱 시스템의 Master Arduino 코드입니다.
 * PC의 5×5 입력·3×3 필터를 UART로 받아 9개 작업을 네 Slave에 나누고 결과를 모읍니다.
 * LoadBalancerCore가 배치·부하·큐를 관리하고 이 파일은 TWI, UART, 완료 interrupt를 실제 보드에 연결합니다.
 */

/* Report-derived register-level Arduino UNO scheduler, publication revision. */
#include <Arduino.h>
#include <avr/io.h>
#include <avr/interrupt.h>
#include <avr/pgmspace.h>
#include <util/atomic.h>
#include <LoadBalancerCore.h>

lb::Scheduler scheduler;
static volatile uint8_t rx_frame[38], rx_index;
static volatile bool rx_ready, button_event, done_event[lb::Workers];
static volatile uint8_t previous_pinb;
static volatile uint32_t last_button;
static bool started;
static const uint8_t slave_addresses[] = {1, 2, 3, 4};

/**
 * 기능: Master USART로 한 바이트를 전송합니다.
 * 입력: b: 전송 바이트.
 * 출력: UDR0에 바이트를 기록합니다.
 * 처리 구조: 송신 데이터 레지스터가 비었는지 UDRE0을 기다린 뒤 기록합니다.
 */
static void uart_byte(uint8_t b) {
    while (!(UCSR0A & (1 << UDRE0))) {}
    UDR0 = b;
}
/**
 * 기능: Flash에 보관한 로그 문자열을 UART로 보냅니다.
 * 입력: s: F()로 생성한 Flash 문자열.
 * 출력: 문자열을 UART로 순서대로 전송합니다.
 * 처리 구조: pgm_read_byte로 Flash를 읽어 RAM 문자열 복사 없이 출력합니다.
 */
static void uart_flash(const __FlashStringHelper *s) {
    const char *p = (const char *)s;
    char c;
    while ((c = (char)pgm_read_byte(p++)) != 0) uart_byte((uint8_t)c);
}
/**
 * 기능: signed 32비트 정수를 UART의 십진 문자열로 출력합니다.
 * 입력: n: 출력 정수.
 * 출력: 부호와 숫자 문자를 전송합니다.
 * 처리 구조: unsigned magnitude로 INT32_MIN도 처리하고 역순으로 모은 자릿수를 다시 정방향 출력합니다.
 */
static void uart_number(int32_t n) {
    uint32_t magnitude = (uint32_t)n;
    if (n < 0) { uart_byte('-'); magnitude = 0u - magnitude; }
    char digits[10]; uint8_t count = 0;
    do { digits[count++] = (char)('0' + magnitude % 10); magnitude /= 10; } while (magnitude);
    while (count) uart_byte(digits[--count]);
}
/**
 * 기능: PC의 UART frame을 수신하고 완성 여부를 알립니다.
 * 입력: UCSR0A의 오류 상태와 UDR0의 새 바이트.
 * 출력: rx_frame의 38바이트 입력, 완성 시 rx_ready=true.
 * 처리 구조: 고정 위치의 <, M, F, >를 확인해 payload를 모읍니다. 오류 시 수신 인덱스를 초기화하고 이미 완료된 frame은 loop가 가져갈 때까지 유지합니다.
 */
ISR(USART_RX_vect) {
    uint8_t status = UCSR0A, value = UDR0;
    if (status & ((1 << FE0) | (1 << DOR0) | (1 << UPE0))) { rx_index = 0; return; }
    if (rx_ready) return;
    if (!rx_index) {
        if (value == '<') { rx_frame[0] = value; rx_index = 1; }
        return;
    }
    if ((rx_index == 1 && value != 'M') || (rx_index == 27 && value != 'F') ||
        (rx_index == 37 && value != '>')) {
        rx_index = value == '<' ? 1 : 0;
        return;
    }
    rx_frame[rx_index++] = value;
    if (rx_index == sizeof(rx_frame)) { rx_ready = true; rx_index = 0; }
}
/**
 * 기능: 첫 번째 Slave의 완료 에지를 기록합니다.
 * 입력: Master D2의 falling edge.
 * 출력: done_event[0]=true.
 * 처리 구조: ISR에서는 플래그만 설정하고 결과 읽기·ACK는 collect가 처리합니다.
 */
ISR(INT0_vect) { done_event[0] = true; }
/**
 * 기능: 두 번째 Slave의 완료 에지를 기록합니다.
 * 입력: Master D3의 falling edge.
 * 출력: done_event[1]=true.
 * 처리 구조: ISR에서는 플래그만 설정해 TWI 처리가 interrupt 안에서 길어지지 않게 합니다.
 */
ISR(INT1_vect) { done_event[1] = true; }
/**
 * 기능: 시작 버튼과 세 번째·네 번째 Slave 완료선을 구분합니다.
 * 입력: PINB 현재값과 이전 snapshot.
 * 출력: 버튼 또는 done_event[2/3] 플래그.
 * 처리 구조: falling bit를 구분하고 버튼에만 10 ms 디바운스를 적용합니다.
 */
ISR(PCINT0_vect) {
    uint8_t now = PINB, falling = previous_pinb & (uint8_t)~now;
    previous_pinb = now;
    if (falling & (1 << PB1)) done_event[2] = true;
    if (falling & (1 << PB2)) done_event[3] = true;
    if (falling & (1 << PB0)) {
        uint32_t time = millis();
        if ((uint32_t)(time - last_button) >= 10) { button_event = true; last_button = time; }
    }
}

/**
 * 기능: TWI의 현재 전송 단계가 끝날 때까지 기다립니다.
 * 입력: start: 해당 단계 시작 millis().
 * 출력: 20 ms 안에 TWINT가 설정되면 true, timeout이면 false.
 * 처리 구조: TWINT를 polling하고 timeout이면 TWI를 재활성화해 호출자에게 실패를 알립니다.
 */
static bool twi_wait(uint32_t start) {
    while (!(TWCR & (1 << TWINT))) {
        if ((uint32_t)(millis() - start) >= 20) { TWCR = 0; TWCR = (1 << TWEN); return false; }
    }
    return true;
}
/**
 * 기능: I²C START와 Slave 주소 전송을 수행합니다.
 * 입력: address: 7비트 주소, read: true이면 읽기/false이면 쓰기.
 * 출력: START와 주소 ACK가 정상 상태 코드이면 true.
 * 처리 구조: TWSTA 설정 후 START 상태를 검사하고 SLA+R/W를 보내 해당 ACK 상태를 확인합니다.
 */
static bool twi_start(uint8_t address, bool read) {
    uint32_t start = millis();
    TWCR = (1 << TWINT) | (1 << TWSTA) | (1 << TWEN);
    if (!twi_wait(start)) return false;
    uint8_t state = TWSR & 0xf8;
    if (state != 0x08 && state != 0x10) return false;
    TWDR = (uint8_t)((address << 1) | (read ? 1 : 0));
    TWCR = (1 << TWINT) | (1 << TWEN);
    return twi_wait(start) && (TWSR & 0xf8) == (read ? 0x40 : 0x18);
}
/**
 * 기능: I²C STOP을 발생시키고 버스 종료를 확인합니다.
 * 입력: 없음. 현재 TWI 교환을 종료합니다.
 * 출력: STOP 완료이면 true, 20 ms timeout이면 false.
 * 처리 구조: TWSTO를 설정한 뒤 하드웨어가 bit를 해제하는지 기다립니다.
 */
static bool twi_stop() {
    uint32_t start = millis();
    TWCR = (1 << TWINT) | (1 << TWEN) | (1 << TWSTO);
    while (TWCR & (1 << TWSTO)) {
        if ((uint32_t)(millis() - start) >= 20) {
            TWCR = 0; TWCR = (1 << TWEN); return false;
        }
    }
    return true;
}
/**
 * 기능: 지정 Slave 또는 general call 주소로 프레임을 보냅니다.
 * 입력: address: 대상, data: TX 배열, count: 전송 바이트 수.
 * 출력: 주소·모든 data ACK·STOP이 성공하면 true.
 * 처리 구조: SLA+W→각 바이트 전송/상태 확인→STOP 순서로 수행합니다.
 */
static bool twi_write(uint8_t address, const uint8_t *data, uint8_t count) {
    bool ok = twi_start(address, false);
    for (uint8_t i = 0; ok && i < count; ++i) {
        TWDR = data[i]; TWCR = (1 << TWINT) | (1 << TWEN);
        ok = twi_wait(millis()) && (TWSR & 0xf8) == 0x28;
    }
    return twi_stop() && ok;
}
/**
 * 기능: 지정 Slave에서 고정 길이 프레임을 읽습니다.
 * 입력: address: 대상, data: RX 공간, count: 읽을 바이트 수.
 * 출력: 각 단계와 STOP이 정상이면 true와 수신 배열.
 * 처리 구조: SLA+R 후 마지막 이전은 ACK, 마지막은 NACK으로 읽고 상태 코드를 확인합니다.
 */
static bool twi_read(uint8_t address, uint8_t *data, uint8_t count) {
    bool ok = twi_start(address, true);
    for (uint8_t i = 0; ok && i < count; ++i) {
        bool more = i + 1 < count;
        TWCR = (1 << TWINT) | (1 << TWEN) | (more ? (1 << TWEA) : 0);
        ok = twi_wait(millis()) && (TWSR & 0xf8) == (more ? 0x50 : 0x58);
        if (ok) data[i] = TWDR;
    }
    return twi_stop() && ok;
}
/**
 * 기능: 해당 Worker의 전송 가능한 작업을 실제 TWI 프레임으로 보냅니다.
 * 입력: worker: Slave 번호.
 * 출력: 성공한 작업은 queue에서 pending으로 옮깁니다.
 * 처리 구조: credit를 확인하고 배치 input/filter와 식별자를 22바이트에 넣어 전송합니다. 실패 시 queue를 유지합니다.
 */
static void dispatch(uint8_t worker) {
    lb::Job job;
    while (scheduler.next_job(worker, job)) {
        const lb::Batch &batch = scheduler.batches[job.slot];
        uint8_t packet[lb::JobBytes]; int8_t patch[9];
        packet[0] = lb::CmdJob; lb::put16(packet + 1, batch.sequence); packet[3] = job.patch;
        lb::extract_patch(batch.input, job.patch, patch);
        memcpy(packet + 4, patch, 9); memcpy(packet + 13, batch.filter, 9);
        if (!twi_write(slave_addresses[worker], packet, sizeof(packet))) return;
        scheduler.mark_sent(worker); // Commit only after the full write succeeds.
    }
}
/**
 * 기능: 완료된 3×3 결과를 호스트에 출력하고 다음 입력을 요청합니다.
 * 입력: slot: 9개 결과가 모인 배치.
 * 출력: RESULT sequence와 9개 값, RDY_FOR_NEXT 문자열.
 * 처리 구조: 결과를 행 우선 순서로 출력한 뒤 배치 slot을 release해 새 frame용 credit를 제공합니다.
 */
static void print_completed(uint8_t slot) {
    const lb::Batch &b = scheduler.batches[slot];
    uart_flash(F("RESULT ")); uart_number(b.sequence);
    for (uint8_t i = 0; i < 9; ++i) { uart_byte(' '); uart_number(b.result[i]); }
    uart_flash(F("\r\n"));
    scheduler.release(slot);
    uart_flash(F("RDY_FOR_NEXT\r\n"));
}
/**
 * 기능: Slave의 완료 결과를 읽고 ACK 후 스케줄러에 반영합니다.
 * 입력: worker: 결과를 확인할 Slave 번호.
 * 출력: 유효 결과를 배치에 저장하고 완료된 배치는 호스트에 출력합니다.
 * 처리 구조: 최대 2개 결과를 읽어 헤더·작업 소속을 확인하고 명시적 ACK를 보냅니다. 정상 결과만 complete로 반영합니다.
 */
static void collect(uint8_t worker) {
    for (uint8_t attempt = 0; attempt < lb::Credits; ++attempt) {
        uint8_t response[lb::ResultBytes];
        if (!twi_read(slave_addresses[worker], response, sizeof(response)) ||
            response[0] != lb::ResultHeader) return;
        uint16_t sequence = lb::get16(response + 1); uint8_t patch = response[3];
        uint8_t ack[] = {lb::CmdAck, response[1], response[2], patch};
        bool expected = scheduler.expected(worker, sequence, patch);
        if (!twi_write(slave_addresses[worker], ack, sizeof(ack))) return;
        if (!expected) { uart_flash(F("IGNORED_STALE_RESULT\r\n")); continue; }
        int8_t slot = scheduler.complete(worker, sequence, patch, lb::get32(response + 4));
        if (slot >= 0 && scheduler.batches[slot].completed == lb::Patches) print_completed((uint8_t)slot);
    }
}
/**
 * 기능: 합성곱 Master의 시작 상태와 주변장치를 설정합니다.
 * 입력: 없음. Arduino core가 부팅 시 한 번 호출합니다.
 * 출력: 스케줄러·115200 UART·100 kHz TWI·완료선·시작 버튼과 ARDUINO_READY 출력.
 * 처리 구조: 메모리 상태 초기화→UART/TWI→GPIO/interrupt 설정→전역 interrupt 허용 순으로 시작합니다.
 */
void setup() {
    scheduler.init();
    UBRR0H = 0; UBRR0L = 16; UCSR0A = (1 << U2X0);
    UCSR0B = (1 << RXEN0) | (1 << TXEN0) | (1 << RXCIE0);
    UCSR0C = (1 << UCSZ01) | (1 << UCSZ00);
    TWSR = 0; TWBR = (uint8_t)((F_CPU / 100000UL - 16) / 2); TWCR = (1 << TWEN);
    DDRD &= (uint8_t)~((1 << PD2) | (1 << PD3)); PORTD |= (1 << PD2) | (1 << PD3);
    EICRA = (1 << ISC01) | (1 << ISC11); EIFR = (1 << INTF0) | (1 << INTF1);
    EIMSK = (1 << INT0) | (1 << INT1);
    DDRB &= (uint8_t)~((1 << PB0) | (1 << PB1) | (1 << PB2));
    PORTB |= (1 << PB0) | (1 << PB1) | (1 << PB2);
    previous_pinb = PINB; PCMSK0 = (1 << PCINT0) | (1 << PCINT1) | (1 << PCINT2);
    PCIFR = (1 << PCIF0); PCICR = (1 << PCIE0);
    sei(); uart_flash(F("ARDUINO_READY\r\n"));
}
/**
 * 기능: 호스트 입력, 시작 버튼, Slave 결과 수집과 작업 전송을 연결합니다.
 * 입력: UART 완료·버튼·Slave ISR flag와 스케줄러 상태.
 * 출력: 수신 배치 배정, RESULT 출력, 후속 job 전송.
 * 처리 구조: 완전한 입력을 accept하고 버튼으로 시작합니다. 완료 이벤트 또는 10 ms polling으로 결과를 모으며 Worker별 credit에 맞춰 dispatch합니다.
 */
void loop() {
    if (rx_ready) {
        int8_t input[25], filter[9];
        ATOMIC_BLOCK(ATOMIC_RESTORESTATE) {
            for (uint8_t i = 0; i < 25; ++i) input[i] = (int8_t)rx_frame[2 + i];
            for (uint8_t i = 0; i < 9; ++i) filter[i] = (int8_t)rx_frame[28 + i];
            rx_ready = false;
        }
        uint16_t sequence;
        if (scheduler.accept(input, filter, sequence)) {
            uart_flash(F("ACCEPTED ")); uart_number(sequence); uart_flash(F("\r\n"));
            if (scheduler.free_slot() >= 0) uart_flash(F("RDY_FOR_NEXT\r\n"));
        } else uart_flash(F("BUSY\r\n"));
    }
    bool pressed = false;
    ATOMIC_BLOCK(ATOMIC_RESTORESTATE) { pressed = button_event; button_event = false; }
    if (pressed && !started) {
        uint8_t filter_packet[10] = {lb::CmdFilter};
        for (uint8_t s = 0; s < 2; ++s) if (scheduler.batches[s].active) {
            memcpy(filter_packet + 1, scheduler.batches[s].filter, 9); break;
        }
        if (scheduler.batches[0].active || scheduler.batches[1].active) {
            if (twi_write(0, filter_packet, sizeof(filter_packet))) started = true;
        }
    }
    if (!started) return;
    static uint32_t last_poll;
    bool periodic = (uint32_t)(millis() - last_poll) >= 10;
    if (periodic) last_poll = millis();
    for (uint8_t worker = 0; worker < lb::Workers; ++worker) {
        bool event;
        ATOMIC_BLOCK(ATOMIC_RESTORESTATE) { event = done_event[worker]; done_event[worker] = false; }
        if ((event || periodic) && scheduler.workers[worker].pending_count) collect(worker);
        dispatch(worker);
    }
}
