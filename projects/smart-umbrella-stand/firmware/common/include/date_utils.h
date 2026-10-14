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

bool datetime_is_valid(const DateTime *dt);
bool datetime_add_hours(DateTime *dt, int hours);
uint8_t datetime_days_in_month(uint16_t year, uint8_t month);

#endif

