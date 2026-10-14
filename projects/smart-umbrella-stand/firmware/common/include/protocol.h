/*
 * 스마트 우산꽂이의 통신 상수·데이터 구조·명령 enum입니다.
 * SensorData, FanCommand, PrecipitationType을 정의해 Master와 두 Slave가 같은 값의 의미를 사용합니다.
 */

#ifndef SMART_UMBRELLA_PROTOCOL_H
#define SMART_UMBRELLA_PROTOCOL_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#define SENSOR_FRAME_HEADER 0xA5u
#define SENSOR_FRAME_SIZE   6u
#define WEATHER_CMD_HEADER  0xB6u
#define WEATHER_RSP_HEADER  0xC7u
#define WEATHER_FRAME_SIZE  4u

typedef struct {
    uint16_t humidity_raw;
    uint16_t water_raw;
    bool umbrella_present;
} SensorData;

typedef enum {
    FAN_CMD_MEDIUM = 3u,
    FAN_CMD_HIGH = 4u,
    FAN_CMD_OFF = 5u,
} FanCommand;

typedef enum {
    PRECIP_NONE = 0u,
    PRECIP_RAIN = 1u,
    PRECIP_RAIN_SNOW = 2u,
    PRECIP_SNOW = 3u,
    PRECIP_SHOWER = 4u,
} PrecipitationType;

/**
 * 기능: 센서 값을 SPI 전송용 6바이트 배열로 직렬화합니다.
 * 입력: data: 습도·수위 ADC 값과 우산 유무, out: 6바이트 출력 공간.
 * 출력: out에 헤더 0xA5, 습도·수위의 little-endian 값, 0/1 우산 플래그를 기록합니다.
 * 처리 구조: 구조체를 통째로 보내지 않고 필드별 바이트를 넣어 메모리 padding과 무관한 프레임을 만듭니다.
 */
void sensor_frame_encode(uint8_t out[SENSOR_FRAME_SIZE], const SensorData *data);
/**
 * 기능: 수신한 SPI 센서 프레임을 검사하고 센서 구조체로 변환합니다.
 * 입력: frame: 6바이트 수신 배열, out: 유효한 센서값을 저장할 구조체.
 * 출력: 정상 프레임이면 true와 갱신된 out, 오류이면 false와 기존 out 유지.
 * 처리 구조: 포인터·헤더를 확인한 뒤 ADC 0~4095와 우산 플래그 0/1을 검사하고 최종적으로 값을 반영합니다.
 */
bool sensor_frame_decode(SensorData *out, const uint8_t frame[SENSOR_FRAME_SIZE]);
/**
 * 기능: Slave2의 날씨 응답에서 강수확률과 강수형태를 추출합니다.
 * 입력: frame: 4바이트 응답, rain_probability와 type: 결과 저장 포인터.
 * 출력: 정상 응답이면 true와 두 결과값, 잘못된 응답이면 false.
 * 처리 구조: 0xC7 헤더, 확률 0~100, 강수형태 enum 범위를 확인한 뒤 결과를 저장합니다.
 */
bool weather_response_decode(uint8_t *rain_probability,
                             PrecipitationType *type,
                             const uint8_t frame[WEATHER_FRAME_SIZE]);
/**
 * 기능: 강수형태 enum을 화면·로그에 사용할 문자열로 바꿉니다.
 * 입력: type: NONE/RAIN/RAIN_SNOW/SNOW/SHOWER 중 하나.
 * 출력: 해당 강수형태의 상수 문자열 포인터.
 * 처리 구조: switch로 상태를 구분하고 알 수 없는 값은 NONE으로 표시합니다.
 */
const char *precipitation_label(PrecipitationType type);

#endif
