#include "peripheral.h"
#include "arducam.h"
#include "xil_printf.h"
#include "FreeRTOS.h"
#include "task.h"
#include "ov5642_regs.h"
#include <stdlib.h>
#include <string.h>
#include "xparameters.h"

static int wait_i2c_idle(void);
static int spi_failed;

// SPI 쓰기
void ArduCAM_WriteReg(u8 addr, u8 data) {
    u8 tx[2];
    tx[0] = addr | 0x80;
    tx[1] = data;
    if (XSpiPs_PolledTransfer(&Spi, tx, NULL, 2) != XST_SUCCESS) spi_failed = 1;
}

// SPI 읽기
u8 ArduCAM_ReadReg(u8 addr) {
    u8 tx[2], rx[2] = {0};
    tx[0] = addr & 0x7F;
    tx[1] = 0x00;
    if (XSpiPs_PolledTransfer(&Spi, tx, rx, 2) != XST_SUCCESS) {
        spi_failed = 1;
        return 0;
    }
    return rx[1];
}

// I2C 쓰기 (상세 디버깅)
int wrSensorReg16_8(u16 regID, u8 regDat) {
    u8 tx[3];
    int Status;

    tx[0] = (u8)(regID >> 8);
    tx[1] = (u8)(regID & 0xFF);
    tx[2] = regDat;

    // 전송 전 버스 확인
    if (wait_i2c_idle() != XST_SUCCESS) return XST_FAILURE;

    Status = XIicPs_MasterSendPolled(&Iic, tx, 3, OV5642_I2C_ADDR);

    if (Status != XST_SUCCESS) {
        // 실패 시 에러 코드 출력 (중요!)
        xil_printf("  -> [I2C Write Fail] Addr:0x%04X Data:0x%02X ErrorCode:%d\r\n", regID, regDat, Status);

    }

    return Status;
}

// I2C 읽기 (상세 디버깅)
int rdSensorReg16_8(u16 regID, u8* regDat) {
    u8 tx[2];
    int Status;

    tx[0] = (u8)(regID >> 8);
    tx[1] = (u8)(regID & 0xFF);

    if (wait_i2c_idle() != XST_SUCCESS) return XST_FAILURE;

    Status = XIicPs_MasterSendPolled(&Iic, tx, 2, OV5642_I2C_ADDR);
    if (Status != XST_SUCCESS) {
        xil_printf("  -> [I2C Read-Addr Fail] ErrorCode:%d\r\n", Status);
        return Status;
    }

    if (wait_i2c_idle() != XST_SUCCESS) return XST_FAILURE;

    Status = XIicPs_MasterRecvPolled(&Iic, regDat, 1, OV5642_I2C_ADDR);
    if (Status != XST_SUCCESS) {
        xil_printf("  -> [I2C Read-Data Fail] ErrorCode:%d\r\n", Status);
    }

    return Status;
}

int wrSensorRegs16_8(const struct sensor_reg reglist[]) {
    if (reglist == NULL) return XST_FAILURE;
    for (const struct sensor_reg *next = reglist;
         !(next->reg == 0xffff && next->val == 0xff); ++next) {
        int status = wrSensorReg16_8(next->reg, next->val);
        if (status != XST_SUCCESS) return status;
    }
    return XST_SUCCESS;
}

static int wait_i2c_idle(void) {
    TickType_t start = xTaskGetTickCount();
    while (XIicPs_BusIsBusy(&Iic)) {
        if ((TickType_t)(xTaskGetTickCount() - start) >= pdMS_TO_TICKS(100))
            return XST_FAILURE;
        vTaskDelay(1);
    }
    return XST_SUCCESS;
}

// I2C 버스 스캐너 (연결된 모든 장치 찾기)
void I2C_Scanner() {
    int Status;
    u8 buffer[1] = {0x00}; // 더미 데이터
    int found = 0;

    xil_printf("\r\n--- I2C Bus Scanner Start ---\r\n");
    xil_printf("Scanning...");

    // 7비트 주소 범위 (0x01 ~ 0x7F) 스캔
    for (u8 addr = 1; addr < 128; addr++) {
        // 해당 주소로 1바이트 쓰기 시도
        Status = XIicPs_MasterSendPolled(&Iic, buffer, 1, addr);
        if (wait_i2c_idle() != XST_SUCCESS) return; // 완료 대기

        if (Status == XST_SUCCESS) {
            xil_printf("\r\n[!] Device Found at Address: 0x%02X", addr);
            if (addr == 0x3C) xil_printf(" (Possibly OV5642)");
            found++;
        }
    }

    if (found == 0) {
        xil_printf("\r\n[Error] No I2C devices found.\r\n");
        xil_printf("Check SDA/SCL wiring and Pull-up resistors.\r\n");
    } else {
        xil_printf("\r\n--- Scan Complete. Found %d device(s) ---\r\n", found);
    }
}

