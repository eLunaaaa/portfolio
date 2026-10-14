/*
 * Zybo의 AI 추론 task입니다.
 * CameraTask가 공유 버퍼에 넣은 JPEG를 RGB tensor로 변환하고 TFLite Micro 모델을 실행합니다.
 * data-ready/AI-done semaphore로 버퍼 소유권을 주고받고 Arduino 결과는 UART TX queue로 전달합니다.
 */

#include "FreeRTOS.h"
#include "task.h"
#include "xil_printf.h"
#include "tensorflow/lite/micro/micro_interpreter.h"
#include "tensorflow/lite/micro/micro_log.h"
#include "tensorflow/lite/micro/micro_mutable_op_resolver.h"
#include "tensorflow/lite/schema/schema_generated.h"
#include "xil_cache.h"
#include "peripheral.h"
#include "stb_image.h"
#include "semphr.h"
#include "queue.h"
#include <stdio.h>
#include "model_data.h"
#include "../common/vision_core.h"

// External references
extern uint8_t* ImageBuffer;
extern u32 g_camera_len;


extern bool g_ai_ready;
extern SemaphoreHandle_t xSem_AI_Ready;
extern SemaphoreHandle_t xSem_DataReady;
extern SemaphoreHandle_t xSem_AI_Done;
extern QueueHandle_t xQueue_UartTx;  // UART 송신 Queue

// ===========================================================================
// 입력 tensor 자료형·차원 출력
// ===========================================================================
/**
 * 기능: 입력 tensor의 자료형·차원·메모리 크기를 로그에 출력합니다.
 * 입력: tensor: 모델 입력 tensor.
 * 출력: 콘솔에 FLOAT32/INT8 여부, shape, bytes와 기대 크기 비교를 표시합니다.
 * 처리 구조: tensor 메타데이터를 순서대로 읽어 출력하며 모델 추론 자체를 수행하지는 않습니다.
 */
void PrintTensorInfo(TfLiteTensor* tensor) {
    printf("==================================================\r\n");
    printf("[Check] Input Tensor Verification\r\n");
    printf(" - Type: %s (Expected: 1=FLOAT32)\r\n",
               (tensor->type == kTfLiteFloat32) ? "FLOAT32" :
               (tensor->type == kTfLiteInt8) ? "INT8" : "UNKNOWN");

    int dims = tensor->dims->size;
    printf(" - Dimensions: %d [", dims);
    for (int i = 0; i < dims; i++) {
        printf("%d%s", tensor->dims->data[i], (i < dims-1) ? ", " : "");
    }
    printf("]\r\n");

    printf(" - Total Bytes: %lu\r\n", (unsigned long)tensor->bytes);

    // 검증 로직
    if (tensor->type != kTfLiteFloat32) {
        printf(" [FAIL] Error: Model requires FLOAT32 input!\r\n");
    } else if (tensor->bytes != 128*128*3*4) {
        printf(" [FAIL] Error: Size mismatch! Expected 196608 bytes.\r\n");
    } else {
        printf(" [PASS] Input format is correct.\r\n");
    }
    printf("==================================================\r\n");
}
/**
 * 기능: 영상 전처리 코드가 사용할 수 있는 입력 tensor인지 확인합니다.
 * 입력: input: TFLite 입력 tensor 포인터.
 * 출력: FLOAT32 [1,128,128,3]과 정확한 공간이 있으면 true.
 * 처리 구조: 포인터, type, 차원 수·각 차원, data.f와 bytes를 모두 검사합니다.
 */
static bool InputTensorValid(const TfLiteTensor *input) {
    return input && input->type == kTfLiteFloat32 && input->dims &&
        input->dims->size == 4 && input->dims->data[0] == 1 &&
        input->dims->data[1] == 128 && input->dims->data[2] == 128 &&
        input->dims->data[3] == 3 && input->data.f &&
        input->bytes == 128u * 128u * 3u * sizeof(float);
}

/**
 * 기능: 카메라 JPEG를 AI 모델의 RGB 입력 tensor로 변환합니다.
 * 입력: jpeg_data/jpeg_len: 압축 영상, input: FLOAT32 모델 입력.
 * 출력: 성공이면 true와 채워진 입력 tensor, 실패이면 false.
 * 처리 구조: tensor·JPEG 해상도를 확인하고 stb_image로 RGB를 디코딩한 뒤 공통 리사이즈/정규화 함수를 호출하고 임시 영상을 해제합니다.
 */
