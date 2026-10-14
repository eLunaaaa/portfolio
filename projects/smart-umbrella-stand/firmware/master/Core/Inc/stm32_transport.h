/*
 * Master가 호출할 SPI1 DMA·SPI2 polling 교환 함수 선언입니다.
 * 레지스터 접근 구현은 stm32_transport.c에 있으며 상위 제어 코드에는 성공/실패와 수신 데이터만 제공합니다.
 */

#ifndef SMART_UMBRELLA_STM32_TRANSPORT_H
#define SMART_UMBRELLA_STM32_TRANSPORT_H

#include <stdbool.h>
#include <stdint.h>
#include "protocol.h"

/**
 * 기능: Master와 Slave1 사이에서 제어 명령과 센서값을 동시에 교환합니다.
 * 입력: servo_command/fan_command: 이번 명령, sensor: 수신 센서 저장 공간, timeout_ms: 전체 교환 제한 시간.
 * 출력: DMA 교환과 프레임 검증이 성공하면 true, 실패하면 false.
 * 처리 구조: 6바이트 TX/RX DMA를 설정하고 NSS를 내린 뒤 완료·SPI busy 해제를 기다립니다. 모든 종료 경로에서 DMA와 NSS를 정리합니다.
 */
bool spi1_exchange_sensor_dma(uint8_t servo_command, FanCommand fan_command,
                              SensorData *sensor, uint32_t timeout_ms);
/**
 * 기능: Master와 Slave2 사이에서 날씨·표시 명령 4바이트를 polling으로 교환합니다.
 * 입력: command: 송신 4바이트, response: 수신 4바이트 공간, timeout_ms: 제한 시간.
 * 출력: 교환이 완료되면 true, 제한 시간을 넘으면 false.
 * 처리 구조: NSS를 내리고 TXE/RXNE를 바이트마다 확인합니다. 실패 시 NSS 해제와 SPI 수신 상태 정리 후 반환합니다.
 */
bool spi2_exchange_weather(const uint8_t command[WEATHER_FRAME_SIZE],
                           uint8_t response[WEATHER_FRAME_SIZE],
                           uint32_t timeout_ms);

#endif
