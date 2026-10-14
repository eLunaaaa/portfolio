/*
 * 쓰레기통 네 종류의 적재량을 센싱하고 ESP8266으로 서버에 보내는 AVR 프로그램입니다.
 * USART ISR은 응답을 원형 버퍼에 저장하고 Timer1은 거리·timeout의 기준 시각을 제공합니다.
 * main은 거리 히스테리시스, HTTP 성공 확인, 상태 재전송·재연결을 묶어 실행합니다.
 */

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
/**
 * 기능: ESP8266 응답 바이트를 RX 원형 버퍼에 저장합니다.
 * 입력: UDR0의 새 응답 바이트와 rx_head/rx_tail 위치.
 * 출력: rx_buffer·head 갱신 또는 rx_overflow=1.
 * 처리 구조: 다음 head가 tail과 같으면 포화 상태만 남기고, 공간이 있으면 바이트를 저장해 메인 응답 parser에 전달합니다.
 */
ISR(USART_RX_vect) {
    char c = UDR0;
    uint8_t next = (uint8_t)((rx_head + 1u) % RX_BUF_SIZE);
    if (next == rx_tail) rx_overflow = 1;
    else { rx_buffer[rx_head] = c; rx_head = next; }
}
/**
 * 기능: 적재량 보드의 Timer1 overflow를 시간 누적값에 반영합니다.
 * 입력: 16비트 Timer1 overflow interrupt.
 * 출력: timer_us_base를 32768 us 증가시킵니다.
 * 처리 구조: prescaler 8의 0.5 us tick 65536개를 us로 환산해 now_us와 결합합니다.
 */
ISR(TIMER1_OVF_vect) { timer_us_base += 32768UL; }
/**
 * 기능: Arduino 적재량 보드의 Timer1 시간을 microsecond 단위로 읽습니다.
 * 입력: 없음. overflow 누적값과 TCNT1을 사용합니다.
 * 출력: unsigned 32비트 us 시각.
 * 처리 구조: atomic 구간에서 누적값·카운터·보류 중인 overflow를 읽고 0.5 us tick을 us로 변환합니다.
 */
static uint32_t now_us(void) {
    uint32_t base; uint16_t count;
    ATOMIC_BLOCK(ATOMIC_RESTORESTATE) {
        base = timer_us_base; count = TCNT1;
        if ((TIFR1 & (1 << TOV1)) && count < 65535u) base += 32768UL;
    }
    return base + count / 2u; /* 16 MHz / 8 = 2 MHz */
}
/**
 * 기능: 적재량 측정·통신 timeout의 Timer1 기준을 설정합니다.
 * 입력: 없음. F_CPU=16 MHz를 기준으로 합니다.
 * 출력: Timer1 2 MHz 카운터와 overflow interrupt를 활성화합니다.
 * 처리 구조: prescaler 8, counter 0, overflow flag 정리 후 interrupt enable을 설정합니다.
 */
static void timer_init(void) {
    TCCR1A = 0; TCCR1B = (1 << CS11); TCNT1 = 0;
    TIFR1 = (1 << TOV1); TIMSK1 = (1 << TOIE1);
}
/**
 * 기능: ESP8266 통신용 하드웨어 USART를 설정합니다.
 * 입력: ubrr: 보레이트 분주값.
 * 출력: 8N1 RX/TX와 수신 interrupt를 활성화합니다.
 * 처리 구조: 분주 레지스터·UCSR0B/C를 설정하고 전역 interrupt를 허용합니다.
 */
static void UART_Init(unsigned int ubrr) {
    UBRR0H = (uint8_t)(ubrr >> 8); UBRR0L = (uint8_t)ubrr;
    UCSR0B = (1 << RXEN0) | (1 << TXEN0) | (1 << RXCIE0);
    UCSR0C = (1 << UCSZ01) | (1 << UCSZ00);
    sei();
}
/**
 * 기능: ESP8266에 AT 명령 또는 HTTP 요청 문자열을 보냅니다.
 * 입력: str: NUL 종료 문자열.
 * 출력: 전체 전송이면 1, TX 공간 대기 timeout이면 0.
 * 처리 구조: 문자마다 UDRE를 확인해 UDR0에 기록하고 100 ms의 바이트 대기 제한을 적용합니다.
 */
