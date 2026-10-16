# Time Sync 담당 코드

[설계 선택·스레드·상태·복구](../../docs/design/TIME_SYNC.md)

- [src/time_sync.c](src/time_sync.c): Chrony 명령 실행, 측정 Queue, 상태 Snapshot, IPC, 종료
- [Makefile](Makefile): 원본 공용 IPC에 연결하는 빌드
- [deploy/systemd/time_sync.service](deploy/systemd/time_sync.service): watchdog·재시작·IPC namespace
- [deploy](deploy): 설치·장치 배포 설정

시계 보정은 Chrony가 담당하고 이 서비스는 상태 제공을 담당합니다. 공용 IPC·watchdog은 본인 코드로 복제하지 않았으므로 [원본 프로젝트 의존성](../README.md#time-sync-빌드)이 필요합니다. 팀 후속 복구 보완이 포함된 통합 스냅샷입니다.
