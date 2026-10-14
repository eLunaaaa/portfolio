/*
 * 스마트 우산꽂이 Master의 상태 판정·구동 명령 생성 로직입니다.
 * 센서 보정과 습도 이동평균을 계산하고 wet/dry·강수 히스테리시스를 적용합니다.
 * 결과는 ControlOutput으로 전달하며 실제 통신·PWM 접근은 별도 어댑터가 수행합니다.
 */

#include "control_logic.h"
#include "wet_model_params.h"

#define HUMIDITY_RAW_DRY 10u
#define HUMIDITY_RAW_WET 260u
#define WATER_RAW_EMPTY 15u
#define WATER_RAW_FULL 260u
/* sigmoid(z) >= 0.60 and <= 0.40 expressed in logit space. */
#define WET_Z_ON  0.4054651f
#define WET_Z_OFF (-0.4054651f)

/**
 * 기능: 습도 ADC 값을 제어·모델 입력용 백분율로 보정합니다.
 * 입력: raw: 습도 센서 ADC 값.
 * 출력: 0.0~100.0 범위의 습도(float).
 * 처리 구조: 보정 하한·상한에서 0/100으로 제한하고 그 사이 값을 선형 변환합니다.
 */
float humidity_raw_to_percent(uint16_t raw)
{
    if (raw <= HUMIDITY_RAW_DRY) return 0.0f;
    if (raw >= HUMIDITY_RAW_WET) return 100.0f;
    return ((float)(raw - HUMIDITY_RAW_DRY) * 100.0f) /
           (float)(HUMIDITY_RAW_WET - HUMIDITY_RAW_DRY);
}

/**
 * 기능: 수위 ADC 값을 백분율로 보정합니다.
 * 입력: raw: 수위 센서 ADC 값.
 * 출력: 0~100 범위의 수위(uint8_t).
 * 처리 구조: empty/full 보정점을 기준으로 범위를 제한하고 정수 연산으로 선형 변환합니다.
 */
uint8_t water_raw_to_percent(uint16_t raw)
{
    if (raw <= WATER_RAW_EMPTY) return 0u;
    if (raw >= WATER_RAW_FULL) return 100u;
    return (uint8_t)(((uint32_t)(raw - WATER_RAW_EMPTY) * 100u) /
                     (WATER_RAW_FULL - WATER_RAW_EMPTY));
}

/**
 * 기능: wet/dry 판정에 사용할 로지스틱 회귀의 선형 점수 z를 계산합니다.
 * 입력: humidity: 현재 습도, umbrella: 우산 유무, mean: 습도 이동평균.
 * 출력: 각 feature와 가중치를 합산한 float 점수.
 * 처리 구조: 현재 습도·평균·변화량을 정규화한 뒤 wet_model_params.h의 가중치와 bias를 적용합니다.
 */
static float model_score(float humidity, bool umbrella, float mean)
{
    const float h_norm = (humidity / 100.0f - 0.5f) * 2.0f;
    const float mean_norm = (mean / 100.0f - 0.5f) * 2.0f;
    const float dh_norm = (humidity - mean) / 50.0f;
    return WET_W0 * h_norm + WET_W1 * (umbrella ? 1.0f : 0.0f) +
           WET_W2 * mean_norm + WET_W3 * dh_norm + WET_BIAS;
}

/**
 * 기능: 센서·날씨 상태를 통합해 다음 팬·승강 명령을 결정합니다.
 * 입력: state: 이전 평균·wet/rainy 상태, sensor: 현재 센서값, rain_probability/precipitation: 날씨, out: 결과 공간.
 * 출력: state를 갱신하고 out에 wet/rainy, 팬 명령, 서보 단계(0~2)를 기록합니다.
 * 처리 구조: 습도 평균을 갱신하고 60/40 히스테리시스로 상태를 판정한 뒤 우산 유무·젖음·비 상태별 명령을 선택합니다.
 */
void control_update(ControlState *state, const SensorData *sensor,
                    uint8_t rain_probability, PrecipitationType precipitation,
                    ControlOutput *out)
{
    const float humidity = humidity_raw_to_percent(sensor->humidity_raw);
    if (!state->mean_initialized) {
        state->humidity_mean = humidity;
        state->mean_initialized = true;
    } else {
        state->humidity_mean = 0.9f * state->humidity_mean + 0.1f * humidity;
    }

    if (!state->rainy_latched && rain_probability >= 60u) state->rainy_latched = true;
    else if (state->rainy_latched && rain_probability <= 40u) state->rainy_latched = false;

    out->rainy = state->rainy_latched || precipitation != PRECIP_NONE;

    /* Sensor-based wet/dry judgment comes only from the trained model score.
       40-60% is a hold band that preserves the previous state. */
    const float wet_score = model_score(humidity, sensor->umbrella_present,
                                        state->humidity_mean);
    if (!state->wet_latched && wet_score >= WET_Z_ON) {
        state->wet_latched = true;
    } else if (state->wet_latched && wet_score <= WET_Z_OFF) {
        state->wet_latched = false;
    }
    out->wet = state->wet_latched;

    if (!sensor->umbrella_present) {
        out->servo_level = 0u;
        out->fan_command = out->wet ? FAN_CMD_MEDIUM : FAN_CMD_OFF;
    } else if (out->wet) {
        out->servo_level = 0u;
        out->fan_command = out->rainy ? FAN_CMD_HIGH : FAN_CMD_MEDIUM;
    } else {
        out->servo_level = out->rainy ? 2u : 1u;
        out->fan_command = FAN_CMD_OFF;
    }
}
