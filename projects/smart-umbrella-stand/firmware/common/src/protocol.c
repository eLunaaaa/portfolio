/*
 * 스마트 우산꽂이의 SPI wire format 직렬화·검증 로직입니다.
 * Slave1 센서 6바이트와 Slave2 날씨 4바이트를 명시적으로 해석합니다.
 * Master/Slave 양쪽에서 공유하며 구조체 padding이나 MCU별 정렬에 의존하지 않습니다.
 */

#include "protocol.h"

/**
 * 기능: 센서 값을 SPI 전송용 6바이트 배열로 직렬화합니다.
 * 입력: data: 습도·수위 ADC 값과 우산 유무, out: 6바이트 출력 공간.
 * 출력: out에 헤더 0xA5, 습도·수위의 little-endian 값, 0/1 우산 플래그를 기록합니다.
 * 처리 구조: 구조체를 통째로 보내지 않고 필드별 바이트를 넣어 메모리 padding과 무관한 프레임을 만듭니다.
 */
void sensor_frame_encode(uint8_t out[SENSOR_FRAME_SIZE], const SensorData *data)
{
    out[0] = SENSOR_FRAME_HEADER;
    out[1] = (uint8_t)(data->humidity_raw & 0xffu);
    out[2] = (uint8_t)(data->humidity_raw >> 8);
    out[3] = (uint8_t)(data->water_raw & 0xffu);
    out[4] = (uint8_t)(data->water_raw >> 8);
    out[5] = data->umbrella_present ? 1u : 0u;
}

/**
 * 기능: 수신한 SPI 센서 프레임을 검사하고 센서 구조체로 변환합니다.
 * 입력: frame: 6바이트 수신 배열, out: 유효한 센서값을 저장할 구조체.
 * 출력: 정상 프레임이면 true와 갱신된 out, 오류이면 false와 기존 out 유지.
 * 처리 구조: 포인터·헤더를 확인한 뒤 ADC 0~4095와 우산 플래그 0/1을 검사하고 최종적으로 값을 반영합니다.
 */
bool sensor_frame_decode(SensorData *out, const uint8_t frame[SENSOR_FRAME_SIZE])
{
    if (out == NULL || frame == NULL || frame[0] != SENSOR_FRAME_HEADER) {
        return false;
    }

    const uint16_t humidity = (uint16_t)frame[1] | ((uint16_t)frame[2] << 8);
    const uint16_t water = (uint16_t)frame[3] | ((uint16_t)frame[4] << 8);
    if (humidity > 4095u || water > 4095u || frame[5] > 1u) return false;
    out->humidity_raw = humidity;
    out->water_raw = water;
    out->umbrella_present = frame[5] != 0u;
    return true;
}

/**
 * 기능: Slave2의 날씨 응답에서 강수확률과 강수형태를 추출합니다.
 * 입력: frame: 4바이트 응답, rain_probability와 type: 결과 저장 포인터.
 * 출력: 정상 응답이면 true와 두 결과값, 잘못된 응답이면 false.
 * 처리 구조: 0xC7 헤더, 확률 0~100, 강수형태 enum 범위를 확인한 뒤 결과를 저장합니다.
 */
bool weather_response_decode(uint8_t *rain_probability,
                             PrecipitationType *type,
                             const uint8_t frame[WEATHER_FRAME_SIZE])
{
    if (rain_probability == NULL || type == NULL || frame == NULL ||
        frame[0] != WEATHER_RSP_HEADER || frame[1] > 100u ||
        frame[2] > (uint8_t)PRECIP_SHOWER) {
        return false;
    }
    *rain_probability = frame[1];
    *type = (PrecipitationType)frame[2];
    return true;
}

/**
 * 기능: 강수형태 enum을 화면·로그에 사용할 문자열로 바꿉니다.
 * 입력: type: NONE/RAIN/RAIN_SNOW/SNOW/SHOWER 중 하나.
 * 출력: 해당 강수형태의 상수 문자열 포인터.
 * 처리 구조: switch로 상태를 구분하고 알 수 없는 값은 NONE으로 표시합니다.
 */
const char *precipitation_label(PrecipitationType type)
{
    switch (type) {
    case PRECIP_RAIN: return "RAIN";
    case PRECIP_RAIN_SNOW: return "RAIN/SNOW";
    case PRECIP_SNOW: return "SNOW";
    case PRECIP_SHOWER: return "SHOWER";
    case PRECIP_NONE:
    default: return "NONE";
    }
}
