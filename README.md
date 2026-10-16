# Portfolio

전자공학 기반의 임베디드 시스템 프로젝트 포트폴리오입니다.

## Blue Witness · 다중 시점 사고 증거 수집 서비스

여러 Raspberry Pi 차량에서 사고 전후 영상을 확보하고, AI로 필요한 목격 영상을 선별해 시간축을 맞춰 웹에서 확인하는 팀 프로젝트입니다.

- **개인 기여**: 카메라·AI 실행, 차량 검출 모델 학습 파이프라인, FastAPI 서버·웹, Chrony 상태 IPC, GitHub Actions CI·Pi 수동 배포 흐름 구축
- **공동 작업**: 데이터 라벨 검수, 프로세스·통신 규약 통합, 실제 차량 시나리오 검증. 센서·V2V·위치 필터·업로더는 팀 모듈과 연동
- **기술**: C/C++ · Raspberry Pi 5 · libcamera · YOLOX-Tiny · PyTorch/MPS · ONNX Runtime · FFmpeg · FastAPI · SQLite · Chrony · GitHub Actions
- **평가 기록**: 전체 BBox 검증 12,873프레임의 대상 차종 유무 정답률 98.39%; 별도 test 묶음 1,633프레임에서 98.65%
- **팀 통합 검증**: 테스트베드 실제 충돌 27회 시나리오 성공, 사고·목격 영상 81건 서버 저장 확인
- **공개 코드 재검증**: 서버 Python 72개·웹 JavaScript 51개·모델 실행 계약 4개 테스트 및 시나리오 CSV 검증 통과

[프로젝트 상세 및 기여](projects/blue-witness/README.md) · [모델 학습](projects/blue-witness/ml/README.md) · [코드·기여 근거](projects/blue-witness/docs/CODE_GUIDE.md) · [검증 및 한계](projects/blue-witness/docs/CODE_REVIEW.md)

## Configurable CNN Training and RTL · CNN 학습·RTL 자동화

MNIST 모델 구조·학습부터 Q3.4 출력 평가, Verilog 연산 모듈, RTL 생성·합성·분석 자동화까지 직접 구현했습니다.

- **개인 기여**: 전체 구현
- **기술**: Python · PyTorch · JSON · Verilog/SystemVerilog · Icarus Verilog · sv2v · Yosys
- **학습 평가 기록**: baseline 85.99%, 최종 구성 97.48%

[프로젝트 상세와 코드](projects/configurable-cnn-rtl/README.md)

## Embedded Linux Experiment Setup · 졸업연구 실험 환경 구성

실험 환경과 PREEMPT_RT 도구 설정, CPU 3번 고정, 논문 리딩·정리를 담당했습니다.

- **개인 기여**: 실험 세팅, PREEMPT_RT 실험 도구 설정, CPU affinity 적용, 관련 논문 정리
- **기술·도구**: Embedded Linux · PREEMPT_RT · cyclictest · taskset · CPU affinity · Shell

[담당 작업과 기술 구현](projects/embedded-linux-system-programming/README.md)

## Arduino Dynamic Load Balancer · 동적 부하 분산 시스템

Arduino UNO 5대로 합성곱 연산을 분산 처리하고, Slave별 연산 잔량을 서보로 시각화한 팀 프로젝트입니다.

- **개인 기여**: Master 부하 계산·스케줄링, I²C·완료 인터럽트, Slave 연산·큐, PWM·UART·타이머
- **공동 작업**: 아이디어·설계·통합, 실험·성능 분석·시연
- **기술**: C/C++ · ATmega328P · I²C/TWI · UART · INT/PCINT · Timer/PWM · Python
- **문제 해결**: zero-skip, 미처리 부하 기반 배정, 이중 버퍼, 결과 ACK와 signed 32비트 연산
- **성과**: 보고서의 시연 지연 포함 조건에서 순차 234초 → 분산 80.5초, 약 2.91배
- **검증**: 실보드 시연 기록, Master·Slave 4개 주소 컴파일, 1,800개 패치의 파이프라인·호스트 테스트 통과

[프로젝트 상세 및 시연](projects/arduino-load-balancer/README.md) · [코드 검토 결과](projects/arduino-load-balancer/docs/CODE_REVIEW.md) · [통신 규격](projects/arduino-load-balancer/docs/PROTOCOL.md)

## Edge AI Trash Sorter · 엣지 AI 쓰레기 분류 시스템

카메라 기반 AI 분류와 쓰레기통 자동 개방, 적재량 모니터링을 통합한 팀 프로젝트입니다.

- **개인 기여**: 시스템 설계·통합, Zybo 하드웨어·카메라, AI 학습·변환, AI 실행·FreeRTOS, Arduino 적재량·Wi-Fi, Flask 서버·웹 대시보드
- **공동 작업**: Arduino 물체 감지·뚜껑·LCD 제어
- **기술**: C/C++ · Zybo Z7-20 · FreeRTOS · TensorFlow Lite Micro · MobileNetV2 · Arduino · ESP8266 · Flask
- **문제 해결**: 촬영 요청과 AI 완료 신호 분리, JPEG·tensor 검증, 적재량 히스테리시스, 전송 실패 재시도
- **공개본 검증**: Arduino 1/2 컴파일, C 호스트 테스트·ASan/UBSan, Flask API 테스트 5개 통과. 프로젝트 실보드 연동·실행 완료

[프로젝트 상세 및 시연](projects/edge-ai-trash-sorter/README.md) · [코드 검토 결과](projects/edge-ai-trash-sorter/docs/CODE_REVIEW.md) · [시스템 구조](projects/edge-ai-trash-sorter/docs/ARCHITECTURE.md)

## Smart Umbrella Stand · 스마트 우산꽂이

센서 데이터와 날씨 정보를 결합해 우산 건조·승강·상태 표시를 자동화한 팀 프로젝트입니다.

- **개인 기여**: 아이디어 제안, Master STM32 제어·SPI 통신, Slave2 날씨 정보·LCD·상태 표시 담당
- **기술**: C · STM32F103 · SPI · ADC/DMA · PWM · Python · 경량 로지스틱 회귀
- **문제 해결**: 명시적 통신 프레임, 타임아웃, 상태 히스테리시스, 날짜 rollover 처리
- **성능 기록**: 보고서의 센서 처리 구간 기준 2,264 → 1,171 cycles, 약 48.3% 감소
- **공개본 검증**: C 호스트 테스트·ASan/UBSan 및 Python 학습 파이프라인 통과

[프로젝트 상세 및 시연 영상](projects/smart-umbrella-stand/README.md) · [코드 검토 결과](projects/smart-umbrella-stand/docs/CODE_REVIEW.md) · [시스템 구조](projects/smart-umbrella-stand/docs/ARCHITECTURE.md)
