# 졸업연구 · 실시간 Linux 실험 환경 구성

Raspberry Pi 5의 실시간 Linux 실험 환경과 UDP 기준 측정 도구를 구성한 팀 프로젝트입니다. 이 페이지는 직접 구현·설정한 기술 작업을 소개합니다.

**개인 담당:** UDP 기준 실험 송수신 코드, 실험 환경 구성·PREEMPT_RT 도구 설정, CPU affinity 적용, 관련 논문 리딩·정리.

| 먼저 확인할 내용 | 바로가기 |
|---|---|
| 직접 작성한 C 코드 | [송신기](source/udp_socket/src/sender.c) · [수신기](source/udp_socket/src/receiver.c) |
| 측정 구조와 설계 선택 | [측정 설계](docs/DESIGN.md) |
| 빌드·실행 방법 | [개인 코드 실행 안내](source/README.md) |
| 원본과 개인 기여 근거 | [원본 저장소](https://github.com/kingwooseok/ACEteam_Bachelor_thesis) · [UDP 구현 커밋](https://github.com/kingwooseok/ACEteam_Bachelor_thesis/commit/05722a5) · [주석·설명 보강](https://github.com/kingwooseok/ACEteam_Bachelor_thesis/commit/440f477) |

개인 구현 기준 버전은 `440f477`입니다. UDP 송수신·실험 설정을 담당했으며, 후속 드라이버·XDP·PHC 보정·분석 모듈은 팀 연구 영역입니다.
<!-- LOC:START -->
## 코드 규모와 기술적 과제

**591 LOC · 5개 파일** · 구현 574 / 테스트·예제 0 / 빌드·CI·설정 17.

주석·빈 줄·외부 코드·자동 생성 파일을 제외한 프로젝트 규모입니다. [언어별·범주별 분석](docs/LOC.md)

- **기술적 과제:** 측정 코드의 저장·스케줄링 비용이 실험을 교란하지 않게 구성.
- **설계 선택:** 절대 주기 송신·mmap 사전 준비·수신 중 binary 기록·CPU affinity.
- **확인 근거:** UDP C 코드·실행 설정·측정점 정의. [문제·해결·검증](../../docs/TECHNICAL_CHALLENGES.md#embedded-linux-system-programming)
<!-- LOC:END -->


## 개인 기여

| 담당 영역 | 수행한 작업 |
|---|---|
| UDP 기준 실험 구현 | 송신 sequence·시각 기록, 절대 시간 기반 주기 제어, 수신 시각 측정, tmpfs 선형 버퍼·종료 후 결과 저장 |
| 실험 환경 구성 | Linux 실험 환경과 도구를 준비하고 실행 순서·설정 조건 정리 |
| PREEMPT_RT 실험 도구 설정 | 실시간 Linux 환경에서 사용할 도구의 실행 조건 설정 및 확인 |
| CPU 3번 고정 | CPU affinity를 적용해 지정 작업의 실행 CPU를 CPU 3으로 고정 |
| 논문 리딩·정리 | 관련 논문의 주요 내용과 실험에 적용할 사항을 읽고 정리 |

## 사용 기술과 도구

Embedded Linux · C · POSIX UDP socket · PREEMPT_RT · mmap/mlockall · cyclictest · taskset · CPU affinity · Shell

## 코드에서 확인할 핵심

| 파일 | 구현 내용 |
|---|---|
| [sender.c](source/udp_socket/src/sender.c) | CLI 검증, sequence·TX 시각, MONOTONIC 절대 sleep, UDP 송신 |
| [receiver.c](source/udp_socket/src/receiver.c) | tmpfs 검증, mmap·메모리 잠금·pre-touch, recv 직후 RX 시각, 종료 후 파일 저장·fsync |
| [protocol.h](source/udp_socket/include/protocol.h) | 16-byte wire header, 32-byte native 기록, endian 변환 |
| [config.h](source/udp_socket/include/config.h) | port·payload·주기·샘플 수·출력 경로 기본값 |
| [Makefile](source/udp_socket/Makefile) | 추가 라이브러리 없는 Linux C 빌드·경고 옵션 |

같은 송수신기를 Vanilla·PREEMPT_RT·CPU isolation 조건에서 사용하고 외부 환경만 바꿉니다. 프로그램 차이와 커널·CPU 배치 차이가 뒤섞이지 않도록 하기 위한 기준선입니다. CPU affinity는 `taskset -c 3`으로 외부 적용하며 PTP 동기화도 별도 설정합니다.

## 기술적으로 수행한 내용

### 실험 환경과 실행 조건 구성

실험에 필요한 Linux 환경과 도구 설정을 준비했습니다. 실행 순서와 적용한 설정 조건을 정리해 같은 조건으로 실험을 진행할 수 있도록 구성했습니다.

### PREEMPT_RT 도구 설정

PREEMPT_RT 환경의 실험 도구를 설정하고 실행 조건을 확인했습니다. cyclictest 등 실시간 Linux 환경을 확인하는 도구의 사용 방법과 실행 설정을 정리했습니다.

### CPU affinity 적용

지정 작업이 CPU 3에서 실행되도록 CPU affinity를 설정했습니다. 실행 대상의 CPU 배치를 고정해 실험 조건을 일관되게 유지하는 작업을 수행했습니다.

### 논문 리딩과 기술 정리

관련 논문을 읽고 주요 방법과 실험 조건을 정리했습니다. 정리한 내용을 실험 환경 구성과 설정을 이해하고 적용하는 데 활용했습니다.
