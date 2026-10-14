#ifndef ARDUCAM_H_
#define ARDUCAM_H_

#include "xil_types.h"
#ifdef __cplusplus
extern "C" {
#endif

// ArduCAM 레지스터 정의
// ArduChip 레지스터 (헤더에 없다면 여기 정의)
#define ARDUCHIP_TEST1       0x00
#define ARDUCHIP_MODE        0x02  // [중요] 모드 레지스터 주소
#define ARDUCHIP_FIFO        0x04  // FIFO 제어 레지스터
#define ARDUCHIP_TRIG        0x41  // 캡처 상태 확인용
#define FIFO_CLEAR_MASK      0x01
#define FIFO_START_MASK      0x02
#define CAP_DONE_MASK        0x08

#define FIFO_SIZE1           0x42  // 이미지 크기 (Low)
#define FIFO_SIZE2           0x43  // 이미지 크기 (Mid)
#define FIFO_SIZE3           0x44  // 이미지 크기 (High)

#define BURST_FIFO_READ      0x3C  // 버스트 읽기 명령

// I2C 주소 (OV5642)
#define OV5642_I2C_ADDR      0x3C

#define ARDUCHIP_FRAMES           0x01  //FRAME control register, Bit[2:0] = Number of frames to be captured                                                                        //On 5MP_Plus platforms bit[2:0] = 7 means continuous capture until frame buffer is full

#define ARDUCHIP_TIM            0x03  //Timming control
#define HREF_LEVEL_MASK         0x01  //0 = High active ,       1 = Low active
#define VSYNC_LEVEL_MASK        0x02  //0 = High active ,       1 = Low active
#define LCD_BKEN_MASK           0x04  //0 = Enable,                     1 = Disable
#define PCLK_DELAY_MASK         0x08  //0 = data no delay,      1 = data delayed one PCLK


// 함수 선언
int ArduCAM_Init();
int ArduCAM_Capture();
u32 ArduCAM_ReadFifoLength();
int ArduCAM_ReadFifoBurst(u8 *buffer, u32 length);

#ifdef __cplusplus
}
#endif
#endif /* ARDUCAM_H_ */