int ArduCAM_Init() {
    u8 vid = 0, pid = 0;
    spi_failed = 0;

    xil_printf("\r\n[ArduCAM] === Initialization Start ===\r\n");

    ArduCAM_WriteReg(0x07, 0x80);
    vTaskDelay(pdMS_TO_TICKS(100));
    ArduCAM_WriteReg(0x07, 0x00);
    vTaskDelay(pdMS_TO_TICKS(100));

    // 1. SPI 통신 확인
    ArduCAM_WriteReg(ARDUCHIP_TEST1, 0x55);
    u8 test_val = ArduCAM_ReadReg(ARDUCHIP_TEST1);
    if (test_val != 0x55) {
        xil_printf("[Error] SPI Bus Fail! Read: 0x%02X\r\n", test_val);
        return XST_FAILURE;
    }


    // 2. I2C 센서 확인
    if (wrSensorReg16_8(0x00ff, 0x01) != XST_SUCCESS) return XST_FAILURE;
    if (rdSensorReg16_8(0x300A, &vid) != XST_SUCCESS) return XST_FAILURE;
    if (rdSensorReg16_8(0x300B, &pid) != XST_SUCCESS) return XST_FAILURE;
    xil_printf("Sensor ID: 0x%02X%02X (I2C OK)\r\n", vid, pid);
    if (vid != 0x56 || pid != 0x42) return XST_FAILURE;

    //arducam.initCAM with JPG mode
    if (wrSensorReg16_8(0x3008, 0x80) != XST_SUCCESS) return XST_FAILURE;
    if (wrSensorRegs16_8(OV5642_QVGA_Preview) != XST_SUCCESS) return XST_FAILURE;
    vTaskDelay(pdMS_TO_TICKS(100));

    vTaskDelay(pdMS_TO_TICKS(100));
    if (wrSensorRegs16_8(OV5642_JPEG_Capture_QSXGA) != XST_SUCCESS) return XST_FAILURE;
    if (wrSensorRegs16_8(ov5642_320x240) != XST_SUCCESS) return XST_FAILURE;
    vTaskDelay(pdMS_TO_TICKS(100));
    if (wrSensorReg16_8(0x3818, 0xa8) != XST_SUCCESS) return XST_FAILURE;
    if (wrSensorReg16_8(0x3621, 0x10) != XST_SUCCESS) return XST_FAILURE;
    if (wrSensorReg16_8(0x3801, 0xb0) != XST_SUCCESS) return XST_FAILURE;
    if (wrSensorReg16_8(0x4407, 0x08) != XST_SUCCESS) return XST_FAILURE;
    if (wrSensorReg16_8(0x5888, 0x00) != XST_SUCCESS) return XST_FAILURE;
    if (wrSensorReg16_8(0x5000, 0xFF) != XST_SUCCESS) return XST_FAILURE;

    ArduCAM_WriteReg(ARDUCHIP_TIM, VSYNC_LEVEL_MASK);   // VSYNC is active HIGH

    //OV5642_set_JPEG_size(OV5642_320x240)
    if (wrSensorRegs16_8(ov5642_320x240) != XST_SUCCESS) return XST_FAILURE;
    vTaskDelay(pdMS_TO_TICKS(1000));

    //myCAM.clear_fifo_flag();
    ArduCAM_WriteReg(ARDUCHIP_FIFO, FIFO_CLEAR_MASK); // 0x01

    ArduCAM_WriteReg(ARDUCHIP_FRAMES,0x00);
    if (spi_failed) return XST_FAILURE;
    xil_printf("[Success] Sensor Init Done.\r\n");
    return XST_SUCCESS;
}

int ArduCAM_Capture() {
    spi_failed = 0;
    // 1. FIFO 초기화
    ArduCAM_WriteReg(ARDUCHIP_FIFO, FIFO_CLEAR_MASK); // 0x01

    // 2. [중요] 센서가 이미지를 뿜어낼 시간을 줌 (Dummy Frame)
    // 설정 직후 바로 캡처하면 센서가 아직 준비 안 됐을 수 있음
    ArduCAM_WriteReg(ARDUCHIP_FIFO, FIFO_START_MASK); // 0x02
    xil_printf("[ArduCAM] Capture Command Sent.\r\n");

    // 3. 완료 대기 (타임아웃 늘림)
    TickType_t capture_start = xTaskGetTickCount();
    u8 status = 0;
    while (1) {
        status = ArduCAM_ReadReg(ARDUCHIP_TRIG); // 0x41

        if (spi_failed) return XST_FAILURE;
        // Bit 3 (0x08)이 1이면 캡처 완료
        if (status & CAP_DONE_MASK) {
            xil_printf("[ArduCAM] Capture Done! (Status: 0x%02X)\r\n", status);
            break;
        }

        vTaskDelay(pdMS_TO_TICKS(100));

        // 5초 이상 안 되면 타임아웃
        if ((TickType_t)(xTaskGetTickCount() - capture_start) >= pdMS_TO_TICKS(5000)) {
            xil_printf("[Error] Capture Timeout. Sensor not outputting data.\r\n");
            return XST_FAILURE;
        }
    }
    return XST_SUCCESS;
}

// 캡처된 이미지 크기 읽기
u32 ArduCAM_ReadFifoLength() {
    u32 len1, len2, len3;
    len1 = ArduCAM_ReadReg(FIFO_SIZE1);
    len2 = ArduCAM_ReadReg(FIFO_SIZE2);
    len3 = ArduCAM_ReadReg(FIFO_SIZE3);
    if (spi_failed) return 0;
    return ((len3 << 16) | (len2 << 8) | len1) & 0x07fffff;
}

// 이미지 데이터 읽기 (Burst Read)
int ArduCAM_ReadFifoBurst(u8 *buffer, u32 length) {
    if (buffer == NULL || length == 0 || length > 512u * 1024u)
        return XST_FAILURE;
    u8 *tx = (u8 *)calloc(length + 1u, 1);
    u8 *rx = (u8 *)malloc(length + 1u);
    if (tx == NULL || rx == NULL) {
        free(tx);
        free(rx);
        return XST_FAILURE;
    }
    tx[0] = BURST_FIFO_READ;
    int status = XSpiPs_PolledTransfer(&Spi, tx, rx, length + 1u);
    if (status == XST_SUCCESS) memcpy(buffer, rx + 1, length);
    free(tx);
    free(rx);
    return status;
}
