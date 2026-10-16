# CI 설계 — 변경한 모듈을 실제 배포 산출물까지 검사하기

[보존한 workflow](../../source/automation/.github/workflows) · [검사 스크립트](../../source/automation/scripts/ci) · [설계 선택](DECISIONS.md)

## 기준과 목적

이 폴더는 2026-10-16 원격 main `5148804`의 실제 운영 코드를 보존합니다. 초기 구축뿐 아니라 비용 최적화·SDK 정렬·Windows FFmpeg 캐시 개선 방향까지 이민석의 CI/CD 설계·구축 기여에 포함합니다. 협업 구현의 Git 작성자 이력은 유지합니다. 목적은 ‘컴파일 명령이 성공했는가’보다 **같은 계약·의존성으로 검사한 파일을 장치에 넘길 수 있는가**입니다. [실제 CI 성공 기록](https://github.com/kingwooseok/2026ESWContest_mobility_ACE/actions/runs/38017054195)

## 선택 1. 거대한 단일 job 대신 모듈 영향도

Sensor·Filter·P2P·AI·Time Sync·Uploader와 서버는 언어·라이브러리·실행 환경이 다릅니다. 모든 변경에 모든 장치를 검증하면 시간이 커지고, 파일 하나만 검사하면 공용 IPC 변경의 영향을 놓칩니다. workflow에서 모듈 경로와 IPC·빌드 스크립트 같은 공용 경로를 연결합니다.

변경 영향 job의 출력을 Pi 컴포넌트 workflow와 서버 검사에 넘깁니다. 선택되지 않은 job은 skip일 수 있지만, 선택된 job의 실패를 최종 성공으로 덮으면 안 됩니다. 필요한 전체 검사는 수동·main 정책과 구분합니다.

## 선택 2. 개발 환경과 CI의 교차 빌드 기준

Pi 소스를 x86 host compiler로만 검사하면 ARM 라이브러리·ABI 문제를 놓칩니다. 원본 SDK/sysroot와 ARM64 cross compiler를 사용해 장치 실행 파일을 만듭니다. host 단위 테스트와 target ELF 검사는 서로 대체하지 않습니다.

`pi-component.yml`과 `build-pi.sh`에서 대상별 빌드·검사·artifact 경로를 연결합니다. 모델·libcamera·FFmpeg 등 장치 의존성이 준비돼 있어야 하며, 포트폴리오의 스크립트 폴더만으로 모든 모듈 빌드가 되는 것은 아닙니다.

## 선택 3. 서버는 계약·실행·TLS·미디어를 나눠 확인

| 검사 계층 | 잡으려는 문제 | 잡지 못하는 문제 |
|---|---|---|
| Python 문법·단위 테스트 | 잘못된 입력·상태·저장/ACK 계약 | 장치 네트워크·운영 부하 |
| 실제 앱 시작·health | import·DB 초기화·서버 lifespan | 모든 영상 데이터 유효성 |
| TLS fixture | 인증서 검증을 포함한 HTTPS 시작·요청 | 운영 CA 배포 상태 |
| FFmpeg fixture | 실제 인코딩·병합·probe | 실차 모든 codec·긴 영상 |
| 웹 회귀 | 비동기 응답·재생·모달·선택 유지 | 모든 모바일 브라우저의 시각적 동작 |

현재 운영본은 `check-server.py`와 OS별 의존성 설치로 계약·HTTP/TLS·미디어 검사를 연결합니다. `setup-windows-ffmpeg.py`는 캐시된 미디어 도구도 검증합니다. 이 저장소의 루트 workflow는 통합 서버 Python 74개·웹 51개와 C 서비스·모델·RTL 회귀 검사를 실제 포트폴리오 commit에서 실행합니다.

## 선택 4. 코드와 산출물의 연결

빌드 결과를 artifact로 보관하고 CD가 선택한 main CI run의 SHA와 현재 대상 SHA를 맞춥니다. CD에서 임의 최신 소스를 다시 빌드하면 검사한 것과 배포한 것이 달라질 수 있으므로, 검증된 artifact를 승격하는 방식을 선택했습니다.

## 실패 처리와 보안

- 잘못된 컴포넌트·빌드 실패·계약 오류는 해당 단계에서 실패시킵니다.
- 인증 token은 Actions secret·실행 환경으로 받고 소스에 실제 값을 넣지 않습니다.
- PR 검사와 self-hosted 운영 배포를 분리해 검증 코드가 바로 fleet에 쓰이지 않게 합니다.
- 캐시 hit는 정확한 도구·SDK라는 보장과 다릅니다. 운영본의 도구 검증과 캐시 회귀 테스트를 함께 보존했습니다.

## 대안과 비용

매번 Pi에서 직접 빌드하는 방식보다 CI에서 일관된 검사를 할 수 있지만 SDK·sysroot·toolchain 유지 부담이 생깁니다. 모든 경로를 좁게 필터링하면 빠르지만 의존 경로 누락 위험이 생겨 공용 IPC·빌드·설정 변경을 영향 범위에 포함해야 합니다.

## 검증 범위

여기에 보존한 YAML은 포트폴리오 루트의 `.github`가 아니라 참고 하위 폴더라 자동으로 실행되지 않습니다. Shell 문법과 YAML 형식은 정리 단계에서 확인하고, 실제 ARM64 빌드·Actions runner·artifact 승격 전체를 이번에 새로 운영 실행했다고 주장하지 않습니다.
