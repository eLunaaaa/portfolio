# 구현 설정

## 연산과 전처리

AI 모델은 Zybo PS의 ARM 프로세서에서 실행합니다. JPEG는 RGB로 디코딩한 뒤 half-pixel bilinear 방식으로 128×128에 맞추고 [-1,1]로 정규화합니다. 입력·출력 tensor는 FLOAT32입니다.

MobileNetV2 alpha 0.5와 5개 클래스 can/general/glass/pet/empty를 사용합니다. 모델 파라미터는 876,469개이며 학습 결과 accuracy는 0.7229입니다.

## Task와 메모리

CameraTask는 촬영 요청을 기다리고 AI task는 완전한 JPEG가 준비된 뒤 추론합니다. data-ready와 AI-done semaphore가 하나의 공유 JPEG 버퍼 사용 순서를 정합니다. UART 수신은 polling task가 FIFO에서 바이트를 읽어 queue로 전달합니다.

Tensor arena는 4 MiB, 공유 JPEG buffer는 512 KiB입니다. burst TX/RX 임시 버퍼와 RGB 디코딩 공간은 해당 처리 이후 해제합니다.

## 시간과 표시

Arduino 1의 결과 대기는 10초이며 뚜껑 유지 시간은 5초입니다. Arduino 2는 Timer1을 거리 측정과 응답 timeout의 공통 시간 기준으로 사용합니다. 초음파 거리의 포화 상태는 2/4 cm 히스테리시스로 판정합니다.

## 주변장치와 빌드

peripheral.c는 SDT/legacy BSP에서 사용하는 LookupConfig 인자를 구분합니다. SPI prescaler는 128이고 SCK는 SPI 입력 clock/128입니다. SS0과 force slave-select를 사용해 FIFO burst 전송 동안 선택 상태를 유지합니다.

Arduino 1의 SoftwareSerial은 D7 RX/D8 TX, 115200 bps입니다. Servo 라이브러리로 뚜껑을 제어합니다. 자세한 pin 연결은 hardware/README.md에 있습니다.
