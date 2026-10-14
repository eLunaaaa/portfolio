#ifndef VISION_CORE_H
#define VISION_CORE_H
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#ifdef __cplusplus
extern "C" {
#endif
bool jpeg_trim_padding(const uint8_t *data, size_t capacity, size_t *length);
bool rgb_resize_normalize(const uint8_t *rgb, size_t rgb_bytes,
                          int width, int height, float *output, size_t elements,
                          int out_width, int out_height);
char classification_command(const float *scores, size_t count);
#ifdef __cplusplus
}
#endif
#endif
