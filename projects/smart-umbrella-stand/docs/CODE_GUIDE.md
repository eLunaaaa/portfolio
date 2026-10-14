# 코드 읽기 안내

이 프로젝트는 Slave1의 센서값과 Slave2의 날씨 정보를 Master가 통합해 팬·승강 명령을 결정하는 프로그램입니다. 공통 로직과 STM32 레지스터 어댑터를 나누었습니다.

| 파일 | 담당 역할 | 먼저 볼 함수 |
|---|---|---|
| [Master 제어](../firmware/master/Core/Src/control_logic.c) | 센서 보정, wet/rainy 상태, 팬·서보 명령 | control_update → model_score |
| [Master 통신](../firmware/master/Core/Src/stm32_transport.c) | SPI1 DMA/SPI2 polling 교환 | spi1_exchange_sensor_dma, spi2_exchange_weather |
| [센서 보드](../firmware/slave1/Core/Src/slave1_app.c) | 이동평균·센서 frame·팬/서보 | slave1_app_update → slave1_spi_exchange_polling → slave1_apply_commands |
| [날씨 보드 SPI](../firmware/slave2/Core/Src/slave2_spi.c) | NSS/RX 이벤트의 응답 상태 머신 | slave2_spi_on_select → slave2_spi_on_byte |
| [통신 규격](../firmware/common/src/protocol.c) | 6/4바이트 frame 변환·검증 | sensor_frame_encode/decode, weather_response_decode |
| [날짜 계산](../firmware/common/src/date_utils.c) | 윤년·월말·연말 시간 변환 | datetime_add_hours |
| [표본 수집](../ml/collect_wet_data.py) | UART feature와 label의 CSV 저장 | main |
| [모델 학습](../ml/train_wet_model.py) | 학습 데이터 → 계수 → C 헤더 | load_dataset → train → export_header |

## 한 제어 주기의 입력·출력

ADC/IR → Slave1App의 평균 센서 → SPI1 센서 frame → SensorData → control_update의 wet/rainy 판단 → ControlOutput → 다음 SPI 교환의 팬/서보 명령 → Slave1 PWM/GPIO.

날씨는 SPI2 frame의 강수확률·강수형태로 함께 전달됩니다. ControlState는 이전 평균과 히스테리시스 상태를 보관하고 ControlOutput은 이번에 계산한 명령을 보관합니다. 센서 판단과 실제 PWM 출력은 서로 다른 파일에서 수행합니다.

각 헤더는 구조체·상수·함수 인터페이스를, 같은 이름의 source는 실제 처리를 설명합니다. 파일 개요와 함수별 기능·입력·출력·처리 구조 주석을 같은 형식으로 작성했습니다.