static uint8_t UART_SendString(const char *str) {
    while (*str) {
        uint32_t start = now_us();
        while (!(UCSR0A & (1 << UDRE0)))
            if ((uint32_t)(now_us() - start) >= 100000UL) return 0;
        UDR0 = *str++;
    }
    return 1;
}
/**
 * 기능: 다음 AT 명령의 응답을 읽기 전에 수신 상태를 비웁니다.
 * 입력: 없음. USART RX 원형 버퍼를 사용합니다.
 * 출력: tail을 head로 옮기고 overflow 플래그를 초기화합니다.
 * 처리 구조: head/tail 변경을 atomic으로 처리해 수신 ISR과 충돌하지 않게 합니다.
 */
static void ESP_ClearBuffer(void) {
    ATOMIC_BLOCK(ATOMIC_RESTORESTATE) { rx_tail = rx_head; rx_overflow = 0; }
}
/**
 * 기능: 원형 버퍼의 수신 문자를 읽어 AT/HTTP 응답을 기다립니다.
 * 입력: target: 찾을 응답 문자열 또는 HTTP_SUCCESS, timeout_ms: 제한 시간.
 * 출력: 목표 응답을 찾으면 1, timeout·ERROR/FAIL·수신 overflow이면 0.
 * 처리 구조: 고정 길이 sliding 문자열 창을 유지합니다. HTTP_SUCCESS는 HTTP/1.0 또는 1.1의 200 상태를 확인합니다.
 */
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

/**
 * 기능: 네 종류 쓰레기통의 초음파 trigger/echo 핀을 연결합니다.
 * 입력: 없음. bins 배열과 AVR port 레지스터를 사용합니다.
 * 출력: 종류별 pin 포인터·bit·상태와 입출력 방향을 설정합니다.
 * 처리 구조: 일반/캔/유리/PET의 구조체를 채우고 trigger를 output, echo를 input으로 지정합니다.
 */
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

/**
 * 기능: 선택한 쓰레기통의 초음파 센서에서 거리를 읽습니다.
 * 입력: idx: bins 배열 번호(0~3).
 * 출력: cm 단위 정수 거리, echo timeout이면 -1.
 * 처리 구조: trigger 펄스를 만든 뒤 Timer1 us 시각으로 echo 길이를 구하고 58로 나눕니다.
 */
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

/**
 * 기능: 쓰레기통 상태를 ESP8266 TCP 연결로 서버에 보고합니다.
 * 입력: zone: 위치 ID, action: fill/empty, type: ge/ca/gl/pe.
 * 출력: HTTP 200 응답이면 1, 구성·연결·전송·응답 실패이면 0.
 * 처리 구조: CIPSTART→CIPSEND→GET 요청→HTTP 상태 확인→연결 종료 순서입니다. 문자열 공간 크기를 검사하며 실패한 상태는 확정하지 않습니다.
 */
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

/**
 * 기능: AT 명령 전송과 기대 응답 확인을 한 번에 수행합니다.
 * 입력: cmd: AT 문자열, response: 기대 응답, ms: 제한 시간.
 * 출력: 송신과 응답 확인 모두 성공하면 1.
 * 처리 구조: 이전 응답을 비운 뒤 UART_SendString과 UART_WaitResponse를 연달아 호출합니다.
 */
static uint8_t esp_command(const char *cmd, const char *response, uint32_t ms) {
    ESP_ClearBuffer();
    return UART_SendString(cmd) && UART_WaitResponse(response, ms);
}
/**
 * 기능: ESP8266을 초기화하고 지정 Wi-Fi에 연결합니다.
 * 입력: 없음. secrets.h의 SSID·비밀번호를 사용합니다.
 * 출력: 각 설정·연결 단계가 성공하면 1, 실패하면 0.
 * 처리 구조: reset→echo off→station mode→단일 연결/normal mode→AP join 순으로 기대 응답을 확인합니다.
 */
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
/**
 * 기능: 적재량 보드의 센싱과 서버 보고 루프를 실행합니다.
 * 입력: 로컬 Wi-Fi 설정, 네 초음파 센서의 echo.
 * 출력: HTTP 상태 보고와 서버 반영 성공 상태.
 * 처리 구조: 타이머/UART/GPIO 초기화 후 Wi-Fi를 연결합니다. 2/4 cm 히스테리시스로 상태를 판정해 변경·최초·주기 보고를 수행하고 실패 시 재연결합니다.
 */
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
