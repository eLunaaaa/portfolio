/*
 * 날씨 조회용 날짜·시각 계산의 공통 로직입니다.
 * 월별 일수·윤년과 월말/연말 이동을 처리해 시간대 변환에 사용할 수 있습니다.
 * MCU 레지스터를 사용하지 않아 PC 테스트와 firmware에서 같은 함수를 호출합니다.
 */

#include "date_utils.h"

/**
 * 기능: 주어진 연도가 Gregorian 윤년인지 판단합니다.
 * 입력: year: 연도.
 * 출력: 윤년이면 true, 평년이면 false.
 * 처리 구조: 4의 배수 조건에 100년·400년 예외를 적용합니다.
 */
static bool is_leap_year(uint16_t year)
{
    return (year % 4u == 0u && year % 100u != 0u) || year % 400u == 0u;
}

/**
 * 기능: 지정한 연도·월의 마지막 날짜를 계산합니다.
 * 입력: year: 연도, month: 월(1~12).
 * 출력: 해당 월의 일수. 월 범위를 벗어나면 0.
 * 처리 구조: 기본 월별 일수 테이블을 사용하고 윤년 2월에만 29일을 반환합니다.
 */
uint8_t datetime_days_in_month(uint16_t year, uint8_t month)
{
    static const uint8_t days[] = {31u, 28u, 31u, 30u, 31u, 30u,
                                   31u, 31u, 30u, 31u, 30u, 31u};
    if (month < 1u || month > 12u) return 0u;
    if (month == 2u && is_leap_year(year)) return 29u;
    return days[month - 1u];
}

/**
 * 기능: 날짜와 시각 필드가 유효한지 검사합니다.
 * 입력: dt: 연·월·일·시·분·초를 담은 DateTime 포인터.
 * 출력: 모든 필드가 유효하면 true, 아니면 false.
 * 처리 구조: 1970년 이상인지, 실제 월별 일수 안의 날짜인지, 시·분·초 범위가 맞는지 순서대로 검사합니다.
 */
bool datetime_is_valid(const DateTime *dt)
{
    return dt != 0 && dt->year >= 1970u && dt->month >= 1u && dt->month <= 12u &&
           dt->day >= 1u && dt->day <= datetime_days_in_month(dt->year, dt->month) &&
           dt->hour < 24u && dt->minute < 60u && dt->second < 60u;
}

/**
 * 기능: 날짜를 하루 앞으로 이동합니다.
 * 입력: dt: 변경할 유효 DateTime.
 * 출력: dt의 날짜를 직접 변경합니다.
 * 처리 구조: day를 증가시키고 월 마지막 날을 넘으면 month, 연말을 넘으면 year까지 갱신합니다.
 */
static void next_day(DateTime *dt)
{
    if (++dt->day <= datetime_days_in_month(dt->year, dt->month)) return;
    dt->day = 1u;
    if (++dt->month <= 12u) return;
    dt->month = 1u;
    ++dt->year;
}

/**
 * 기능: 날짜를 하루 이전으로 이동합니다.
 * 입력: dt: 변경할 유효 DateTime.
 * 출력: 이동하면 true와 변경된 날짜, 1970-01-01보다 이전이면 false.
 * 처리 구조: 월초에서는 이전 월의 마지막 날짜를 계산하고 연초에서는 연도를 먼저 감소시킵니다.
 */
static bool previous_day(DateTime *dt)
{
    if (dt->year == 1970u && dt->month == 1u && dt->day == 1u) return false;
    if (dt->day > 1u) {
        --dt->day;
        return true;
    }
    if (dt->month > 1u) --dt->month;
    else {
        dt->month = 12u;
        --dt->year;
    }
    dt->day = datetime_days_in_month(dt->year, dt->month);
    return true;
}

/**
 * 기능: 날짜의 월말·연말 경계를 처리하며 시간을 더하거나 뺍니다.
 * 입력: dt: 기준 시각, hours: 이동할 시간 수(음수 허용).
 * 출력: 성공하면 true와 변경된 dt, 유효하지 않거나 지원 날짜 아래로 이동하면 false.
 * 처리 구조: 날짜를 검증한 뒤 시간을 한 칸씩 이동하며 자정 경계에서 next_day 또는 previous_day를 호출합니다.
 */
bool datetime_add_hours(DateTime *dt, int hours)
{
    if (!datetime_is_valid(dt)) return false;
    while (hours > 0) {
        if (++dt->hour == 24u) {
            dt->hour = 0u;
            next_day(dt);
        }
        --hours;
    }
    while (hours < 0) {
        if (dt->hour == 0u) {
            if (!previous_day(dt)) return false;
            dt->hour = 23u;
        } else {
            --dt->hour;
        }
        ++hours;
    }
    return true;
}
