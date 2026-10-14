#ifndef SMART_UMBRELLA_SLAVE1_APP_H
#define SMART_UMBRELLA_SLAVE1_APP_H

#include <stdint.h>
#include "protocol.h"

#define ADC_FILTER_TAPS 16u

typedef struct {
    uint16_t humidity_history[ADC_FILTER_TAPS];
    uint16_t water_history[ADC_FILTER_TAPS];
    uint32_t humidity_sum;
    uint32_t water_sum;
    uint8_t index;
    uint8_t samples;
    SensorData sensor;
} Slave1App;

void slave1_app_init(Slave1App *app);
void slave1_app_update(Slave1App *app, uint16_t water_dma,
                       uint16_t humidity_dma, bool umbrella_present);
void slave1_apply_commands(uint8_t servo_level, FanCommand fan_command);
void slave1_spi_exchange_polling(const Slave1App *app, uint32_t timeout_ms);
void slave1_timers_init_64mhz(void);

#endif

