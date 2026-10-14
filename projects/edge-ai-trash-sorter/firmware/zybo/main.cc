/*
 * Zybo 프로그램의 RTOS 진입점과 제어 task 모음입니다.
 * 카메라, AI, UART, 버튼·상태 표시 task와 이들을 연결하는 queue/semaphore를 생성합니다.
 * CameraTask는 arducam.c를 호출하며 AI 구현은 ai_task.cc, 실제 주변장치 초기화는 peripheral.c가 맡습니다.
 */

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

QueueHandle_t xQueue_UartRx = NULL; /* 수신 task → 명령 처리 task의 바이트 큐 */
QueueHandle_t xQueue_UartTx = NULL; /* 촬영/AI task → 실제 UART 송신 task의 결과 큐 */

// Semaphore & Mutex
SemaphoreHandle_t xSem_AI_Ready = NULL; /* AI 초기화 결과를 CameraTask에 전달 */
bool g_ai_ready = false; /* AI-ready 신호와 함께 전달하는 성공 여부 */
SemaphoreHandle_t xSem_CaptureRequest = NULL; /* Arduino/버튼이 촬영을 요청하는 이벤트 */
SemaphoreHandle_t xSem_DataReady = NULL; /* JPEG 준비 후 AI task를 깨우는 이벤트 */
SemaphoreHandle_t xSem_AI_Done = NULL; /* AI가 공유 JPEG 사용을 마쳤음을 알리는 이벤트 */
SemaphoreHandle_t xMutex_UartTx = NULL;

// 외부 AI 태스크
/**
 * 기능: 프레임 준비 신호를 기다렸다가 AI 추론과 분류 응답을 수행합니다.
 * 입력: pvParameters: FreeRTOS task 인자(미사용), 공유 JPEG 버퍼와 길이.
 * 출력: TX queue에 ASCII 분류/오류 명령을 넣고 AI 완료 semaphore로 버퍼를 반환합니다.
 * 처리 구조: 모델·resolver·arena와 tensor를 초기화하고 준비 상태를 알립니다. 이후 data-ready 대기→전처리→Invoke→점수 선택→완료 신호를 반복합니다.
 */
extern "C" void vAI_InferenceTask( void *pvParameters );

// 태스크 프로토타입
/**
 * 기능: 시스템 heartbeat LED를 500 ms 주기로 점멸합니다.
 * 입력: pvParameters: task 인자(미사용).
 * 출력: LD4 GPIO를 반복적으로 전환합니다.
 * 처리 구조: LED 상태를 반전하고 vTaskDelay로 다음 주기까지 양보합니다.
 */
static void ColorTask( void *pvParameters );
/**
 * 기능: 버튼 상승 에지를 감지해 수동 촬영 요청·LED 명령을 처리합니다.
 * 입력: pvParameters: task 인자(미사용), GPIO 버튼 4개.
 * 출력: 촬영 요청 semaphore 또는 RGB 표시 상태를 변경합니다.
 * 처리 구조: 50 ms마다 이전·현재 버튼 상태를 비교하고 눌린 순간에만 해당 동작을 수행합니다.
 */
static void ButtonTask( void *pvParameters );
/**
 * 기능: 촬영 요청에 맞춰 JPEG를 획득하고 AI task에 전달합니다.
 * 입력: pvParameters: task 인자(미사용), capture-request/AI-ready semaphore.
 * 출력: 공유 ImageBuffer와 길이를 채우고 data-ready 신호를 보내며 오류 시 TX queue에 6을 넣습니다.
 * 처리 구조: AI 준비→카메라 초기화→요청 대기→촬영/FIFO/JPEG 검사→AI 전달→완료 대기 순으로 반복해 버퍼 재사용 시점을 관리합니다.
 */
static void CameraTask( void *pvParameters );
/**
 * 기능: Arduino UART FIFO의 수신 데이터를 RTOS queue에 옮깁니다.
 * 입력: pvParameters: task 인자(미사용), UART RX FIFO.
 * 출력: RX queue에 수신 바이트를 넣습니다.
 * 처리 구조: 회당 최대 100바이트를 읽고 queue에 전달합니다. 이후 1 tick을 양보하고 반복합니다.
 */
static void ArduinoPollingTask( void *pvParameters );
/**
 * 기능: 수신 queue의 명령을 촬영 요청으로 해석합니다.
 * 입력: pvParameters: task 인자(미사용), RX queue.
 * 출력: ASCII 1을 받으면 capture-request semaphore를 줍니다.
 * 처리 구조: queue에서 blocking 대기하고 지원하는 요청 문자에만 반응합니다.
 */
