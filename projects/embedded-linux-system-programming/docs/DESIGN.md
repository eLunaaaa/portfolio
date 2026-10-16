# UDP 기준 실험의 설계 선택

## 1. 연구에서 기준선이 필요한 이유

팀 연구는 일반 트래픽의 Linux 스택 호환성과 중요 트래픽의 CPU 격리·스택 우회를 함께 다룹니다. XDP만 빠르게 만드는 것이 아니라 PASS·CPUMAP·AF_XDP 경로의 역할을 구분합니다. 개인 UDP 모듈은 이 중 일반 socket 경로를 같은 프로그램으로 반복 측정하기 위한 기준선입니다.

| 선택 | 대안 | 선택 이유 | 비용·한계 |
|---|---|---|---|
| 커널 조건별 동일 송수신기 | 조건별 별도 프로그램 | 애플리케이션 차이를 줄이고 환경 차이를 비교 | 실제 부하·IRQ·CPU·clock 설정도 동일하게 기록해야 함 |
| MONOTONIC 절대 sleep | 매번 상대 sleep | 작업 시간 누적에 따른 주기 drift 감소 | scheduling 지연·송신 jitter 자체를 제거하지는 않음 |
| REALTIME TX/RX 시각 | 각 호스트 MONOTONIC 직접 차감 | PTP로 정렬한 두 장치의 시각 비교 가능 | 동기 오차·시계 보정이 측정에 섞임 |
| tmpfs + mmap + pre-touch | 패킷마다 디스크 CSV 출력 | 측정 경로의 디스크 쓰기·포맷팅·할당 감소 | RAM·memlock 권한 필요, 전원 단절 시 영속성 보장 불가 |
| 외부 taskset으로 affinity | 코드 안 CPU 3 하드코딩 | 동일 바이너리로 일반/격리 조건 비교 | IRQ·다른 작업의 CPU 배치까지 자동 설정하지 않음 |

## 2. 송신 주기와 측정 시계 분리

[sender.c](../source/udp_socket/src/sender.c)의 `sleep_until()`은 `CLOCK_MONOTONIC`과 `TIMER_ABSTIME`을 사용합니다. 패킷마다 상대 sleep을 더하면 코드 수행 시간이 주기에 누적됩니다. 절대 deadline은 이 drift를 줄이지만 CPU 경쟁으로 deadline을 놓치는 것까지 막지는 않습니다.

패킷에는 sequence와 `CLOCK_REALTIME` TX 시각을 기록합니다. 송신 주기를 유지하는 시계와 서로 다른 장치 사이의 절대 시각 비교를 분리한 설계입니다. TX 측정점은 `sendto()` 이전 사용자 공간이며 실제 NIC wire departure 시각이 아닙니다.

## 3. 측정 구간에서 저장 작업 분리

[receiver.c](../source/udp_socket/src/receiver.c)는 tmpfs 파일을 미리 확보하고 mmap·mlockall·페이지 pre-touch 후 수신합니다.

```text
준비: tmpfs 검사 → 파일 크기 확보 → mmap → mlockall → pre-touch
수신: recv → RX 시각 측정 → header decode → 선형 record 저장
종료: 유효 record만 출력 파일에 복사 → fsync
```

패킷마다 CSV 문자열을 만들거나 디스크를 동기화하지 않습니다. SIGINT/SIGTERM handler는 종료 플래그만 바꾸고 일반 실행 경로에서 결과를 저장합니다. 메모리 잠금 실패는 숨기지 않고 측정을 중단합니다.

이 구조는 측정 교란을 줄이는 것이지 무손실 저장을 보장하는 설계는 아닙니다. 전원 단절·강제 종료 전의 staging 데이터는 영속 저장되지 않을 수 있습니다. 원본 baseline의 결과 경로 재사용·중복·유실 판정 등은 최신 팀 구현과 다를 수 있으므로 실행마다 새로운 경로를 사용합니다.

## 4. wire 형식과 분석 형식 분리

[protocol.h](../source/udp_socket/include/protocol.h)의 wire header는 sequence·TX ns를 network byte order로 전송합니다. 수신 기록은 sequence·TX/RX ns·길이·flag의 32-byte native binary입니다. CSV보다 기록 비용이 낮지만 분석기는 저장 장치의 endian·ABI를 알아야 합니다.

`rx_ns - tx_ns`는 사용자 공간 TX 측정점부터 사용자 공간 RX 측정점까지의 차이입니다. 송신 socket·네트워크·수신 경로·스케줄링·동기 오차가 포함되므로 순수 커널 처리 시간이나 socket wakeup 시간으로 부르면 안 됩니다. Ns 저장 형식도 ns 정확도를 뜻하지 않습니다.

## 5. 환경 설정과 검증의 역할

CPU 3 affinity 적용은 실행 위치 제한이지 CPU 전체 격리의 증명은 아닙니다. IRQ affinity·다른 작업·커널 설정을 함께 확인해야 합니다. `cyclictest`는 scheduling latency의 환경 검사이며 UDP의 end-to-end 지연을 대신 측정하지 않습니다.

팀의 최신 [실험 모니터링 안내](https://github.com/kingwooseok/ACEteam_Bachelor_thesis/tree/2048af3/monitor)는 부하 생성·CPU/network 상태·cyclictest·실제 IRQ 처리 CPU 확인을 구분합니다. 이 자동화 스크립트는 팀원 구현으로 링크만 제공합니다. 드라이버·PHC 보정·completion marker 기반 분석도 [팀 원본](https://github.com/kingwooseok/ACEteam_Bachelor_thesis/tree/2048af3)에서 확인할 수 있습니다.

## 6. 개인 기여와 보존 범위

- `05722a5`: elunaaa 계정의 UDP baseline 추가와 공통 설정 정리.
- `440f477`: elunaaa 계정의 기능별 한국어 주석·설명 보강.
- 포함 코드: 위 두 커밋이 반영된 `440f477`의 `udp_socket/` 원본.
- 제외: 다른 팀원의 XDP·드라이버 포팅·모니터링·후속 분석 본체. 자신의 단독 구현으로 표시하지 않습니다.

[프로젝트 요약](../README.md) · [실행 안내](../source/README.md)
