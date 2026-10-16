# 본인 작업 시점의 CI/CD 코드

기준 커밋: [bc6371a](https://github.com/kingwooseok/2026ESWContest_mobility_ACE/commit/bc6371a)

[CI 설계 선택](../../docs/design/CI.md) · [CD 설계 선택](../../docs/design/CD.md)

| 코드 | 역할 |
|---|---|
| [.github/workflows/ci.yml](.github/workflows/ci.yml) | 변경 영향·모듈 검사 흐름 |
| [.github/workflows/pi-component.yml](.github/workflows/pi-component.yml) | Pi 모듈 빌드·artifact |
| [.github/workflows/integration.yml](.github/workflows/integration.yml) | 서버 통합 검사 |
| [.github/workflows/deploy.yml](.github/workflows/deploy.yml) | 성공한 main CI 확인·release·수동 배포 |
| [scripts/ci](scripts/ci) | 빌드·서버 시작·TLS·미디어 검사 |
| [scripts/deploy/deploy_fleet.sh](scripts/deploy/deploy_fleet.sh) | 대상 사전 확인·canary·순차 rollout |
| [scripts/deploy/apply_release.sh](scripts/deploy/apply_release.sh) | 장치별 파일·서비스 적용·smoke·복원 |
| [scripts/deploy/fleet-deploy.sudoers](scripts/deploy/fleet-deploy.sudoers) | 제한된 배포 helper 권한 |

이 코드는 원본 프로젝트·장치·runner 설정을 전제로 한 역사적 스냅샷이며 최신 운영 보안 패치가 모두 반영된 배포 패키지가 아닙니다. 포트폴리오 루트 workflow로 등록하지 않았으므로 push만으로 배포가 실행되지 않습니다. 담당 흐름을 코드와 함께 설명하기 위한 자료입니다.
