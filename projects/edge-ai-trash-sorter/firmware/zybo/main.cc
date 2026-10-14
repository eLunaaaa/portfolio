/*
 * FreeRTOS Kernel V10.x.x
 * Zybo Z7-20: Camera AI System with UART Polling
 */

#include "FreeRTOS.h"
#include "task.h"
#include "xparameters.h"
#include "xil_printf.h"
#include "peripheral.h"
#include "arducam.h"
#include <stdlib.h>
#include "semphr.h"
#include "queue.h"
#include <stdio.h>
#include "xplatform_info.h"
#include "xscugic.h"
#include "../common/vision_core.h"

// ===================================================================
// 1. 전역 변수 및 핸들 선언
// ===================================================================

// 이미지를 담을 전역 버퍼
#define MAX_IMG_SIZE (1024 * 512)
u8 *ImageBuffer;
u32 g_camera_len = 0;

// UART 관련

QueueHandle_t xQueue_UartRx = NULL;
QueueHandle_t xQueue_UartTx = NULL;

// Semaphore & Mutex
SemaphoreHandle_t xSem_AI_Ready = NULL;
bool g_ai_ready = false;
SemaphoreHandle_t xSem_CaptureRequest = NULL;
SemaphoreHandle_t xSem_DataReady = NULL;
SemaphoreHandle_t xSem_AI_Done = NULL;
SemaphoreHandle_t xMutex_UartTx = NULL;

// 외부 AI 태스크
extern "C" void vAI_InferenceTask( void *pvParameters );

// 태스크 프로토타입
static void ColorTask( void *pvParameters );
static void ButtonTask( void *pvParameters );
static void CameraTask( void *pvParameters );
static void ArduinoPollingTask( void *pvParameters );
static void ArduinoProcessingTask( void *pvParameters );
static void ArduinoTxTask( void *pvParameters );
// ===================================================================
// 4. 메인 함수
// ===================================================================
int main( void )
{
    if (Setup_UART(921600) != XST_SUCCESS) return -1; // PC UART

    if (Setup_Arduino_UART(115200) != XST_SUCCESS) {
        xil_printf("[Error] Arduino UART Init Failed!\r\n");
        return -1;
    } else {
        xil_printf("Arduino UART Ready.\r\n");
        /* Class UART carries protocol bytes only; logs use the PC UART. */
    }

    if (Setup_GPIO() != XST_SUCCESS || Setup_SPI() != XST_SUCCESS ||
        Setup_I2C() != XST_SUCCESS) return -1;

    // FreeRTOS 객체 생성
    xSem_AI_Ready = xSemaphoreCreateBinary();
    xSem_CaptureRequest = xSemaphoreCreateBinary();
    xSem_DataReady = xSemaphoreCreateBinary();
    xSem_AI_Done   = xSemaphoreCreateBinary();
    xMutex_UartTx  = xSemaphoreCreateMutex();

    xQueue_UartRx = xQueueCreate(64, sizeof(u8)); // 넉넉하게
    xQueue_UartTx = xQueueCreate(64, sizeof(u8));

    if (!xQueue_UartRx || !xQueue_UartTx || !xMutex_UartTx ||
        !xSem_AI_Ready || !xSem_CaptureRequest || !xSem_DataReady || !xSem_AI_Done) {
        xil_printf("Object Creation Failed\r\n");
        return -1;
    }

    // 태스크 생성
    if (xTaskCreate( ColorTask, "Color", 256, NULL, 2, NULL ) != pdPASS) return -1;
    if (xTaskCreate( ButtonTask, "Button", 512, NULL, 3, NULL ) != pdPASS) return -1;
    if (xTaskCreate( CameraTask, "Camera", 4096, NULL, 2, NULL) != pdPASS) return -1;
    if (xTaskCreate( ArduinoTxTask, "TxTask", 256, NULL, 2, NULL ) != pdPASS) return -1;
    if (xTaskCreate( ArduinoPollingTask, "Polling", 512, NULL, 3, NULL ) != pdPASS) return -1;
    if (xTaskCreate( ArduinoProcessingTask, "Process", 512, NULL, 2, NULL ) != pdPASS) return -1;
    if (xTaskCreate( vAI_InferenceTask, "AI_Task", 8192, NULL, 3, NULL ) != pdPASS) return -1;

    xil_printf("Starting Scheduler...\r\n");

    // 스케줄러 시작
    vTaskStartScheduler();

    return 0;
}

