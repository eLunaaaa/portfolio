# Technical notes

## 보고서 설명과 코드의 차이

- FPGA 플랫폼을 사용하지만 모델 실행 위치는 ARM PS이다. PL CNN 가속기는 없다.
- Zybo UART 수신은 1 tick 주기 polling + queue이며 interrupt 구현은 아니다.
- 전처리 원 코드는 nearest-neighbor인데 보고서는 bilinear라고 설명했다. 공개본은 half-pixel bilinear 구현을 사용한다. 실제 TensorFlow 결과와의 수치 비교는 모델·환경 확보 후 필요하다.
- 원 코드의 180도 회전 주석과 달리 픽셀 좌표 계산에는 회전이 없다. 공개본도 회전하지 않는다.
- 모델 입력·출력은 float이다. UART debug용 int8 배열은 모델 양자화가 아니다.
- 원 코드 `SERVO_HOLD_MS=5000`은 물체가 사라질 때까지 기다리는 동작이 아니다.
- Arduino 2는 Timer1 기반 시간 측정으로 보완했다. 원 코드의 loop 횟수와 `_delay_us(1)` 누적은 실제 echo 시간과 다르다.
- 레지스터 접근 자체나 task 대기 자체만으로 전력 절감률을 입증할 수 없다. 전력·에너지의 실측 결과는 없다.

## 모델 구성

MobileNetV2 alpha 0.5, RGB 128×128, 5개 클래스를 사용했다. 모델 파라미터는 876,469개이며 학습 결과 accuracy는 0.7229이다. 모델 입력·출력은 FLOAT32이고, 클래스 순서는 can/general/glass/pet/empty이다.

## 메모리와 실행 시간

Tensor arena는 4 MiB로 설정했다. 공유 JPEG buffer 512 KiB 외에도 burst TX/RX 임시 버퍼, JPEG 디코딩 RGB 메모리, task stack, C heap과 RTOS heap을 함께 고려해야 한다. AI 이미지 디버그 UART 전송은 공개본에서 제거했다.

Arduino 1의 결과 대기 시간은 10초이며, 분류 결과에 따라 뚜껑을 5초 동안 연다.

## 외부 API와 보드 설정

SDT와 legacy BSP의 LookupConfig 인자는 서로 다르므로 공개본은 `SDT` 매크로로 base address/device ID를 구분한다. 실제 생성된 `xparameters.h`의 인스턴스와 driver 버전을 확인해야 한다.

SPI prescaler는 128이며 실제 SCK는 SPI 입력 clock / 128이다. 원 코드의 “50MHz / 8” 주석과 일치하지 않는다. SS0을 명시적으로 선택하고 force slave select를 사용해 burst 동안 CS를 유지한다. 전송 오류는 반환값으로 확인한다.

Arduino 1의 SoftwareSerial은 D7 RX/D8 TX, 115200 bps를 유지했다. Servo 라이브러리로 뚜껑을 제어한다. UNO 5 V GPIO와 Zybo/ESP8266의 GPIO 허용 전압에 맞는 인터페이스 구성이 필요하다.

## 참고 문서

- [AMD XSpiPs driver](https://github.com/Xilinx/embeddedsw/blob/master/XilinxProcessorIPLib/drivers/spips/src/xspips.h)
- [FreeRTOS binary semaphore](https://sources.freertos.org/Documentation/02-Kernel/02-Kernel-features/02-Queues-mutexes-and-semaphores/02-Binary-semaphores)
- [AVR delay와 F_CPU](https://www.nongnu.org/avr-libc/user-manual/group__util__delay.html)
- [ATmega328P USART interrupt 명칭](https://www.nongnu.org/avr-libc/user-manual/group__avr__interrupts.html)
- [Espressif TCP/IP AT commands](https://docs.espressif.com/projects/esp-at/en/release-v2.1.0.0_esp8266/AT_Command_Set/TCP-IP_AT_Commands.html)
