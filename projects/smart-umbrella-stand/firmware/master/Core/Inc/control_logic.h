/*
 * Master 제어 로직의 입력·유지 상태·출력 인터페이스입니다.
 * ControlState는 이전 평균과 판정 상태, ControlOutput은 다음 팬·서보 명령과 wet/rainy 결과를 담습니다.
 */

#ifndef SMART_UMBRELLA_CONTROL_LOGIC_H
#define SMART_UMBRELLA_CONTROL_LOGIC_H

#include <stdbool.h>
#include <stdint.h>
#include "protocol.h"

typedef struct {
    float humidity_mean; /* 이전 주기까지의 습도 지수 이동평균(%) */
    bool mean_initialized; /* 첫 표본으로 평균을 초기화했는지 표시 */
    bool wet_latched; /* 40~60% hold 구간에서 유지할 직전 wet 판정 */
    bool rainy_latched; /* 강수확률 히스테리시스로 유지하는 rainy 판정 */
} ControlState;

typedef struct {
    uint8_t servo_level; /* 다음 구동 위치: 0=하단, 1/2=승강 단계 */
    FanCommand fan_command; /* 다음 팬 세기 또는 정지 명령 */
    bool wet; /* 현재 센서 기반 wet/dry 결과 */
    bool rainy; /* 확률·강수형태를 합친 현재 강수 상태 */
} ControlOutput;

/**
 * 기능: 습도 ADC 값을 제어·모델 입력용 백분율로 보정합니다.
 * 입력: raw: 습도 센서 ADC 값.
 * 출력: 0.0~100.0 범위의 습도(float).
 * 처리 구조: 보정 하한·상한에서 0/100으로 제한하고 그 사이 값을 선형 변환합니다.
 */
float humidity_raw_to_percent(uint16_t raw);
/**
 * 기능: 수위 ADC 값을 백분율로 보정합니다.
 * 입력: raw: 수위 센서 ADC 값.
 * 출력: 0~100 범위의 수위(uint8_t).
 * 처리 구조: empty/full 보정점을 기준으로 범위를 제한하고 정수 연산으로 선형 변환합니다.
 */
uint8_t water_raw_to_percent(uint16_t raw);
/**
 * 기능: 센서·날씨 상태를 통합해 다음 팬·승강 명령을 결정합니다.
 * 입력: state: 이전 평균·wet/rainy 상태, sensor: 현재 센서값, rain_probability/precipitation: 날씨, out: 결과 공간.
 * 출력: state를 갱신하고 out에 wet/rainy, 팬 명령, 서보 단계(0~2)를 기록합니다.
 * 처리 구조: 습도 평균을 갱신하고 60/40 히스테리시스로 상태를 판정한 뒤 우산 유무·젖음·비 상태별 명령을 선택합니다.
 */
void control_update(ControlState *state, const SensorData *sensor,
                    uint8_t rain_probability, PrecipitationType precipitation,
                    ControlOutput *out);

#endif
