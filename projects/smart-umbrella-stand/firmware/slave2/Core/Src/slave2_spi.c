/*
 * 스마트 우산꽂이 Slave2의 SPI 응답 상태 머신입니다.
 * NSS 선택 시 날씨 snapshot을 만들고 바이트마다 다음 TX 값을 제공합니다.
 * 완전한 명령 frame에서만 다이얼 서보·LED 상태를 갱신하며 실제 ISR은 이 반환값을 SPI DR에 적재합니다.
 */

#include <string.h>
#include "slave2_spi.h"

/**
 * 기능: Slave2의 SPI 응답 상태를 초기화합니다.
 * 입력: state: Slave2Spi 저장 공간.
 * 출력: 수신·송신 배열, 인덱스와 날씨·표시 상태를 0으로 초기화합니다.
 * 처리 구조: memset으로 첫 NSS 선택을 받을 준비를 합니다.
 */
void slave2_spi_init(Slave2Spi *state)
{
    memset(state, 0, sizeof(*state));
}

/**
 * 기능: 새 NSS 선택 시 날씨 응답 프레임을 준비합니다.
 * 입력: state: 현재 날씨와 이전 SPI 상태.
 * 출력: 첫 송신 바이트 0xC7을 반환하고 state의 프레임·인덱스를 갱신합니다.
 * 처리 구조: RX를 비우고 현재 강수확률·강수형태의 TX snapshot을 만듭니다. 반환값은 첫 SCK 전에 SPI DR에 적재합니다.
 */
uint8_t slave2_spi_on_select(Slave2Spi *state)
{
    state->index = 0u;
    memset(state->rx, 0, sizeof(state->rx));
    state->tx[0] = WEATHER_RSP_HEADER;
    state->tx[1] = state->rain_probability <= 100u ? state->rain_probability : 100u;
    state->tx[2] = (uint8_t)state->precipitation;
    state->tx[3] = 0u;
    return state->tx[0]; /* Write this to SPI->DR before the first SCK edge. */
}

/**
 * 기능: SPI 수신 바이트를 누적하고 다음 송신 바이트를 제공합니다.
 * 입력: state: 진행 중인 프레임, received: 방금 받은 한 바이트.
 * 출력: 다음 TX 바이트를 반환하며 완전한 명령의 서보·LED 상태를 갱신합니다.
 * 처리 구조: 4바이트와 명령 헤더가 맞을 때만 표시 명령을 반영합니다. 프레임 종료 뒤 추가 바이트에는 0xFF를 반환합니다.
 */
uint8_t slave2_spi_on_byte(Slave2Spi *state, uint8_t received)
{
    const uint8_t i = state->index;
    if (i >= WEATHER_FRAME_SIZE) return 0xffu;
    if (i < WEATHER_FRAME_SIZE) state->rx[i] = received;
    if (state->index < WEATHER_FRAME_SIZE) ++state->index;
    if (state->index == WEATHER_FRAME_SIZE && state->rx[0] == WEATHER_CMD_HEADER) {
        state->servo_level = state->rx[1] <= 4u ? state->rx[1] : 0u;
        state->led_water_full = state->rx[2] != 0u;
        state->led_rainy = state->rx[3] != 0u;
    }
    return state->index < WEATHER_FRAME_SIZE ? state->tx[state->index] : 0xffu;
}
