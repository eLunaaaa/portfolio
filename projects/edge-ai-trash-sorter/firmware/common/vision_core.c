/*
 * 쓰레기 분류 시스템의 하드웨어 독립 영상 처리 로직입니다.
 * JPEG 마커·버퍼 크기 검사, RGB bilinear 리사이즈/정규화, AI 점수의 명령 변환을 담당합니다.
 * Zybo의 ai_task.cc와 CameraTask가 호출하며 같은 함수를 PC 테스트에서도 사용합니다.
 */

#include "vision_core.h"
#include <math.h>
#include <limits.h>

/**
 * 기능: JPEG의 시작·끝 마커를 검사하고 뒤에 붙은 0 패딩을 제외합니다.
 * 입력: data: JPEG 바이트, capacity: 버퍼 크기, length: 현재 길이 포인터.
 * 출력: 정상 형식이면 true와 보정된 length, 오류이면 false.
 * 처리 구조: 길이·SOI(FF D8)를 검사하고 trailing zero를 제거한 위치에서 EOI(FF D9)를 확인합니다.
 */
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

/**
 * 기능: RGB 영상을 모델 입력 크기로 bilinear 리사이즈하고 정규화합니다.
 * 입력: rgb/rgb_bytes: 원본 RGB와 크기, width/height: 원본 해상도, output/elements: float 출력 공간, out_width/out_height: 목표 해상도.
 * 출력: 입력·버퍼가 맞으면 true와 [-1,1] 정규화 영상, 아니면 false.
 * 처리 구조: 해상도와 공간 크기를 검증하고 half-pixel 좌표의 주변 4픽셀을 보간한 뒤 (pixel-127.5)/127.5를 적용합니다.
 */
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

/**
 * 기능: 5개 모델 점수에서 Arduino에 보낼 분류 명령을 선택합니다.
 * 입력: scores: 클래스별 점수 배열, count: 점수 개수(5).
 * 출력: 캔/일반/유리/PET/Empty는 ASCII 1~5, 잘못된 점수는 6.
 * 처리 구조: NaN/Inf와 0~1 범위를 검사한 뒤 가장 큰 점수의 인덱스를 명령 문자로 변환합니다.
 */
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
