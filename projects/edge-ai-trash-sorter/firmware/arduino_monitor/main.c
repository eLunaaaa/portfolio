/* ATmega328P standalone AVR firmware. Timer1 supplies elapsed time. */
#ifndef F_CPU
#define F_CPU 16000000UL
#endif
#include <avr/io.h>
#include <avr/interrupt.h>
#include <util/delay.h>
#include <util/atomic.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include "secrets.h"
#define BAUD 9600UL
#define MYUBRR (F_CPU / 16UL / BAUD - 1UL)
#define RX_BUF_SIZE 128
#define FULL_ON_CM 2L
#define FULL_OFF_CM 4L

typedef struct {
    char type[3];
    volatile uint8_t *port;
    volatile uint8_t *ddr;
    volatile uint8_t *pin;
    uint8_t trig_bit;
    uint8_t echo_bit;
    uint8_t isFull;
    uint8_t reported;
} TrashBin;

TrashBin bins[4];


static volatile char rx_buffer[RX_BUF_SIZE];
static volatile uint8_t rx_head, rx_tail, rx_overflow;
static volatile uint32_t timer_us_base;
ISR(USART_RX_vect) {
    char c = UDR0;
    uint8_t next = (uint8_t)((rx_head + 1u) % RX_BUF_SIZE);
    if (next == rx_tail) rx_overflow = 1;
    else { rx_buffer[rx_head] = c; rx_head = next; }
}
ISR(TIMER1_OVF_vect) { timer_us_base += 32768UL; }
static uint32_t now_us(void) {
    uint32_t base; uint16_t count;
    ATOMIC_BLOCK(ATOMIC_RESTORESTATE) {
        base = timer_us_base; count = TCNT1;
        if ((TIFR1 & (1 << TOV1)) && count < 65535u) base += 32768UL;
    }
    return base + count / 2u; /* 16 MHz / 8 = 2 MHz */
}
static void timer_init(void) {
    TCCR1A = 0; TCCR1B = (1 << CS11); TCNT1 = 0;
    TIFR1 = (1 << TOV1); TIMSK1 = (1 << TOIE1);
}
static void UART_Init(unsigned int ubrr) {
    UBRR0H = (uint8_t)(ubrr >> 8); UBRR0L = (uint8_t)ubrr;
    UCSR0B = (1 << RXEN0) | (1 << TXEN0) | (1 << RXCIE0);
    UCSR0C = (1 << UCSZ01) | (1 << UCSZ00);
    sei();
}
static uint8_t UART_SendString(const char *str) {
    while (*str) {
        uint32_t start = now_us();
        while (!(UCSR0A & (1 << UDRE0)))
            if ((uint32_t)(now_us() - start) >= 100000UL) return 0;
        UDR0 = *str++;
    }
    return 1;
}
static void ESP_ClearBuffer(void) {
    ATOMIC_BLOCK(ATOMIC_RESTORESTATE) { rx_tail = rx_head; rx_overflow = 0; }
}
static uint8_t UART_WaitResponse(const char *target, uint32_t timeout_ms) {
    char window[80] = {0};
    size_t used = 0;
    uint32_t start = now_us();
    while ((uint32_t)(now_us() - start) < timeout_ms * 1000UL) {
        if (rx_overflow) return 0;
        if (rx_head == rx_tail) continue;
        char c = rx_buffer[rx_tail];
        rx_tail = (uint8_t)((rx_tail + 1u) % RX_BUF_SIZE);
        if (used == sizeof(window) - 1) {
            memmove(window, window + 1, used - 1); --used;
        }
        window[used++] = c; window[used] = '\0';
        if (strcmp(target, "HTTP_SUCCESS") == 0) {
            if (strstr(window, "HTTP/1.1 200 ") || strstr(window, "HTTP/1.0 200 ")) return 1;
        } else if (strstr(window, target)) return 1;
        if (strstr(window, "ERROR") || strstr(window, "FAIL")) return 0;
    }
    return 0;
}

void GPIO_Init() {
    // 순서: 일반(ge) -> 캔(ca) -> 유리(gl) -> 페트(pe)
    bins[0] = (TrashBin){"ge", &PORTD, &DDRD, &PIND, 4, 5, 0, 0};
    *bins[0].ddr |= (1 << bins[0].trig_bit); *bins[0].ddr &= ~(1 << bins[0].echo_bit);

    bins[1] = (TrashBin){"ca", &PORTD, &DDRD, &PIND, 6, 7, 0, 0};
    *bins[1].ddr |= (1 << bins[1].trig_bit); *bins[1].ddr &= ~(1 << bins[1].echo_bit);

    bins[2] = (TrashBin){"gl", &PORTB, &DDRB, &PINB, 0, 1, 0, 0};
    *bins[2].ddr |= (1 << bins[2].trig_bit); *bins[2].ddr &= ~(1 << bins[2].echo_bit);

    bins[3] = (TrashBin){"pe", &PORTB, &DDRB, &PINB, 2, 3, 0, 0};
    *bins[3].ddr |= (1 << bins[3].trig_bit); *bins[3].ddr &= ~(1 << bins[3].echo_bit);
}

