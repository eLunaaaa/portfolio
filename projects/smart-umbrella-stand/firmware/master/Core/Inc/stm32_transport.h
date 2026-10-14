#ifndef SMART_UMBRELLA_STM32_TRANSPORT_H
#define SMART_UMBRELLA_STM32_TRANSPORT_H

#include <stdbool.h>
#include <stdint.h>
#include "protocol.h"

bool spi1_exchange_sensor_dma(uint8_t servo_command, FanCommand fan_command,
                              SensorData *sensor, uint32_t timeout_ms);
bool spi2_exchange_weather(const uint8_t command[WEATHER_FRAME_SIZE],
                           uint8_t response[WEATHER_FRAME_SIZE],
                           uint32_t timeout_ms);

#endif

