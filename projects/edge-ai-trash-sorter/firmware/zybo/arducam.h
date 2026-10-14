/*
 * CameraTask가 사용할 ArduCAM 제어 상수와 함수 선언입니다.
 * SPI FIFO 제어 bit와 OV5642 I2C 주소를 정의하며 구현은 arducam.c에 있습니다.
 */

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
/**
 * 기능: SPI 제어칩과 OV5642 센서를 JPEG 촬영 상태로 초기화합니다.
 * 입력: 없음. 초기화된 SPI/I2C 인스턴스와 센서 설정 테이블을 사용합니다.
 * 출력: 초기화 성공이면 XST_SUCCESS, 통신·센서 ID·설정 오류이면 XST_FAILURE.
 * 처리 구조: 리셋→SPI 테스트→센서 ID 확인→JPEG/해상도 레지스터→FIFO/프레임 설정 순서로 진행합니다.
 */
int ArduCAM_Init();
/**
 * 기능: 카메라 촬영을 시작하고 FIFO 준비 완료를 기다립니다.
 * 입력: 없음. 초기화된 카메라를 사용합니다.
 * 출력: 완료되면 XST_SUCCESS, 통신 오류·5초 timeout이면 XST_FAILURE.
 * 처리 구조: FIFO를 비우고 capture bit를 설정한 뒤 CAP_DONE을 tick 기준으로 확인합니다.
 */
int ArduCAM_Capture();
/**
 * 기능: 캡처된 JPEG의 FIFO 바이트 수를 읽습니다.
 * 입력: 없음. 카메라 FIFO 크기 레지스터를 사용합니다.
 * 출력: 3개 레지스터를 조합한 길이, SPI 오류 시 0.
 * 처리 구조: low/middle/high 바이트를 읽고 지원하는 길이 비트 마스크를 적용합니다.
 */
u32 ArduCAM_ReadFifoLength();
/**
 * 기능: 카메라 FIFO의 압축 영상을 연속 SPI 전송으로 가져옵니다.
 * 입력: buffer: 출력 JPEG 공간, length: 읽을 바이트 수(최대 512 KiB).
 * 출력: 전송 상태 코드와 성공 시 buffer에 복사한 JPEG.
 * 처리 구조: 명령+dummy TX와 RX 임시 공간을 준비하고 하나의 전송으로 읽습니다. 명령 응답 바이트를 제외해 복사하고 양쪽 임시 공간을 해제합니다.
 */
int ArduCAM_ReadFifoBurst(u8 *buffer, u32 length);

#ifdef __cplusplus
}
#endif
#endif /* ARDUCAM_H_ */
