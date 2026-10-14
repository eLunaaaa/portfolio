/*
 * 영상 처리 공통 함수의 인터페이스입니다.
 * vision_core.c의 JPEG 검사·모델 전처리·분류 명령 함수를 C와 C++에서 함께 사용할 수 있게 선언합니다.
 */

#ifndef VISION_CORE_H
#define VISION_CORE_H
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#ifdef __cplusplus
extern "C" {
#endif
/**
 * 기능: JPEG의 시작·끝 마커를 검사하고 뒤에 붙은 0 패딩을 제외합니다.
 * 입력: data: JPEG 바이트, capacity: 버퍼 크기, length: 현재 길이 포인터.
 * 출력: 정상 형식이면 true와 보정된 length, 오류이면 false.
 * 처리 구조: 길이·SOI(FF D8)를 검사하고 trailing zero를 제거한 위치에서 EOI(FF D9)를 확인합니다.
 */
bool jpeg_trim_padding(const uint8_t *data, size_t capacity, size_t *length);
/**
 * 기능: RGB 영상을 모델 입력 크기로 bilinear 리사이즈하고 정규화합니다.
 * 입력: rgb/rgb_bytes: 원본 RGB와 크기, width/height: 원본 해상도, output/elements: float 출력 공간, out_width/out_height: 목표 해상도.
 * 출력: 입력·버퍼가 맞으면 true와 [-1,1] 정규화 영상, 아니면 false.
 * 처리 구조: 해상도와 공간 크기를 검증하고 half-pixel 좌표의 주변 4픽셀을 보간한 뒤 (pixel-127.5)/127.5를 적용합니다.
 */
bool rgb_resize_normalize(const uint8_t *rgb, size_t rgb_bytes,
                          int width, int height, float *output, size_t elements,
                          int out_width, int out_height);
/**
 * 기능: 5개 모델 점수에서 Arduino에 보낼 분류 명령을 선택합니다.
 * 입력: scores: 클래스별 점수 배열, count: 점수 개수(5).
 * 출력: 캔/일반/유리/PET/Empty는 ASCII 1~5, 잘못된 점수는 6.
 * 처리 구조: NaN/Inf와 0~1 범위를 검사한 뒤 가장 큰 점수의 인덱스를 명령 문자로 변환합니다.
 */
char classification_command(const float *scores, size_t count);
#ifdef __cplusplus
}
#endif
#endif
