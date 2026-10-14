/*
 * Zybo 주변장치 어댑터의 공통 인터페이스와 핀 정의입니다.
 * main.cc·ai_task.cc·arducam.c가 공유할 인스턴스와 초기화/송신/LED 함수를 선언합니다.
 */

#ifndef __PERIPHERAL_H__
#define __PERIPHERAL_H__

#include "xil_types.h"
#ifdef __cplusplus
extern "C" {
#endif

#include "xil_types.h"
#include "xgpiops.h"
#include "xuartps.h"
#include "xuartps_hw.h"
#include "xiicps.h"
#include "xspips.h"
#include "FreeRTOS.h"
#include "sleep.h"

#ifndef __PERIPHERAl__C_
extern XGpioPs Gpio;
extern XSpiPs Spi;
extern XIicPs Iic;
extern XUartPs Uart_Ps;
extern XUartPs Arduino_Uart;
#else
#endif

#define delay(x) vTaskDelay(pdMS_TO_TICKS(x));

/**
 * 기능: Arduino 분류 명령용 UART를 설정합니다.
 * 입력: baud: 양쪽 보드에서 맞출 보레이트.
 * 출력: UART 설정 상태 코드.
 * 처리 구조: Arduino UART BSP 설정을 조회해 초기화하고 normal mode와 보레이트를 적용합니다.
 */
int Setup_Arduino_UART(u32 BaudRate);
/**
 * 기능: Arduino UART로 NUL 종료 문자열을 순서대로 보냅니다.
 * 입력: message/Msg: 송신 문자열.
 * 출력: 문자열을 전송하며 오류가 생기면 즉시 종료합니다.
 * 처리 구조: 각 문자를 Send_Byte_Arduino로 전달합니다. 분류 task의 실제 결과 전송은 TX queue 경로를 사용합니다.
 */
void Send_To_Arduino(const char *Msg);
/**
 * 기능: Arduino 명령 UART로 바이트 하나를 보냅니다.
 * 입력: data: ASCII 분류·오류 명령.
 * 출력: 공통 send_byte의 전송 상태 코드.
 * 처리 구조: 전역 Arduino_Uart 인스턴스로 bounded FIFO 송신을 수행합니다.
 */
int Send_Byte_Arduino(u8 Data);

/**
 * 기능: 상태 LED와 버튼용 PS GPIO 방향을 설정합니다.
 * 입력: 없음. BSP의 GPIO 설정과 peripheral.h의 핀 상수를 사용합니다.
 * 출력: 조회·초기화 상태 코드.
 * 처리 구조: GPIO 인스턴스를 초기화하고 LED는 output/enable, 버튼 영역은 input으로 설정합니다.
 */
int Setup_GPIO(void);
/**
 * 기능: 카메라 레지스터 제어용 PS I2C를 초기화합니다.
 * 입력: 없음. BSP의 I2C 설정을 사용합니다.
 * 출력: 조회·초기화·self-test·clock 설정 상태 코드.
 * 처리 구조: 설정 조회 후 인스턴스 초기화와 자가 검사를 수행하고 50 kHz로 설정합니다.
 */
int Setup_I2C(void);
/**
 * 기능: ArduCAM 제어·FIFO 전송용 PS SPI를 초기화합니다.
 * 입력: 없음. BSP의 SPI 설정을 사용합니다.
 * 출력: 설정 단계의 상태 코드.
 * 처리 구조: Master/force slave-select, clock prescaler 128, ArduCAM SS0을 차례대로 설정합니다.
 */
int Setup_SPI(void);
/**
 * 기능: PC 디버그용 UART를 설정합니다.
 * 입력: baud: 디버그 UART 보레이트.
 * 출력: UART 설정 상태 코드.
 * 처리 구조: BSP의 PC UART 설정을 조회하고 공통 setup_uart를 호출합니다.
 */
int Setup_UART(u32 BaudRate);

/**
 * 기능: PC UART에 바이트 배열을 그대로 보냅니다.
 * 입력: data/Data: 원본 배열, size/Size: 바이트 수.
 * 출력: 전체 성공이면 XST_SUCCESS, 포인터·전송 오류이면 XST_FAILURE.
 * 처리 구조: 줄바꿈 변환 없이 send_byte로 순서대로 전달해 binary 데이터 형태를 유지합니다.
 */
int Uart_Send_Raw_Data(u8 *Data, u32 Size);


#define COLOR_RED 1
#define COLOR_GREEN 2
#define COLOR_BLUE 4
#define COLOR_CYAN 6
#define COLOR_MAGENTA 5
#define COLOR_YELLOW 3

/**
 * 기능: 분류·오류 상태를 RGB LED 색 비트로 표시합니다.
 * 입력: color/colorIdx: bit0=red, bit1=green, bit2=blue.
 * 출력: RGB 출력 GPIO의 HIGH/LOW를 변경합니다.
 * 처리 구조: 세 비트를 분리해 대응하는 LED 핀에 기록합니다.
 */
void ColorLED(uint8_t colorIdx);

// ===================================================================
// 1. 핀 번호 정의 (사용자님 설정 기준)
// ===================================================================
// [PS MIO]
#define LD4_PIN         7       // LD4 (Single LED)

// [PL EMIO] - EMIO GPIO Width=8 설정 필요
#define LED_OUTPUT_PIN  (54 + 0) // 54번: 출력 LED (LD0)

#define INPUT_BTN       (54 + 1) // 55번: 입력 버튼 (BTN)
#define LD5_R_PIN       (54 + 2) // 56
#define LD5_G_PIN       (54 + 3) // 57
#define LD5_B_PIN       (54 + 4) // 58

#define LD6_R_PIN       (54 + 5) // 59
#define LD6_G_PIN       (54 + 6) // 60
#define LD6_B_PIN       (54 + 7) // 61

// 하드웨어 ID (SDT 방식에서는 BASEADDR 사용)
#define GPIO_BASE_ADDR  XPAR_XGPIOPS_0_BASEADDR



// Xilinx UART 드라이버 ID (xparameters.h에서 확인)


#ifdef __cplusplus
}
#endif
#endif
