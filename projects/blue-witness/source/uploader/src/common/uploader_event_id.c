#define _POSIX_C_SOURCE 200809L

#include "uploader_event_id.h"

#include <ctype.h>
#include <stdio.h>
#include <string.h>
#include <time.h>

#define SEOUL_UTC_OFFSET_SECONDS (9LL * 60LL * 60LL)
#define EVENT_VEHICLE_ID_SIZE 32U

static int compact_vehicle_id(
    const char *source,
    char output[EVENT_VEHICLE_ID_SIZE],
    char *error,
    size_t error_size)
{
    size_t source_index;
    size_t output_index = 0U;

    if (source == NULL || source[0] == '\0') {
        snprintf(error, error_size, "accident vehicle_id is empty");
        return -1;
    }

    for (source_index = 0U; source[source_index] != '\0'; source_index++) {
        unsigned char character = (unsigned char)source[source_index];

        if (character == '_' || character == '-') {
            continue;
        }
        if (!isalnum(character) || output_index + 1U >= EVENT_VEHICLE_ID_SIZE) {
            snprintf(error, error_size, "accident vehicle_id cannot form event_id");
            return -1;
        }
        output[output_index++] = (char)toupper(character);
    }

    if (output_index == 0U) {
        snprintf(error, error_size, "accident vehicle_id is empty");
        return -1;
    }
    output[output_index] = '\0';
    return 0;
}

int event_id_build(
    char *output,
    size_t output_size,
    int64_t event_time_ns,
    const char *accident_vehicle_id,
    char *error,
    size_t error_size)
{
    char vehicle_id[EVENT_VEHICLE_ID_SIZE] = {0};
    struct tm seoul_time;
    int64_t epoch_seconds;
    unsigned int milliseconds;
    time_t calendar_seconds;
    int length;

    if (output == NULL || output_size == 0U || error == NULL ||
        error_size == 0U || event_time_ns <= 0) {
        return -1;
    }
    if (compact_vehicle_id(
            accident_vehicle_id,
            vehicle_id,
            error,
            error_size) != 0) {
        return -1;
    }

    epoch_seconds = event_time_ns / 1000000000LL;
    milliseconds = (unsigned int)(
        (event_time_ns % 1000000000LL) / 1000000LL);
    calendar_seconds = (time_t)(epoch_seconds + SEOUL_UTC_OFFSET_SECONDS);

    if ((int64_t)calendar_seconds != epoch_seconds + SEOUL_UTC_OFFSET_SECONDS ||
        gmtime_r(&calendar_seconds, &seoul_time) == NULL) {
        snprintf(error, error_size, "event_time_ns is outside calendar range");
        return -1;
    }

    length = snprintf(
        output,
        output_size,
        "EVT_%04d%02d%02d_%02d%02d%02d%03u_%s",
        seoul_time.tm_year + 1900,
        seoul_time.tm_mon + 1,
        seoul_time.tm_mday,
        seoul_time.tm_hour,
        seoul_time.tm_min,
        seoul_time.tm_sec,
        milliseconds,
        vehicle_id);

    if (length < 0 || (size_t)length >= output_size) {
        snprintf(error, error_size, "derived event_id exceeds server limit");
        return -1;
    }
    return 0;
}
