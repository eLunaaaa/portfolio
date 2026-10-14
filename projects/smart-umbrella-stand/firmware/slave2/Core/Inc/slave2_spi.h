/*
 * Slave2 SPI 상태 머신의 저장 구조와 이벤트 함수 선언입니다.
 * 날씨·표시 상태, 4바이트 TX/RX와 현재 index를 한 Slave2Spi 인스턴스에 담습니다.
 */

#ifndef SMART_UMBRELLA_SLAVE2_SPI_H
#define SMART_UMBRELLA_SLAVE2_SPI_H

#include <stdint.h>
#include "protocol.h"

typedef struct {
    uint8_t tx[WEATHER_FRAME_SIZE];
    uint8_t rx[WEATHER_FRAME_SIZE];
    uint8_t index;
    uint8_t rain_probability;
    PrecipitationType precipitation;
    uint8_t servo_level;
    bool led_water_full;
    bool led_rainy;
} Slave2Spi;

/**
 * 기능: Slave2의 SPI 응답 상태를 초기화합니다.
 * 입력: state: Slave2Spi 저장 공간.
 * 출력: 수신·송신 배열, 인덱스와 날씨·표시 상태를 0으로 초기화합니다.
 * 처리 구조: memset으로 첫 NSS 선택을 받을 준비를 합니다.
 */
void slave2_spi_init(Slave2Spi *state);
/**
 * 기능: 새 NSS 선택 시 날씨 응답 프레임을 준비합니다.
 * 입력: state: 현재 날씨와 이전 SPI 상태.
 * 출력: 첫 송신 바이트 0xC7을 반환하고 state의 프레임·인덱스를 갱신합니다.
 * 처리 구조: RX를 비우고 현재 강수확률·강수형태의 TX snapshot을 만듭니다. 반환값은 첫 SCK 전에 SPI DR에 적재합니다.
 */
uint8_t slave2_spi_on_select(Slave2Spi *state);
/**
 * 기능: SPI 수신 바이트를 누적하고 다음 송신 바이트를 제공합니다.
 * 입력: state: 진행 중인 프레임, received: 방금 받은 한 바이트.
 * 출력: 다음 TX 바이트를 반환하며 완전한 명령의 서보·LED 상태를 갱신합니다.
 * 처리 구조: 4바이트와 명령 헤더가 맞을 때만 표시 명령을 반영합니다. 프레임 종료 뒤 추가 바이트에는 0xFF를 반환합니다.
 */
uint8_t slave2_spi_on_byte(Slave2Spi *state, uint8_t received);

#endif