static void ArduinoProcessingTask( void *pvParameters );
/**
 * 기능: 분류 결과 queue를 UART 송신으로 연결합니다.
 * 입력: pvParameters: task 인자(미사용), TX queue.
 * 출력: 수신한 명령 바이트를 Arduino UART로 보냅니다.
 * 처리 구조: queue에서 대기해 바이트를 꺼내고 UART mutex를 잡아 송신한 뒤 해제합니다.
 */
static void ArduinoTxTask( void *pvParameters );
// ===================================================================
// 4. 메인 함수
// ===================================================================
/**
 * 기능: Zybo 제어 프로그램의 하드웨어·RTOS 객체·task를 시작합니다.
 * 입력: BSP 주변장치 설정.
 * 출력: 촬영·추론·UART 처리 task가 동작하는 FreeRTOS 스케줄러.
 * 처리 구조: UART/GPIO/SPI/I2C를 초기화하고 semaphore·queue·mutex를 만듭니다. 객체·task 생성 성공을 확인한 뒤 scheduler를 시작합니다.
 */
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
/**
 * 기능: 시스템 heartbeat LED를 500 ms 주기로 점멸합니다.
 * 입력: pvParameters: task 인자(미사용).
 * 출력: LD4 GPIO를 반복적으로 전환합니다.
 * 처리 구조: LED 상태를 반전하고 vTaskDelay로 다음 주기까지 양보합니다.
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
/**
 * 기능: 버튼 상승 에지를 감지해 수동 촬영 요청·LED 명령을 처리합니다.
 * 입력: pvParameters: task 인자(미사용), GPIO 버튼 4개.
 * 출력: 촬영 요청 semaphore 또는 RGB 표시 상태를 변경합니다.
 * 처리 구조: 50 ms마다 이전·현재 버튼 상태를 비교하고 눌린 순간에만 해당 동작을 수행합니다.
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

/**
 * 기능: 촬영 또는 준비 실패를 Arduino에 알릴 오류 명령을 예약합니다.
 * 입력: 없음. 전역 TX queue를 사용합니다.
 * 출력: queue에 ASCII 6을 넣으며 queue 포화 시 로그를 남깁니다.
 * 처리 구조: 최대 100 ms 동안 queue에 전송 요청을 넣고 실제 UART 송신은 전용 task가 수행합니다.
 */
static void SendInferenceError(void) {
    u8 code = '6';
    if (xQueueSend(xQueue_UartTx, &code, pdMS_TO_TICKS(100)) != pdTRUE)
        printf("[Error] result UART queue full\r\n");
}

/**
 * 기능: 촬영 요청에 맞춰 JPEG를 획득하고 AI task에 전달합니다.
 * 입력: pvParameters: task 인자(미사용), capture-request/AI-ready semaphore.
 * 출력: 공유 ImageBuffer와 길이를 채우고 data-ready 신호를 보내며 오류 시 TX queue에 6을 넣습니다.
 * 처리 구조: AI 준비→카메라 초기화→요청 대기→촬영/FIFO/JPEG 검사→AI 전달→완료 대기 순으로 반복해 버퍼 재사용 시점을 관리합니다.
 */
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
/**
 * 기능: 분류 결과 queue를 UART 송신으로 연결합니다.
 * 입력: pvParameters: task 인자(미사용), TX queue.
 * 출력: 수신한 명령 바이트를 Arduino UART로 보냅니다.
 * 처리 구조: queue에서 대기해 바이트를 꺼내고 UART mutex를 잡아 송신한 뒤 해제합니다.
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
/**
 * 기능: Arduino UART FIFO의 수신 데이터를 RTOS queue에 옮깁니다.
 * 입력: pvParameters: task 인자(미사용), UART RX FIFO.
 * 출력: RX queue에 수신 바이트를 넣습니다.
 * 처리 구조: 회당 최대 100바이트를 읽고 queue에 전달합니다. 이후 1 tick을 양보하고 반복합니다.
 */
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

/**
 * 기능: 수신 queue의 명령을 촬영 요청으로 해석합니다.
 * 입력: pvParameters: task 인자(미사용), RX queue.
 * 출력: ASCII 1을 받으면 capture-request semaphore를 줍니다.
 * 처리 구조: queue에서 blocking 대기하고 지원하는 요청 문자에만 반응합니다.
 */
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
