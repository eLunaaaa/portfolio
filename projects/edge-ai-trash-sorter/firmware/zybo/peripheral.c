/*
 * Zybo PS의 UART·GPIO·I2C·SPI를 실제 BSP에 연결하는 어댑터입니다.
 * main.cc의 시작 단계에서 초기화하고 task 실행 중 byte 송신·LED 표시를 제공합니다.
 * SDT/legacy BSP의 설정 조회 인자를 분리해 같은 상위 코드가 인터페이스를 사용합니다.
 */

/* Board-dependent adapter. Validate IDs, clocks and pin maps in your BSP. */
#include "FreeRTOS.h"
#include "task.h"
#include "xparameters.h"
#include "peripheral.h"
#include <stddef.h>

XUartPs Uart_Ps, Arduino_Uart;
XGpioPs Gpio;
XSpiPs Spi;
XIicPs Iic;

#ifdef SDT
#define PC_UART_LOOKUP XPAR_XUARTPS_1_BASEADDR
#define ARDUINO_UART_LOOKUP XPAR_XUARTPS_0_BASEADDR
#define GPIO_LOOKUP XPAR_XGPIOPS_0_BASEADDR
#define SPI_LOOKUP XPAR_XSPIPS_0_BASEADDR
#define IIC_LOOKUP XPAR_XIICPS_0_BASEADDR
#else
#define PC_UART_LOOKUP XPAR_XUARTPS_1_DEVICE_ID
#define ARDUINO_UART_LOOKUP XPAR_XUARTPS_0_DEVICE_ID
#define GPIO_LOOKUP XPAR_XGPIOPS_0_DEVICE_ID
#define SPI_LOOKUP XPAR_XSPIPS_0_DEVICE_ID
#define IIC_LOOKUP XPAR_XIICPS_0_DEVICE_ID
#endif

/**
 * 기능: 지정 UART 인스턴스의 기본 설정·보레이트를 적용합니다.
 * 입력: uart: 인스턴스, config: BSP 설정, baud: 보레이트.
 * 출력: 초기화·보레이트 설정의 상태 코드.
 * 처리 구조: config 포인터를 확인하고 CfgInitialize→normal mode→SetBaudRate 순으로 설정합니다.
 */
static int setup_uart(XUartPs *uart, XUartPs_Config *config, u32 baud) {
    if (!config) return XST_FAILURE;
    int status = XUartPs_CfgInitialize(uart, config, config->BaseAddress);
    if (status != XST_SUCCESS) return status;
    XUartPs_SetOperMode(uart, XUARTPS_OPER_MODE_NORMAL);
    return XUartPs_SetBaudRate(uart, baud);
}
/**
 * 기능: PC 디버그용 UART를 설정합니다.
 * 입력: baud: 디버그 UART 보레이트.
 * 출력: UART 설정 상태 코드.
 * 처리 구조: BSP의 PC UART 설정을 조회하고 공통 setup_uart를 호출합니다.
 */
int Setup_UART(u32 baud) {
    return setup_uart(&Uart_Ps, XUartPs_LookupConfig(PC_UART_LOOKUP), baud);
}
/**
 * 기능: Arduino 분류 명령용 UART를 설정합니다.
 * 입력: baud: 양쪽 보드에서 맞출 보레이트.
 * 출력: UART 설정 상태 코드.
 * 처리 구조: Arduino UART BSP 설정을 조회해 초기화하고 normal mode와 보레이트를 적용합니다.
 */
int Setup_Arduino_UART(u32 baud) {
    return setup_uart(&Arduino_Uart, XUartPs_LookupConfig(ARDUINO_UART_LOOKUP), baud);
}
/**
 * 기능: 선택한 UART TX FIFO에 바이트 하나를 넣습니다.
 * 입력: uart: 송신 UART, data: 송신 1바이트.
 * 출력: 성공이면 XST_SUCCESS, FIFO 대기 100 ms 초과이면 XST_FAILURE.
 * 처리 구조: TX FIFO full 여부를 확인하면서 task를 양보하고 공간이 생기면 FIFO 레지스터에 기록합니다.
 */
static int send_byte(XUartPs *uart, u8 data) {
    TickType_t start = xTaskGetTickCount();
    while (XUartPs_IsTransmitFull(uart->Config.BaseAddress)) {
        if ((TickType_t)(xTaskGetTickCount() - start) >= pdMS_TO_TICKS(100))
            return XST_FAILURE;
        vTaskDelay(1);
    }
    XUartPs_WriteReg(uart->Config.BaseAddress, XUARTPS_FIFO_OFFSET, data);
    return XST_SUCCESS;
}
/**
 * 기능: Arduino 명령 UART로 바이트 하나를 보냅니다.
 * 입력: data: ASCII 분류·오류 명령.
 * 출력: 공통 send_byte의 전송 상태 코드.
 * 처리 구조: 전역 Arduino_Uart 인스턴스로 bounded FIFO 송신을 수행합니다.
 */
int Send_Byte_Arduino(u8 data) { return send_byte(&Arduino_Uart, data); }
/**
 * 기능: Arduino UART로 NUL 종료 문자열을 순서대로 보냅니다.
 * 입력: message/Msg: 송신 문자열.
 * 출력: 문자열을 전송하며 오류가 생기면 즉시 종료합니다.
 * 처리 구조: 각 문자를 Send_Byte_Arduino로 전달합니다. 분류 task의 실제 결과 전송은 TX queue 경로를 사용합니다.
 */
