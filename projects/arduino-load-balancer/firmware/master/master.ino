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

static void uart_byte(uint8_t b) {
    while (!(UCSR0A & (1 << UDRE0))) {}
    UDR0 = b;
}
static void uart_flash(const __FlashStringHelper *s) {
    const char *p = (const char *)s;
    char c;
    while ((c = (char)pgm_read_byte(p++)) != 0) uart_byte((uint8_t)c);
}
static void uart_number(int32_t n) {
    uint32_t magnitude = (uint32_t)n;
    if (n < 0) { uart_byte('-'); magnitude = 0u - magnitude; }
    char digits[10]; uint8_t count = 0;
    do { digits[count++] = (char)('0' + magnitude % 10); magnitude /= 10; } while (magnitude);
    while (count) uart_byte(digits[--count]);
}
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
ISR(INT0_vect) { done_event[0] = true; }
ISR(INT1_vect) { done_event[1] = true; }
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

static bool twi_wait(uint32_t start) {
    while (!(TWCR & (1 << TWINT))) {
        if ((uint32_t)(millis() - start) >= 20) { TWCR = 0; TWCR = (1 << TWEN); return false; }
    }
    return true;
}
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
static bool twi_write(uint8_t address, const uint8_t *data, uint8_t count) {
    bool ok = twi_start(address, false);
    for (uint8_t i = 0; ok && i < count; ++i) {
        TWDR = data[i]; TWCR = (1 << TWINT) | (1 << TWEN);
        ok = twi_wait(millis()) && (TWSR & 0xf8) == 0x28;
    }
    return twi_stop() && ok;
}
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
static void print_completed(uint8_t slot) {
    const lb::Batch &b = scheduler.batches[slot];
    uart_flash(F("RESULT ")); uart_number(b.sequence);
    for (uint8_t i = 0; i < 9; ++i) { uart_byte(' '); uart_number(b.result[i]); }
    uart_flash(F("\r\n"));
    scheduler.release(slot);
    uart_flash(F("RDY_FOR_NEXT\r\n"));
}
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
