# 코드 읽기 안내

이 프로젝트는 카메라 분류와 Arduino 뚜껑 제어, 적재량 전송과 웹 관제를 연결합니다. 영상 분류 경로와 적재량 보고 경로가 나뉘어 있습니다.

## 카메라·분류 경로

| 파일 | 담당 역할 | 먼저 볼 함수 |
|---|---|---|
| [Zybo 진입점](../firmware/zybo/main.cc) | RTOS 객체·task 연결, 촬영 요청 처리 | main → CameraTask, ArduinoProcessingTask/ArduinoTxTask |
| [카메라 어댑터](../firmware/zybo/arducam.c) | SPI/I2C 초기화, JPEG 캡처·FIFO 읽기 | ArduCAM_Init → Capture → ReadFifoLength/ReadFifoBurst |
| [AI task](../firmware/zybo/ai_task.cc) | tensor 준비와 모델 실행 | vAI_InferenceTask → ProcessImage |
| [영상 공통 로직](../firmware/common/vision_core.c) | JPEG 검사, RGB 변환, 결과 명령 | jpeg_trim_padding, rgb_resize_normalize, classification_command |
| [주변장치](../firmware/zybo/peripheral.c) | BSP 인스턴스와 UART/GPIO/SPI/I2C 연결 | Setup_*와 Send_Byte_Arduino |
| [뚜껑 Arduino](../firmware/arduino_lid/arduino_lid.ino) | 물체 감지·분류 요청·서보/LCD | setup → loop → moveServoIdx/displayClassOnLCD |

물체 감지 → UART 1 → capture-request → JPEG 준비 → data-ready → AI 입력/추론 → TX queue → UART 결과 → 해당 뚜껑 개방입니다. AI-done은 JPEG buffer를 다음 촬영에 재사용할 수 있다는 신호입니다.

## 적재량·관제 경로

| 파일 | 담당 역할 | 먼저 볼 함수 |
|---|---|---|
| [적재량 Arduino](../firmware/arduino_monitor/main.c) | 센서 4개·ESP8266·상태 재전송 | main → Measure_Distance → Send_HTTP_Request |
| [Flask 서버](../server/app.py) | 위치·종류별 상태와 API | create_app → control_bin/status |
| [화면](../server/templates/index.html) | 상태 조회·아이콘·상세창 | updateDashboard → updateIcon/renderModalContent |

거리 → fill/empty 판정 → ESP8266 HTTP → 서버 data 갱신 → /status JSON → 화면 표시입니다. None은 아직 수신하지 않은 상태, false는 여유, true는 수거 필요를 뜻합니다.

각 파일 개요와 함수별 기능·입력·출력·처리 구조 주석을 먼저 읽으면 하드웨어 API를 몰라도 상위 흐름부터 따라갈 수 있습니다.
