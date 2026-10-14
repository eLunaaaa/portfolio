# 하드웨어 구성

STM32F103 보드 세 대가 Master, Slave1, Slave2 역할을 나누어 수행합니다.

| 보드 | 연결 장치 | 제어·통신 |
|---|---|---|
| Slave1 | 습도·수위·IR 센서, 팬, 승강 서보 | ADC DMA, GPIO, TIM2/TIM3 PWM, SPI1 Slave |
| Master | Slave1·Slave2 | SPI1 DMA Master, SPI2 polling Master |
| Slave2 | ESP8266, LCD, 수위 표시 서보, LED | UART, I2C, PWM, SPI Slave |

보드별 핀과 clock 설정은 [기술 노트](../docs/TECHNICAL_NOTES.md)에 정리되어 있습니다. 각 source 파일 첫머리의 개요와 함수 설명에서 센서 입력이 통신 frame과 구동 명령으로 연결되는 흐름을 확인할 수 있습니다.