bool ProcessImage(const uint8_t *jpeg_data, int jpeg_len, TfLiteTensor *input) {
    if (!jpeg_data || jpeg_len <= 0 || !InputTensorValid(input)) return false;
    int w = 0, h = 0, c = 0;
    if (!stbi_info_from_memory(jpeg_data, jpeg_len, &w, &h, &c) ||
        w <= 0 || h <= 0 || w > 640 || h > 480) return false;
    unsigned char *rgb = stbi_load_from_memory(jpeg_data, jpeg_len, &w, &h, &c, 3);
    if (!rgb) return false;
    bool ok = w > 0 && h > 0 &&
        rgb_resize_normalize(rgb, (size_t)w * (size_t)h * 3,
                             w, h, input->data.f,
                             input->bytes / sizeof(float), 128, 128);
    stbi_image_free(rgb);
    return ok;
}

// Globals
namespace {
    const int kTensorArenaSize = 4 * 1024 * 1024;
    alignas(16) uint8_t tensor_arena[kTensorArenaSize];
}

/**
 * 기능: 프레임 준비 신호를 기다렸다가 AI 추론과 분류 응답을 수행합니다.
 * 입력: pvParameters: FreeRTOS task 인자(미사용), 공유 JPEG 버퍼와 길이.
 * 출력: TX queue에 ASCII 분류/오류 명령을 넣고 AI 완료 semaphore로 버퍼를 반환합니다.
 * 처리 구조: 모델·resolver·arena와 tensor를 초기화하고 준비 상태를 알립니다. 이후 data-ready 대기→전처리→Invoke→점수 선택→완료 신호를 반복합니다.
 */
extern "C" void vAI_InferenceTask(void *pvParameters) {
    (void)pvParameters;
    printf("AI Task Initialized\r\n");

    // 1. 모델 로드
    const tflite::Model* model = tflite::GetModel(g_model);
    if (!model || model->version() != TFLITE_SCHEMA_VERSION) {
        printf("Model schema mismatch!\r\n");
        ColorLED(COLOR_MAGENTA);
        g_ai_ready = false;
        xSemaphoreGive(xSem_AI_Ready);
        vTaskDelete(NULL);
        return;
    }

    // 2. 연산자 등록
    static tflite::MicroMutableOpResolver<20> micro_op_resolver;
    micro_op_resolver.AddConv2D();
    micro_op_resolver.AddDepthwiseConv2D();
    micro_op_resolver.AddAveragePool2D();
    micro_op_resolver.AddMaxPool2D();
    micro_op_resolver.AddSoftmax();
    micro_op_resolver.AddReshape();
    micro_op_resolver.AddFullyConnected();
    micro_op_resolver.AddAdd();
    micro_op_resolver.AddMul();
    micro_op_resolver.AddSub();
    micro_op_resolver.AddMean();
    micro_op_resolver.AddPad();
    micro_op_resolver.AddConcatenation();
    micro_op_resolver.AddQuantize();
    micro_op_resolver.AddDequantize();

    // 3. 인터프리터
    static tflite::MicroInterpreter static_interpreter(
        model, micro_op_resolver, tensor_arena, kTensorArenaSize);
    tflite::MicroInterpreter* interpreter = &static_interpreter;

    if (interpreter->AllocateTensors() != kTfLiteOk) {
        printf("AllocateTensors() failed\r\n");
        ColorLED(COLOR_MAGENTA);
        g_ai_ready = false;
        xSemaphoreGive(xSem_AI_Ready);
        vTaskDelete(NULL);
        return;
    }

    TfLiteTensor* input = interpreter->input(0);
    TfLiteTensor* output = interpreter->output(0);

    if (!InputTensorValid(input) || !output || output->type != kTfLiteFloat32 ||
        !output->data.f || output->bytes != 5u * sizeof(float)) {
        printf("[Error] Expected float32 input [1,128,128,3], output 5 scores\r\n");
        g_ai_ready = false;
        xSemaphoreGive(xSem_AI_Ready);
        vTaskDelete(NULL);
        return;
    }
    PrintTensorInfo(input);

    g_ai_ready = true;
    xSemaphoreGive(xSem_AI_Ready);
    while (1) {
        xSemaphoreTake(xSem_DataReady, portMAX_DELAY);
        char code = '6';
        if (g_camera_len > 0 && g_camera_len <= 512u * 1024u &&
            ProcessImage(ImageBuffer, (int)g_camera_len, input) &&
            interpreter->Invoke() == kTfLiteOk) {
            code = classification_command(output->data.f, 5);
        }
        u8 tx_data = (u8)code;
        if (xQueueSend(xQueue_UartTx, &tx_data, pdMS_TO_TICKS(100)) != pdTRUE)
            printf("[Error] UART result queue full\r\n");
        g_camera_len = 0;
        ColorLED(0);
        /* Release on decode, invoke and output errors as well as success. */
        xSemaphoreGive(xSem_AI_Done);
    }
}
