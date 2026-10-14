#include "vision_core.h"
#include <math.h>
#include <limits.h>

bool jpeg_trim_padding(const uint8_t *data, size_t capacity, size_t *length)
{
    if (!data || !length || *length < 4 || *length > capacity) return false;
    size_t n = *length;
    if (data[0] != 0xff || data[1] != 0xd8) return false;
    while (n > 4 && data[n - 1] == 0) --n;
    if (data[n - 2] != 0xff || data[n - 1] != 0xd9) return false;
    *length = n;
    return true;
}

bool rgb_resize_normalize(const uint8_t *rgb, size_t rgb_bytes,
                          int width, int height, float *output, size_t elements,
                          int out_width, int out_height)
{
    if (!rgb || !output || width <= 0 || height <= 0 ||
        out_width <= 0 || out_height <= 0 || width > 4096 || height > 4096 ||
        out_width > 4096 || out_height > 4096) return false;
    if ((size_t)width > SIZE_MAX / 3 / (size_t)height ||
        (size_t)out_width > SIZE_MAX / 3 / (size_t)out_height) return false;
    if (rgb_bytes < (size_t)width * (size_t)height * 3 ||
        elements < (size_t)out_width * (size_t)out_height * 3) return false;
    /* Half-pixel bilinear resize, matching the documented training pipeline. */
    for (int y = 0; y < out_height; ++y) {
        float sy = ((float)y + 0.5f) * ((float)height / out_height) - 0.5f;
        if (sy < 0) sy = 0;
        int y0 = (int)sy, y1 = y0 + 1 < height ? y0 + 1 : y0;
        float fy = sy - y0;
        for (int x = 0; x < out_width; ++x) {
            float sx = ((float)x + 0.5f) * ((float)width / out_width) - 0.5f;
            if (sx < 0) sx = 0;
            int x0 = (int)sx, x1 = x0 + 1 < width ? x0 + 1 : x0;
            float fx = sx - x0;
            for (int c = 0; c < 3; ++c) {
                size_t p00 = ((size_t)y0 * width + x0) * 3 + c;
                size_t p01 = ((size_t)y0 * width + x1) * 3 + c;
                size_t p10 = ((size_t)y1 * width + x0) * 3 + c;
                size_t p11 = ((size_t)y1 * width + x1) * 3 + c;
                float top = rgb[p00] * (1 - fx) + rgb[p01] * fx;
                float bottom = rgb[p10] * (1 - fx) + rgb[p11] * fx;
                output[((size_t)y * out_width + x) * 3 + c] =
                    (top * (1 - fy) + bottom * fy - 127.5f) / 127.5f;
            }
        }
    }
    return true;
}

char classification_command(const float *scores, size_t count)
{
    if (!scores || count != 5) return '6';
    size_t best = 0;
    for (size_t i = 0; i < count; ++i) {
        if (!isfinite(scores[i]) || scores[i] < 0 || scores[i] > 1) return '6';
        if (scores[i] > scores[best]) best = i;
    }
    return (char)('1' + best); /* can, general, glass, pet, empty */
}
