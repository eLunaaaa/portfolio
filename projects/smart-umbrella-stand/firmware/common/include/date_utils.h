/*
 * 날씨 조회 시각을 위한 DateTime 구조와 날짜 함수 선언입니다.
 * 연·월·일·시·분·초 필드를 저장하며 실제 검증·시간 이동은 date_utils.c에서 수행합니다.
 */

#ifndef SMART_UMBRELLA_DATE_UTILS_H
#define SMART_UMBRELLA_DATE_UTILS_H

#include <stdbool.h>
#include <stdint.h>

typedef struct {
    uint16_t year;
    uint8_t month;
    uint8_t day;
    uint8_t hour;
    uint8_t minute;
    uint8_t second;
} DateTime;

/**
 * 기능: 날짜와 시각 필드가 유효한지 검사합니다.
 * 입력: dt: 연·월·일·시·분·초를 담은 DateTime 포인터.
 * 출력: 모든 필드가 유효하면 true, 아니면 false.
 * 처리 구조: 1970년 이상인지, 실제 월별 일수 안의 날짜인지, 시·분·초 범위가 맞는지 순서대로 검사합니다.
 */
bool datetime_is_valid(const DateTime *dt);
/**
 * 기능: 날짜의 월말·연말 경계를 처리하며 시간을 더하거나 뺍니다.
 * 입력: dt: 기준 시각, hours: 이동할 시간 수(음수 허용).
 * 출력: 성공하면 true와 변경된 dt, 유효하지 않거나 지원 날짜 아래로 이동하면 false.
 * 처리 구조: 날짜를 검증한 뒤 시간을 한 칸씩 이동하며 자정 경계에서 next_day 또는 previous_day를 호출합니다.
 */
bool datetime_add_hours(DateTime *dt, int hours);
/**
 * 기능: 지정한 연도·월의 마지막 날짜를 계산합니다.
 * 입력: year: 연도, month: 월(1~12).
 * 출력: 해당 월의 일수. 월 범위를 벗어나면 0.
 * 처리 구조: 기본 월별 일수 테이블을 사용하고 윤년 2월에만 29일을 반환합니다.
 */
uint8_t datetime_days_in_month(uint16_t year, uint8_t month);

#endif
