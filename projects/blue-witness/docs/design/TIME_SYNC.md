# Time Sync — 시계 보정과 시간 상태 서비스를 분리하기

[소스](../../source/time_sync/src/time_sync.c) · [systemd 설정](../../source/time_sync/deploy/systemd/time_sync.service) · [설계 선택](DECISIONS.md)

## 문제와 제약

서로 다른 Pi의 같은 프레임 번호는 같은 순간이 아닙니다. 영상 업로드 시각도 촬영 시각과 다릅니다. 촬영·사고 시각에는 공통 epoch가 필요하지만, 대기 제한은 벽시계 보정의 영향을 받지 않아야 합니다.

## 선택 1. NTP를 직접 구현하지 않고 Chrony 상태 서비스 구현

시계 보정은 성숙한 Chrony에 맡기고, C 서비스는 `chronyc -n tracking`에서 동기 상태·오차를 읽어 IPC로 제공합니다. HTTP 서버에 Pi 시각을 물어보는 방식보다 카메라와 같은 장치의 상태를 짧은 IPC로 읽을 수 있습니다. 대신 Chrony·NTP 설정이 잘못되면 이 서비스만으로 해결되지 않습니다.

## 선택 2. 조회·판정·IPC 응답 분리

| 실행 단위 | 책임 | 느려졌을 때 영향 제한 |
|---|---|---|
| Monitor | 주기적 외부 명령 실행·측정 sample 생성 | 명령 시간 제한·자식 회수 |
| State | sample Queue 소비·상태 판정·최신 Snapshot 공개 | mutex·조건변수로 공유 상태 보호 |
| Main/IPC | 요청 검증·최신 Snapshot 응답 | 측정을 기다리지 않고 기존 Snapshot 반환 |

요청마다 `chronyc`를 새로 실행하면 요청 폭주와 외부 명령 지연이 IPC 전체에 전파됩니다. 주기적 sample과 공유 Snapshot으로 이를 분리합니다. 대가로 최근 측정값의 나이와 stale 여부를 별도 판단해야 합니다.

`runtime_push_sample`, `runtime_pop_sample`, `runtime_publish_latest`, `runtime_read_latest`가 각 경계입니다. 대기열은 8칸이며 bounded queue라 메모리가 무한히 증가하지 않습니다.

## 선택 3. 동기 여부를 boolean 하나로 줄이지 않기

| 상태 | 의미 | 호출자에게 주는 정보 |
|---|---|---|
| UNKNOWN | 아직 첫 측정 전 | 정상 동기로 오해하지 않음 |
| SYNCING | source는 있으나 offset 등 기준 미충족 | 보정 중인 상태 구분 |
| SYNCED | Leap·stratum·offset 기준 충족 | 해당 측정 시점의 정상 판단 |
| UNSYNCED | 동기 source 없음 | 단순 명령 실패와 구분 |
| ERROR | 명령·파싱 실패 또는 값이 오래됨 | 오래된 정상 상태를 계속 신뢰하지 않음 |

기본 허용 offset은 100ms이고 기본 측정 주기는 1초입니다. P95 실측 11.30~19.18ms와 허용 기준 100ms는 서로 다른 값입니다. `system_offset_ms` 등을 제공한다고 서버가 임의로 모든 timestamp에 offset을 더하도록 설계한 것은 아닙니다.

## 시간 제한과 종료 설계

`read_chrony_output()`은 전체 명령 실행을 5초로 제한하고 초과 시 자식을 종료·회수합니다. IPC는 I/O가 진행되지 않으면 연결을 끊고 다음 요청을 받습니다. 무기한 blocking으로 전체 프로세스가 멈추는 것을 피합니다.

`monotonic_time_ns()`는 진행·대기 판단에, `realtime_time_ns()`는 외부에 의미 있는 측정 시각에 사용합니다. 시각 보정이 timeout을 뒤집지 않게 합니다. SIGINT/SIGTERM에서는 stop 상태를 설정하고 조건변수 대기를 깨워 스레드를 join한 뒤 소켓·자원을 정리합니다.

## watchdog·서비스 설계

Monitor·State·IPC 업무의 진행을 감시합니다. 정상 대기와 외부 명령 timeout 처리는 진행으로 볼 수 있지만, 중요한 업무가 멈추면 watchdog 알림을 중단합니다. systemd는 `WatchdogSec=15s`, `Restart=on-failure`, 재시작 간격 5초를 사용합니다. 같은 IPC 경로를 공유해야 하므로 `PrivateTmp=false`입니다. 단순 보안 설정이 아니라 다른 프로세스와의 namespace 계약입니다.

## 검증·트레이드오프

- chronyd 중단·명령 없음·파싱 오류·offset 초과·stale·정상 상태를 구분해 확인해야 합니다.
- 상태 IPC가 살아 있다는 것과 시계가 정확하다는 것은 다릅니다.
- 측정 P95는 로컬 시험 조건의 수치이며 nanosecond timestamp의 단위와 실제 오차를 혼동하지 않습니다.
- 공용 IPC·watchdog 팀 의존성도 source/ipc에 포함했습니다. source/에서 `make -C time_sync`로 빌드합니다.
