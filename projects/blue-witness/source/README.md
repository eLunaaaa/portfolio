# 담당 모듈 소스

[프로젝트 개요](../README.md) · [모듈별 설계](../docs/design/README.md) · [파일별 출처](PROVENANCE.json)

## 무엇을 넣었는가

| 경로 | 범위 | 원본 기준 |
|---|---|---|
| server/ | 담당 백엔드·프런트엔드·테스트·DB schema·도구 | `5148804` 통합 코드 |
| model_training/ | 촬영 등록·BBox 검수·COCO 변환·학습·평가·ONNX 도구·테스트 | `5148804` 통합 코드 |
| time_sync/ | 담당 C 상태 서비스·Makefile·systemd 배포 설정 | `5148804` 통합 코드 |
| ai/contribution-patches/ | 본인 계정의 추론 threshold 변경 diff | `afca0e4`, `59253bd` |
| ai/inference_config.h | 학습 내보내기·테스트에 필요한 최종 런타임 계약 | `5148804`, 팀 통합 의존성, 개인 단독 작성 아님 |
| automation/ | 본인 작업 시점의 CI·수동 fleet CD 코드 | `bc6371a` — 최신 운영 버전이 아닌 역사적 설계 증거 |

‘내가 만든 부분’은 담당 모듈·설계 범위를 뜻하며 해당 파일 모든 줄의 단독 저작을 주장하지 않습니다. 파일별 원본 commit·SHA-256·출처 범위는 manifest에 기록합니다. 모델 학습의 공식 YOLOX, 공용 IPC·watchdog, Sensor·Filter·P2P·Uploader 본체, 팀원의 독립 모듈은 넣지 않았습니다. AI 촬영·Snapshot·영상 처리 본체는 팀 구현이므로 원본 링크로만 설명합니다.

학습 가중치·ONNX 바이너리, 영상·개인 DB·쿠키·실제 key/cert도 넣지 않았습니다. 웹의 제조사 차량 이미지 일부는 재배포 범위에서 제외했습니다. 기본 프로필·브랜드 이미지는 UI 확인용입니다.

## 서버 실행·테스트

```bash
cd projects/blue-witness/source/server/backend
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
# FFmpeg와 ffprobe를 별도 설치한 뒤
python -m unittest discover -s tests
node --test tests/*.cjs
export EVIDENCE_COOKIE_SECURE=false
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

위 명령은 로컬 HTTP 확인용이며 운영 HTTPS·실제 차량 키를 생성하지 않습니다. 보존된 운영 데이터 대신 새 로컬 DB를 생성합니다. 계정·차량 등록은 `app/cli/` 도구를 확인합니다. 빠진 제조사 이미지와 실제 차량 없이 UI 자산·장치 연동 전체를 재현한다고 주장하지 않습니다.

## 학습 환경

`model_training/requirements.txt`와 FFmpeg가 필요합니다. `third_party/YOLOX/`는 원본 저장소의 **동일 커밋·동일 패치 버전**을 별도 준비해야 합니다. 공식 upstream을 임의 최신 버전으로 받으면 `config_2class_tiny.yaml`의 source commit·패치 해시 검증과 맞지 않을 수 있습니다. 데이터·사전학습 checkpoint 역시 별도 준비가 필요합니다.

```bash
cd projects/blue-witness/source
PYTHONPATH=model_training python -m unittest discover -s model_training/tests -p test_runtime_contract.py -v
```

위 4개 계약 시험은 학습 프레임워크 전체 없이 설정 연동만 검사합니다. 전체 파이프라인 시험·학습은 원본의 YOLOX·checkpoint·데이터 준비를 먼저 완료해야 합니다.

## Time Sync 빌드

`time_sync/Makefile`은 원본 프로젝트와 같은 상대 경로의 `ipc/ipc.c`, protocol headers, 공통 watchdog을 사용합니다. 이 파일은 팀 공용 의존성이므로 본인 코드로 복사하지 않았습니다. 원본 `5148804` 프로젝트 트리에 이 모듈을 두고 `make -C time_sync`로 빌드합니다. Chrony 설치·로컬 NTP 기준과 service 사용자 설정도 필요합니다.

## 자동화 코드 주의

`automation/.github/workflows/`는 증거용 하위 폴더라 **포트폴리오 push 시 실행되지 않습니다.** Pi 계정·runner·SDK·역할별 모듈과 원본 폴더 구조가 전제입니다. 포트폴리오에서 배포 helper나 sudoers를 실행·설치하지 마십시오. 최신 원본의 보안·복원 보완은 별도이며 [CI/CD 설계 문서](../docs/design/CD.md)에 차이를 명시합니다.

## 출처·라이선스

원본 프로젝트는 ACE Team 저작권 고지를 유지합니다. 이 복제는 작성자의 담당 영역을 설명하는 포트폴리오 자료이며 팀·외부 코드의 저작권을 바꾸거나 새 오픈소스 라이선스를 부여하지 않습니다. [원본 고지](https://github.com/kingwooseok/2026ESWContest_mobility_ACE/blob/5148804/THIRD_PARTY_LICENSE.md)
