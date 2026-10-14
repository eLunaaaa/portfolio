#include "date_utils.h"

static bool is_leap_year(uint16_t year)
{
    return (year % 4u == 0u && year % 100u != 0u) || year % 400u == 0u;
}

uint8_t datetime_days_in_month(uint16_t year, uint8_t month)
{
    static const uint8_t days[] = {31u, 28u, 31u, 30u, 31u, 30u,
                                   31u, 31u, 30u, 31u, 30u, 31u};
    if (month < 1u || month > 12u) return 0u;
    if (month == 2u && is_leap_year(year)) return 29u;
    return days[month - 1u];
}

bool datetime_is_valid(const DateTime *dt)
{
    return dt != 0 && dt->year >= 1970u && dt->month >= 1u && dt->month <= 12u &&
           dt->day >= 1u && dt->day <= datetime_days_in_month(dt->year, dt->month) &&
           dt->hour < 24u && dt->minute < 60u && dt->second < 60u;
}

static void next_day(DateTime *dt)
{
    if (++dt->day <= datetime_days_in_month(dt->year, dt->month)) return;
    dt->day = 1u;
    if (++dt->month <= 12u) return;
    dt->month = 1u;
    ++dt->year;
}

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

