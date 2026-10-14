#include <assert.h>
#include <math.h>
#include <stdio.h>
#include "vision_core.h"

int main(void) {
    uint8_t jpeg[] = {0xff,0xd8,1,2,0xff,0xd9,0,0};
    size_t len = sizeof(jpeg);
    assert(jpeg_trim_padding(jpeg, sizeof(jpeg), &len) && len == 6);
    len = 100;
    assert(!jpeg_trim_padding(jpeg, sizeof(jpeg), &len) && len == 100);
    len = 1;
    assert(!jpeg_trim_padding(jpeg, sizeof(jpeg), &len));
    len = sizeof(jpeg); jpeg[7] = 1;
    assert(!jpeg_trim_padding(jpeg, sizeof(jpeg), &len));
    assert(!jpeg_trim_padding(NULL, 0, NULL));
    uint8_t rgb[] = {0,0,0,255,255,255,255,255,255,0,0,0};
    float out[12];
    assert(rgb_resize_normalize(rgb, sizeof(rgb), 2, 2, out, 12, 2, 2));
    assert(out[0] == -1 && out[3] == 1);
    assert(rgb_resize_normalize(rgb, sizeof(rgb), 2, 2, out, 3, 1, 1));
    assert(fabsf(out[0]) < 1e-6f); /* average of the four pixels */
    assert(!rgb_resize_normalize(rgb, 3, 2, 2, out, 12, 2, 2));
    assert(!rgb_resize_normalize(rgb, sizeof(rgb), 2, 2, out, 2, 1, 1));
    assert(!rgb_resize_normalize(rgb, sizeof(rgb), 0, 2, out, 12, 2, 2));
    float scores[5] = {0.1f,0.1f,0.1f,0.1f,0.1f};
    for (int i = 0; i < 5; ++i) {
        scores[i] = 0.6f;
        assert(classification_command(scores,5) == '1' + i);
        scores[i] = 0.1f;
    }
    scores[0] = NAN; assert(classification_command(scores,5) == '6');
    scores[0] = INFINITY; assert(classification_command(scores,5) == '6');
    scores[0] = -1; assert(classification_command(scores,5) == '6');
    assert(classification_command(NULL,5) == '6');
    assert(classification_command(scores,4) == '6');
    puts("JPEG, bilinear preprocessing and class protocol tests passed");
    return 0;
}
