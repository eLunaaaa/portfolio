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

static int setup_uart(XUartPs *uart, XUartPs_Config *config, u32 baud) {
    if (!config) return XST_FAILURE;
    int status = XUartPs_CfgInitialize(uart, config, config->BaseAddress);
    if (status != XST_SUCCESS) return status;
    XUartPs_SetOperMode(uart, XUARTPS_OPER_MODE_NORMAL);
    return XUartPs_SetBaudRate(uart, baud);
}
int Setup_UART(u32 baud) {
    return setup_uart(&Uart_Ps, XUartPs_LookupConfig(PC_UART_LOOKUP), baud);
}
int Setup_Arduino_UART(u32 baud) {
    return setup_uart(&Arduino_Uart, XUartPs_LookupConfig(ARDUINO_UART_LOOKUP), baud);
}
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
int Send_Byte_Arduino(u8 data) { return send_byte(&Arduino_Uart, data); }
void Send_To_Arduino(const char *message) {
    while (*message) if (Send_Byte_Arduino((u8)*message++) != XST_SUCCESS) return;
}
int Uart_Send_Raw_Data(u8 *data, u32 size) {
    if (!data) return XST_FAILURE;
    for (u32 i = 0; i < size; ++i)
        if (send_byte(&Uart_Ps, data[i]) != XST_SUCCESS) return XST_FAILURE;
    return XST_SUCCESS;
}
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
int Setup_I2C(void) {
    XIicPs_Config *config = XIicPs_LookupConfig(IIC_LOOKUP);
    if (!config) return XST_FAILURE;
    int status = XIicPs_CfgInitialize(&Iic, config, config->BaseAddress);
    if (status != XST_SUCCESS) return status;
    status = XIicPs_SelfTest(&Iic);
    if (status != XST_SUCCESS) return status;
    return XIicPs_SetSClk(&Iic, 50000);
}
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
void ColorLED(uint8_t color) {
    XGpioPs_WritePin(&Gpio, LD6_R_PIN, !!(color & 1));
    XGpioPs_WritePin(&Gpio, LD6_G_PIN, !!(color & 2));
    XGpioPs_WritePin(&Gpio, LD6_B_PIN, !!(color & 4));
}