void Send_To_Arduino(const char *message) {
    while (*message) if (Send_Byte_Arduino((u8)*message++) != XST_SUCCESS) return;
}
/**
 * 기능: PC UART에 바이트 배열을 그대로 보냅니다.
 * 입력: data/Data: 원본 배열, size/Size: 바이트 수.
 * 출력: 전체 성공이면 XST_SUCCESS, 포인터·전송 오류이면 XST_FAILURE.
 * 처리 구조: 줄바꿈 변환 없이 send_byte로 순서대로 전달해 binary 데이터 형태를 유지합니다.
 */
int Uart_Send_Raw_Data(u8 *data, u32 size) {
    if (!data) return XST_FAILURE;
    for (u32 i = 0; i < size; ++i)
        if (send_byte(&Uart_Ps, data[i]) != XST_SUCCESS) return XST_FAILURE;
    return XST_SUCCESS;
}
/**
 * 기능: 상태 LED와 버튼용 PS GPIO 방향을 설정합니다.
 * 입력: 없음. BSP의 GPIO 설정과 peripheral.h의 핀 상수를 사용합니다.
 * 출력: 조회·초기화 상태 코드.
 * 처리 구조: GPIO 인스턴스를 초기화하고 LED는 output/enable, 버튼 영역은 input으로 설정합니다.
 */
int Setup_GPIO(void) {
    XGpioPs_Config *config = XGpioPs_LookupConfig(GPIO_LOOKUP);
    if (!config) return XST_FAILURE;
    int status = XGpioPs_CfgInitialize(&Gpio, config, config->BaseAddr);
    if (status != XST_SUCCESS) return status;
    const u32 outputs[] = {LD4_PIN, LED_OUTPUT_PIN, LD6_R_PIN, LD6_G_PIN, LD6_B_PIN};
    for (unsigned i = 0; i < sizeof(outputs) / sizeof(outputs[0]); ++i) {
        XGpioPs_SetDirectionPin(&Gpio, outputs[i], 1);
        XGpioPs_SetOutputEnablePin(&Gpio, outputs[i], 1);
    }
    for (u32 pin = INPUT_BTN; pin <= LD5_B_PIN; ++pin)
        XGpioPs_SetDirectionPin(&Gpio, pin, 0);
    return XST_SUCCESS;
}
/**
 * 기능: 카메라 레지스터 제어용 PS I2C를 초기화합니다.
 * 입력: 없음. BSP의 I2C 설정을 사용합니다.
 * 출력: 조회·초기화·self-test·clock 설정 상태 코드.
 * 처리 구조: 설정 조회 후 인스턴스 초기화와 자가 검사를 수행하고 50 kHz로 설정합니다.
 */
int Setup_I2C(void) {
    XIicPs_Config *config = XIicPs_LookupConfig(IIC_LOOKUP);
    if (!config) return XST_FAILURE;
    int status = XIicPs_CfgInitialize(&Iic, config, config->BaseAddress);
    if (status != XST_SUCCESS) return status;
    status = XIicPs_SelfTest(&Iic);
    if (status != XST_SUCCESS) return status;
    return XIicPs_SetSClk(&Iic, 50000);
}
/**
 * 기능: ArduCAM 제어·FIFO 전송용 PS SPI를 초기화합니다.
 * 입력: 없음. BSP의 SPI 설정을 사용합니다.
 * 출력: 설정 단계의 상태 코드.
 * 처리 구조: Master/force slave-select, clock prescaler 128, ArduCAM SS0을 차례대로 설정합니다.
 */
int Setup_SPI(void) {
    XSpiPs_Config *config = XSpiPs_LookupConfig(SPI_LOOKUP);
    if (!config) return XST_FAILURE;
    int status = XSpiPs_CfgInitialize(&Spi, config, config->BaseAddress);
    if (status != XST_SUCCESS) return status;
    status = XSpiPs_SetOptions(&Spi, XSPIPS_MASTER_OPTION | XSPIPS_FORCE_SSELECT_OPTION);
    if (status != XST_SUCCESS) return status;
    status = XSpiPs_SetClkPrescaler(&Spi, XSPIPS_CLK_PRESCALE_128);
    if (status != XST_SUCCESS) return status;
    /* Explicitly select ArduCAM SS0; verify routing in the Vivado design. */
    return XSpiPs_SetSlaveSelect(&Spi, 0);
}
/**
 * 기능: 분류·오류 상태를 RGB LED 색 비트로 표시합니다.
 * 입력: color/colorIdx: bit0=red, bit1=green, bit2=blue.
 * 출력: RGB 출력 GPIO의 HIGH/LOW를 변경합니다.
 * 처리 구조: 세 비트를 분리해 대응하는 LED 핀에 기록합니다.
 */
void ColorLED(uint8_t color) {
    XGpioPs_WritePin(&Gpio, LD6_R_PIN, !!(color & 1));
    XGpioPs_WritePin(&Gpio, LD6_G_PIN, !!(color & 2));
    XGpioPs_WritePin(&Gpio, LD6_B_PIN, !!(color & 4));
}
