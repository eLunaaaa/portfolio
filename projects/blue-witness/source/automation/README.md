# 실제 운영 CI/CD · GitHub Actions

기준 커밋: [5148804](https://github.com/kingwooseok/2026ESWContest_mobility_ACE/tree/5148804) · 2026-10-16 확인한 원격 main. 과거 `bc6371a` 스냅샷을 최신 운영본으로 교체했습니다.

**이민석의 기여:** CI/CD 구조와 개선 방향의 아이디어·설계, 구축·운영 개선. 변경 영향별 검사, 캐시·비용 제한, 검증 artifact 승격, canary·순차 배포와 복구 흐름까지 담당 기여 범위로 설명합니다. 팀 협업 저장소의 실제 구현이며 Git 작성자 이력은 그대로 유지합니다. 설계 기여를 모든 줄의 단독 작성과 동일시하지 않습니다.

## 실제 Actions 실행 근거

- [main CI 성공 · 38017054195](https://github.com/kingwooseok/2026ESWContest_mobility_ACE/actions/runs/38017054195): 기준 SHA `5148804`.
- [Pi fleet CD 성공 · 37762360292](https://github.com/kingwooseok/2026ESWContest_mobility_ACE/actions/runs/37762360292): 실행 SHA `2ebe4da`. 최신 CI 실행과 같은 commit의 배포 기록은 아닙니다.
- [원본 Actions 목록](https://github.com/kingwooseok/2026ESWContest_mobility_ACE/actions).

[CI 설계 선택](../../docs/design/CI.md) · [CD 설계 선택](../../docs/design/CD.md)

| 코드 | 역할 |
|---|---|
| [.github/workflows/ci.yml](.github/workflows/ci.yml) | 변경 영향·모듈 검사 흐름 |
| [.github/workflows/pi-component.yml](.github/workflows/pi-component.yml) | Pi 모듈 빌드·artifact |
| [.github/workflows/integration.yml](.github/workflows/integration.yml) | 서버 통합 검사 |
| [.github/workflows/deploy.yml](.github/workflows/deploy.yml) | 성공한 main CI 확인·release·수동 배포 |
| [scripts/ci](scripts/ci) | 빌드·서버 시작·TLS·미디어 검사 |
| [scripts/ci/select-ci.py](scripts/ci/select-ci.py) | 변경 영향·이벤트별 검사 선택과 비용 제한 |
| [scripts/ci/install-host-deps.sh](scripts/ci/install-host-deps.sh) | ARM64 compiler 패키지 준비·APT archive 캐시 |
| [scripts/ci/setup-windows-ffmpeg.py](scripts/ci/setup-windows-ffmpeg.py) | 검증된 Windows FFmpeg 캐시·미디어 도구 확인 |
| [scripts/ci/check-server.py](scripts/ci/check-server.py) | 서버 계약·HTTP/TLS·FFmpeg 통합 검사 |
| [scripts/deploy/deploy_fleet.sh](scripts/deploy/deploy_fleet.sh) | 대상 사전 확인·canary·순차 rollout |
| [scripts/deploy/apply_release.sh](scripts/deploy/apply_release.sh) | 장치별 파일·서비스 적용·smoke·복원 |
| [scripts/deploy/fleet-deploy.sudoers](scripts/deploy/fleet-deploy.sudoers) | 제한된 배포 helper 권한 |

## 보존·실행 범위

원본 `.github/`, `scripts/ci/`, `scripts/deploy/`의 추적 파일을 보존했습니다. 추가된 검사 도구·회귀 테스트를 포함하고 원본에서 사라진 구버전 스크립트는 제거했습니다. 파일별 commit·hash는 [manifest](../PROVENANCE.json)에 기록합니다.

반영 검증: 원본 18개 파일의 byte·SHA-256 일치, YAML 5개 파싱, Shell 문법 검사를 확인했습니다. 로컬 CI 도구 회귀 시험은 36개 중 35개 통과·1개 환경 조건 skip입니다. 원본 YAML 2개의 EOF 빈 줄은 원문 보존을 위해 유지했습니다. 이번 복제로 전체 ARM64 빌드·장치 배포를 재실행한 것은 아닙니다.

원본 프로젝트의 모듈·SDK·runner·Secrets·장치 접근 환경이 필요합니다. 이 하위 폴더만으로 전체 파이프라인이 실행되지는 않습니다. 포트폴리오 루트 workflow로 등록하지 않았으므로 push로 실제 fleet 배포가 실행되지 않습니다. 실행 근거는 위 원본 Actions 기록으로 확인합니다.