// ===================================================================
// 5. 태스크 구현
// ===================================================================

/*
 * [Task 1] ColorTask
 * - LD4: 0.5초마다 켜짐/꺼짐 토글
 * - Classification LED updates are handled by capture/inference tasks.
 */
static void ColorTask( void *pvParameters )
{
    (void)pvParameters;
    const TickType_t xDelay = pdMS_TO_TICKS( 500 ); // 0.5초
    int ld4_state = 0;

    while(1)
    {
        // 1. LD4 토글
        ld4_state = !ld4_state;
        XGpioPs_WritePin(&Gpio, LD4_PIN, ld4_state);

        vTaskDelay( xDelay );
    }
}
/* Capture remains blocked until a request arrives. */
/*
 * [Task 2] ButtonTask
 * - 버튼 입력으로 촬영을 요청하거나 상태 LED를 변경
 * - "눌렀다 떼는 순간"이 아니라 "누르는 순간" 반응하도록 Rising Edge 검출
 */
static void ButtonTask( void *pvParameters )
{
    (void)pvParameters;
    const TickType_t xPollDelay = pdMS_TO_TICKS( 50 ); // 50ms마다 확인 (디바운싱 효과)
    u32 current_btn[4];
    u32 last_btn[4];

    // 초기 상태 읽기
    for(int i=0;i<4;i++)
        last_btn[i] = XGpioPs_ReadPin(&Gpio, INPUT_BTN+i);

    while(1)
    {
        // 버튼 상태 읽기
        for(int i=0;i<4;i++){
            current_btn[i] = XGpioPs_ReadPin(&Gpio, INPUT_BTN+i);

            // Rising Edge 감지 (이전엔 0이었는데 지금 1이면 = 눌렀음)
            if (last_btn[i] == 0 && current_btn[i] == 1)
            {
                printf("Button pressed\r\n");

                if(i==0){
                    xSemaphoreGive(xSem_CaptureRequest);
                }
                if(i==1){
                    xSemaphoreGive(xSem_CaptureRequest);
                    ColorLED(COLOR_GREEN);
                }
                if(i==2){
                    ColorLED(7);
                }
                if(i==3){
                    ColorLED(0);
                }
            }

            // 상태 업데이트
            last_btn[i] = current_btn[i];
        }

        vTaskDelay( xPollDelay );
    }
}

static void SendInferenceError(void) {
    u8 code = '6';
    if (xQueueSend(xQueue_UartTx, &code, pdMS_TO_TICKS(100)) != pdTRUE)
        printf("[Error] result UART queue full\r\n");
}

static void CameraTask(void *pvParameters) {
    (void)pvParameters;
    ImageBuffer = (u8 *)malloc(MAX_IMG_SIZE);
    if (ImageBuffer == NULL) {
        SendInferenceError();
        vTaskDelete(NULL);
        return;
    }
    xSemaphoreTake(xSem_AI_Ready, portMAX_DELAY);
    if (!g_ai_ready) {
        while (1) {
            xSemaphoreTake(xSem_CaptureRequest, portMAX_DELAY);
            SendInferenceError();
        }
    }
    while (ArduCAM_Init() != XST_SUCCESS) {
        ColorLED(COLOR_MAGENTA);
        vTaskDelay(pdMS_TO_TICKS(2000));
    }
    vTaskDelay(pdMS_TO_TICKS(2000));
    while (1) {
        xSemaphoreTake(xSem_CaptureRequest, portMAX_DELAY);
        ColorLED(COLOR_YELLOW);
        if (ArduCAM_Capture() != XST_SUCCESS) {
            SendInferenceError();
            continue;
        }
        u32 length = ArduCAM_ReadFifoLength();
        size_t checked_length = length;
        if (length == 0 || length > MAX_IMG_SIZE ||
            ArduCAM_ReadFifoBurst(ImageBuffer, length) != XST_SUCCESS ||
            !jpeg_trim_padding(ImageBuffer, MAX_IMG_SIZE, &checked_length)) {
            SendInferenceError();
            continue;
        }
        g_camera_len = (u32)checked_length;
        xSemaphoreGive(xSem_DataReady);
        /* Only AI may release this buffer. A request never releases it. */
        xSemaphoreTake(xSem_AI_Done, portMAX_DELAY);
    }
}

