# 개인 구현 코드 · UDP socket baseline

원본: [ACEteam_Bachelor_thesis](https://github.com/kingwooseok/ACEteam_Bachelor_thesis/tree/440f477/udp_socket), 보존 commit `440f4779b50b66cb638d0ab7d8650b9aa861825d`.

`udp_socket/` 6개 파일과 당시 LICENSE를 원문 그대로 보존했습니다. **최신 팀 통합본이 아닌 개인 구현 시점의 기준선**입니다. 포함 코드는 원본 [GPL v2 라이선스](LICENSE)를 따릅니다.

## 빌드와 실행

Linux에서 C compiler·make가 필요합니다. macOS에서는 Linux 전용 tmpfs·메모리/소켓 기능을 그대로 실행할 수 없습니다.

```bash
cd projects/embedded-linux-system-programming/source/udp_socket
make
./bin/sender --help
./bin/receiver --help
```

실험에는 별도 Linux 송신·수신 장치, PTP 시각 동기화, tmpfs, memlock 권한을 준비합니다. affinity는 필요 시 외부 `taskset -c 3`으로 적용합니다. 포트폴리오 확인만을 위해 시스템 시계·IRQ 설정을 변경할 필요는 없습니다.

[원본 실행 옵션과 기록 형식](udp_socket/README.md)을 참고하세요. 그 문서의 `make udp` 및 `./udp_socket/...` 경로는 원본 저장소 구조 기준입니다. 이 폴더에서는 위처럼 `udp_socket/` 내부에서 `make`합니다. 결과 파일은 run별 새 경로를 사용하세요.

## 검증 범위

포트폴리오 반영 시 원본 commit과 6개 파일의 byte 일치를 확인하고 `make -n`으로 빌드 명령을 점검했습니다. Linux 실장치 실행·지연 성능 재측정은 수행하지 않았습니다. 원본의 최신 회귀 테스트를 구버전 baseline에 통과한 것처럼 표시하지 않습니다.

[설계 선택](../docs/DESIGN.md) · [개인 담당 범위](../README.md)
