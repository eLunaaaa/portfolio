# Portfolio

전자공학 기반의 임베디드 시스템 프로젝트 포트폴리오입니다.

## Edge AI Trash Sorter · 엣지 AI 쓰레기 분류 시스템

카메라 기반 AI 분류와 쓰레기통 자동 개방, 적재량 모니터링을 통합한 팀 프로젝트입니다.

- **개인 기여**: 시스템 설계·통합, Zybo 하드웨어·카메라, AI 학습·변환, AI 실행·FreeRTOS, Arduino 적재량·Wi-Fi, Flask 서버·웹 대시보드
- **공동 작업**: Arduino 물체 감지·뚜껑·LCD 제어
- **기술**: C/C++ · Zybo Z7-20 · FreeRTOS · TensorFlow Lite Micro · MobileNetV2 · Arduino · ESP8266 · Flask
- **문제 해결**: 촬영 요청과 AI 완료 신호 분리, JPEG·tensor 검증, 적재량 히스테리시스, 전송 실패 재시도
- **공개본 검증**: Arduino 1/2 컴파일, C 호스트 테스트·ASan/UBSan, Flask API 테스트 5개 통과. Zybo 전체 빌드·실보드는 미검증

[프로젝트 상세 및 시연](projects/edge-ai-trash-sorter/README.md) · [코드 검토 결과](projects/edge-ai-trash-sorter/docs/CODE_REVIEW.md) · [시스템 구조](projects/edge-ai-trash-sorter/docs/ARCHITECTURE.md)

## Smart Umbrella Stand · 스마트 우산꽂이

센서 데이터와 날씨 정보를 결합해 우산 건조·승강·상태 표시를 자동화한 팀 프로젝트입니다.

- **개인 기여**: 아이디어 제안, Master STM32 제어·SPI 통신, Slave2 날씨 정보·LCD·상태 표시 담당
- **기술**: C · STM32F103 · SPI · ADC/DMA · PWM · Python · 경량 로지스틱 회귀
- **문제 해결**: 명시적 통신 프레임, 타임아웃, 상태 히스테리시스, 날짜 rollover 처리
- **성능 기록**: 보고서의 센서 처리 구간 기준 2,264 → 1,171 cycles, 약 48.3% 감소
- **공개본 검증**: C 호스트 테스트·ASan/UBSan 및 Python 학습 파이프라인 통과. 실보드 전체 빌드는 미검증

[프로젝트 상세 및 시연 영상](projects/smart-umbrella-stand/README.md) · [코드 검토 결과](projects/smart-umbrella-stand/docs/CODE_REVIEW.md) · [시스템 구조](projects/smart-umbrella-stand/docs/ARCHITECTURE.md)

공개본은 핵심 로직 모듈을 정리한 자료입니다. 전체 시제품 실행 코드와 실제 학습 모델은 포함되어 있지 않습니다.
