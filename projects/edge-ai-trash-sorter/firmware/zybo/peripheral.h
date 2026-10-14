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

int Setup_Arduino_UART(u32 BaudRate);
void Send_To_Arduino(const char *Msg);
int Send_Byte_Arduino(u8 Data);

int Setup_GPIO(void);
int Setup_I2C(void);
int Setup_SPI(void);
int Setup_UART(u32 BaudRate);

int Uart_Send_Raw_Data(u8 *Data, u32 Size);


#define COLOR_RED 1
#define COLOR_GREEN 2
#define COLOR_BLUE 4
#define COLOR_CYAN 6
#define COLOR_MAGENTA 5
#define COLOR_YELLOW 3

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
