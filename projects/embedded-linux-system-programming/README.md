# Embedded Linux Experiment Setup

졸업연구에서 실험 환경 구성, PREEMPT_RT 실험 도구 설정, CPU affinity 적용과 관련 논문 리딩·정리를 담당했습니다.

## 개인 기여

| 담당 영역 | 수행한 작업 |
|---|---|
| 실험 환경 구성 | Linux 실험 환경과 도구를 준비하고 실행 순서·설정 조건 정리 |
| PREEMPT_RT 실험 도구 설정 | 실시간 Linux 환경에서 사용할 도구의 실행 조건 설정 및 확인 |
| CPU 3번 고정 | CPU affinity를 적용해 지정 작업의 실행 CPU를 CPU 3으로 고정 |
| 논문 리딩·정리 | 관련 논문의 주요 내용과 실험에 적용할 사항을 읽고 정리 |

## 사용 기술과 도구

Embedded Linux · PREEMPT_RT · cyclictest · taskset · CPU affinity · Shell

## 기술적으로 수행한 내용

### 실험 환경과 실행 조건 구성

실험에 필요한 Linux 환경과 도구 설정을 준비했습니다. 실행 순서와 적용한 설정 조건을 정리해 같은 조건으로 실험을 진행할 수 있도록 구성했습니다.

### PREEMPT_RT 도구 설정

PREEMPT_RT 환경의 실험 도구를 설정하고 실행 조건을 확인했습니다. cyclictest 등 실시간 Linux 환경을 확인하는 도구의 사용 방법과 실행 설정을 정리했습니다.

### CPU affinity 적용

지정 작업이 CPU 3에서 실행되도록 CPU affinity를 설정했습니다. 실행 대상의 CPU 배치를 고정해 실험 조건을 일관되게 유지하는 작업을 수행했습니다.

### 논문 리딩과 기술 정리

관련 논문을 읽고 주요 방법과 실험 조건을 정리했습니다. 정리한 내용을 실험 환경 구성과 설정을 이해하고 적용하는 데 활용했습니다.