static long Measure_Distance(int idx) {
    *bins[idx].port &= ~(1 << bins[idx].trig_bit); _delay_us(2);
    *bins[idx].port |= (1 << bins[idx].trig_bit); _delay_us(10);
    *bins[idx].port &= ~(1 << bins[idx].trig_bit);
    uint32_t start = now_us();
    while (!(*bins[idx].pin & (1 << bins[idx].echo_bit)))
        if ((uint32_t)(now_us() - start) >= 25000UL) return -1;
    uint32_t rise = now_us();
    while (*bins[idx].pin & (1 << bins[idx].echo_bit))
        if ((uint32_t)(now_us() - rise) >= 25000UL) return -1;
    return (long)((uint32_t)(now_us() - rise) / 58UL);
}

static uint8_t Send_HTTP_Request(const char *zone, const char *action, const char *type) {
    char cmd[80], req[160];
    int n = snprintf(cmd, sizeof(cmd), "AT+CIPSTART=\"TCP\",\"%s\",%s\r\n", SERVER_IP, SERVER_PORT);
    if (n < 0 || (size_t)n >= sizeof(cmd)) return 0;
    ESP_ClearBuffer();
    if (!UART_SendString(cmd) || !UART_WaitResponse("OK", 3000)) return 0;
    n = snprintf(req, sizeof(req),
        "GET /%s/%s/%s HTTP/1.1\r\nHost: %s:%s\r\nConnection: close\r\n\r\n",
        zone, action, type, SERVER_IP, SERVER_PORT);
    if (n < 0 || (size_t)n >= sizeof(req)) return 0;
    n = snprintf(cmd, sizeof(cmd), "AT+CIPSEND=%u\r\n", (unsigned)strlen(req));
    if (n < 0 || (size_t)n >= sizeof(cmd)) return 0;
    ESP_ClearBuffer();
    if (!UART_SendString(cmd) || !UART_WaitResponse(">", 1000)) return 0;
    if (!UART_SendString(req)) return 0;
    /* SEND OK only confirms TCP transmission. Require HTTP success too. */
    uint8_t ok = UART_WaitResponse("HTTP_SUCCESS", 5000);
    UART_WaitResponse("CLOSED", 1000);
    if (!ok) {
        UART_SendString("AT+CIPCLOSE\r\n");
        UART_WaitResponse("OK", 1000);
    }
    return ok;
}

static uint8_t esp_command(const char *cmd, const char *response, uint32_t ms) {
    ESP_ClearBuffer();
    return UART_SendString(cmd) && UART_WaitResponse(response, ms);
}
static uint8_t connect_wifi(void) {
    if (!esp_command("AT+RST\r\n", "ready", 5000) ||
        !esp_command("ATE0\r\n", "OK", 1000) ||
        !esp_command("AT+CWMODE=1\r\n", "OK", 1000) ||
        !esp_command("AT+CIPMUX=0\r\n", "OK", 1000) ||
        !esp_command("AT+CIPMODE=0\r\n", "OK", 1000)) return 0;
    char cmd[160];
    int n = snprintf(cmd, sizeof(cmd), "AT+CWJAP=\"%s\",\"%s\"\r\n", WIFI_SSID, WIFI_PASS);
    return n > 0 && (size_t)n < sizeof(cmd) && esp_command(cmd, "OK", 15000);
}
int main(void) {
    timer_init(); UART_Init(MYUBRR); GPIO_Init();
    while (!connect_wifi()) _delay_ms(2000);
    uint32_t last_refresh = now_us();
    uint8_t failed_cycles = 0;
    while (1) {
        uint8_t cycle_failed = 0;
        uint8_t refresh = (uint32_t)(now_us() - last_refresh) >= 30000000UL;
        if (refresh) last_refresh = now_us();
        for (int i = 0; i < 4; ++i) {
            long dist = Measure_Distance(i);
            if (dist >= 0 && dist < 400) {
                uint8_t full = bins[i].isFull;
                if (dist <= FULL_ON_CM) full = 1;
                else if (dist >= FULL_OFF_CM) full = 0;
                if (!bins[i].reported || full != bins[i].isFull || refresh) {
                    if (Send_HTTP_Request(ZONE_ID, full ? "fill" : "empty", bins[i].type)) {
                        bins[i].isFull = full;
                        bins[i].reported = 1;
                    } else cycle_failed = 1;
                }
            }
            _delay_ms(100);
        }
        if (cycle_failed) {
            if (++failed_cycles >= 3) {
                while (!connect_wifi()) _delay_ms(2000);
                for (int i = 0; i < 4; ++i) bins[i].reported = 0;
                failed_cycles = 0;
            }
        } else failed_cycles = 0;
        _delay_ms(1000);
    }
}
