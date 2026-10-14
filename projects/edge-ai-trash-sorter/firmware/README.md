# Firmware integration

## Zybo

`zybo/main.cc`와 `zybo/ai_task.cc`는 C++로, `zybo/arducam.c`, `zybo/peripheral.c`, `common/vision_core.c`는 C로 빌드한다. 헤더에는 C linkage guard를 적용했다. Vivado에서 PS GPIO/EMIO, SPI0, I2C0, UART0/1과 clock을 확인하고 Vitis에서 FreeRTOS BSP를 생성한다.

보드 프로젝트를 구성할 때 다음 의존성을 함께 연결한다.

- 하드웨어 플랫폼 `.xsa`, bitstream, BSP와 `xparameters.h`, linker script
- FreeRTOS kernel과 board port, FreeRTOSConfig.h
- TensorFlow Lite Micro와 모델 header `model_data.h` (`g_model` 배열)
- `ov5642_regs.h`: struct sensor_reg 및 OV5642 설정 table
- `stb_image.h`와 해당 implementation translation unit

실제 모델은 FLOAT32 [1,128,128,3] 입력과 FLOAT32 5개 출력, can/general/glass/pet/empty 순서가 필요하다. 모델 operator와 resolver를 같은 버전으로 구성한다. kernel version에 따른 resolver/interpreter 생성 API도 확인한다.

BSP의 SDT 여부와 `xparameters.h` 인스턴스를 peripheral.c의 lookup 설정에 맞춘다. SPI SS0을 사용하며, 모든 ArduCAM 접근은 CameraTask가 맡는다. C heap·RTOS heap·4 MiB tensor arena·JPEG/임시 버퍼·task stack이 메모리에 들어가는지 linker와 측정 도구로 확인한다. Task 생성 실패 시 스케줄러를 시작하지 않는다.

## Arduino 1

UNO AVR sketch이며 물체 감지(D12 trig, D4 echo), Zybo SoftwareSerial(D7 RX/D8 TX), Servo(D11/D9/D5/D6), I2C LCD(주소 0x27)를 사용한다. Arduino AVR core, Servo, LiquidCrystal I2C가 필요하다. 분류 오류는 문자 `6`으로 처리한다. Timeout 10초, 뚜껑 유지 시간 5초다.

## Arduino 2

독립 ATmega328P C 프로그램이다. Arduino sketch의 setup/loop가 아니라 자체 main을 사용한다. `secrets.example.h`를 `secrets.h`로 복사하고 로컬 값을 입력한다. Timer1 prescaler 8과 overflow interrupt로 microsecond 단위 기준시각을 만든다. UART는 9600 bps이며 ESP8266 AT firmware도 이에 맞춰야 한다.

센서 pin 정보는 hardware/README.md에 있다. 상태 전송은 t1 기본값을 사용하며 ZONE_ID로 변경한다. 같은 장치를 여러 zone으로 동시에 등록하지 않는다.