/*
 * ArduinoTxTask - UART 송신 전용 Task
 * - 송신 Queue에서 데이터를 꺼내서 UART로 전송합니다.
 * - Mutex를 사용하여 UART 하드웨어 접근을 보호합니다.
 */
static void ArduinoTxTask( void *pvParameters )
{
    (void)pvParameters;
    u8 TxByte;

    printf("Arduino Tx Task Started (Sending data from Queue...)\r\n");

    while(1)
    {
        // Queue에서 데이터 대기 (무한 대기)
        if (xQueueReceive(xQueue_UartTx, &TxByte, portMAX_DELAY) == pdTRUE) {
            // Mutex를 사용하여 UART 하드웨어 보호
            if (xSemaphoreTake(xMutex_UartTx, portMAX_DELAY) == pdTRUE) {
                // UART 전송
                Send_Byte_Arduino(TxByte);
                // Mutex 해제
                xSemaphoreGive(xMutex_UartTx);
            }
        }
    }
}
// [Polling Task] UART 수신 및 처리 통합
static void ArduinoPollingTask( void *pvParameters )
{
    (void)pvParameters;
    UINTPTR BaseAddr = Arduino_Uart.Config.BaseAddress; // 초기화된 주소 사용
    u8 RecvByte;

    // 만약 주소가 0이면 하드웨어 상수(SDT 환경) 사용
    if (BaseAddr == 0) BaseAddr = XPAR_XUARTPS_0_BASEADDR;

    xil_printf("[Polling] Arduino UART Task Started. (Checking every 10ms)\r\n");
    unsigned loop_count;
    while(1)
    {
        // 1. 데이터가 들어왔는지 확인 (하드웨어 레지스터 직접 조회)
        // XUartPs_IsReceiveData 매크로는 FIFO가 비어있지 않으면 True 반환
        loop_count = 0;
        while (XUartPs_IsReceiveData(BaseAddr) && loop_count < 100)
        {
            ++loop_count;
            // 2. 데이터 읽기
            RecvByte = XUartPs_ReadReg(BaseAddr, XUARTPS_FIFO_OFFSET);

            // 3. 데이터 Queue에 등록하기
            if (xQueueSend(xQueue_UartRx, &RecvByte, 0) != pdTRUE) {
                printf("[Warning] UART Rx Queue Full!\r\n");
            }
        }
        if(loop_count>=100)
            // 너무 많은 데이터 감지, 한번에 100개 이상의 데이터는 무시.
            printf("too many UART Inputs...\r\n");
        vTaskDelay(1);

    }
}

static void ArduinoProcessingTask( void *pvParameters )
{
    (void)pvParameters;
    char RecvByte;
    while(1)
    {
        // Queue에서 데이터 대기 (무한 대기)
        if (xQueueReceive(xQueue_UartRx, &RecvByte, portMAX_DELAY) == pdTRUE) {
            // UART 수신

            if (RecvByte == '1')
            {
                printf(" -> Command '1' Detected! Triggering AI...\r\n");

                // LED 녹색으로 변경 (인식됨 표시)
                ColorLED(COLOR_GREEN);

                // AI 동작 트리거 (Semaphore Give)
                xSemaphoreGive(xSem_CaptureRequest);
            }
        }

    }
}
