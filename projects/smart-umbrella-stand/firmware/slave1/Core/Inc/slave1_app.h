/*
 * Slave1의 필터 상태와 보드 처리 함수 선언입니다.
 * 원형 표본 이력·합계·표본 수와 SensorData를 보관하며 slave1_app.c가 이를 갱신합니다.
 */

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

/**
 * 기능: Slave1 센서 필터 상태를 초기화합니다.
 * 입력: app: Slave1App 저장 공간.
 * 출력: 필터 이력·합계·인덱스·센서값을 0으로 초기화합니다.
 * 처리 구조: memset으로 누적 상태를 비워 첫 ADC 표본부터 이동평균을 계산할 준비를 합니다.
 */
void slave1_app_init(Slave1App *app);
/**
 * 기능: ADC DMA 표본으로 습도·수위 이동평균과 우산 상태를 갱신합니다.
 * 입력: app: 이전 필터 상태, water_dma/humidity_dma: 현재 ADC 표본, umbrella_present: IR 감지 결과.
 * 출력: app의 평균 센서값과 원형 필터 상태가 갱신됩니다.
 * 처리 구조: 원형 버퍼의 이전 값을 합계에서 빼고 새 값을 더합니다. 초기에는 실제 수신 표본 수로 나눠 평균을 구합니다.
 */
void slave1_app_update(Slave1App *app, uint16_t water_dma,
                       uint16_t humidity_dma, bool umbrella_present);
/**
 * 기능: Master의 팬·서보 명령을 실제 출력 레지스터에 반영합니다.
 * 입력: servo_level: 승강 위치 0~2, fan_command: 중풍/강풍/정지.
 * 출력: TIM3 서보 compare, TIM2 팬 duty, 모터 방향 GPIO를 변경합니다.
 * 처리 구조: 승강 위치를 1000/1500/2000 us 펄스로 바꾸고 팬 명령에 따라 duty 500/900/0을 선택합니다.
 */
void slave1_apply_commands(uint8_t servo_level, FanCommand fan_command);
/**
 * 기능: Slave1 센서 프레임을 보내면서 Master의 구동 명령을 받습니다.
 * 입력: app: 전송할 센서값, timeout_ms: 선택·바이트 대기 제한 시간.
 * 출력: 정상 6바이트 교환 시 수신한 팬·서보 명령을 적용합니다.
 * 처리 구조: 첫 헤더를 미리 DR에 넣고 NSS·RXNE를 기다립니다. 첫 두 RX 바이트를 명령으로 해석하고 다음 센서 바이트를 적재합니다.
 */
void slave1_spi_exchange_polling(const Slave1App *app, uint32_t timeout_ms);
/**
 * 기능: 64 MHz 타이머 입력에 맞춰 팬·서보 PWM 주기를 설정합니다.
 * 입력: 없음. MCU의 clock/GPIO 설정이 적용된 상태에서 호출합니다.
 * 출력: TIM3은 50 Hz 서보, TIM2는 1 kHz 팬 PWM의 prescaler/ARR를 설정합니다.
 * 처리 구조: PSC=63으로 1 MHz tick을 만들고 ARR=19999/999를 각각 적용합니다.
 */
void slave1_timers_init_64mhz(void);

#endif
