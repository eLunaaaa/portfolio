# Blue Witness 소스 읽기

담당 모듈과 동작에 필요한 팀 공통 코드를 함께 담았습니다. **원본 팀 저장소를 내려받을 필요 없이** 아래 파일에서 구현·테스트·측정 근거를 확인할 수 있습니다.

| 폴더 | 파일 역할·읽는 순서 | 기여 |
|---|---|---|
| [model_training/](model_training/README.md) | 촬영 등록 → BBox 검수 → COCO 변환 → 학습 → 평가 → ONNX | 개인 담당, 팀 검수·후속 수정 포함 |
| [server/](server/README.md) | API → 서비스 → DB repository → 웹·회귀 테스트 | 개인 담당, 팀 후속 수정 포함 |
| [time_sync/](time_sync/README.md) | Chrony 측정 → 상태 Snapshot → IPC 응답 | 개인 담당 |
| [uploader/](uploader/README.md) | 영상 인계 → spool → SQLite 큐 → 전송 → 저장 확인·복구 | 개인 전체 개발, 이후 CD 연동 과정에서 팀 수정 |
| [ai/](ai/README.md) | 촬영 Ring·Snapshot → 추론·인코딩 → 영상 인계 | 팀 런타임, 개인 학습·배포·설정 연동 |
| [ipc/](ipc/README.md) | 공통 메시지·소켓·스레드 진행 watchdog | 팀 공통 의존성 |
| [automation/](automation/README.md) | 팀 CI·ARM64 빌드·수동 Pi 배포의 실제 workflow·스크립트 | 개인 설계·구축, 협업 개선 |
| [docs/measurements/](docs/measurements/README.md) | 차량 간 시각 오차·시나리오·파일 체크섬 | 팀 통합 측정 |

## 서버 실행과 검사

Python 3.12, Node.js 22, FFmpeg·ffprobe를 준비합니다. 이 `source/` 폴더에서 시작합니다.

```sh
python -m pip install -r server/backend/requirements.txt httpx cryptography PyYAML
cd server/backend
python -m unittest discover -s tests -v
node --test tests/*.cjs
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

서버는 새 로컬 DB로 시작합니다. 계정·차량 등록 도구는 [app/cli](server/backend/app/cli)에 있습니다.

## C 서비스 빌드

Linux에서 C compiler, SQLite·OpenSSL 개발 패키지를 준비한 뒤 `source/`에서 실행합니다.

```sh
make -C uploader
make -C time_sync
```

실행 파일은 `test/uploader`, `test/time_sync`입니다. 업로더 설정은 [uploader.env.example](uploader/deploy/uploader.env.example)을 복사해 자신의 서버·차량 인증 정보로 작성합니다. Chrony 서비스와 Pi 카메라 라이브러리는 각 모듈 README에 설명했습니다.

## 학습 파이프라인 검사

YOLOX 소스와 Apache 라이선스를 [third_party/YOLOX](model_training/third_party/YOLOX)에 포함했습니다. 포트폴리오 루트의 [자동 검사](../../../.github/workflows/portfolio-ci.yml)는 임시 모델 fixture를 생성해 학습·추론·export 계약을 검사합니다. 실제 학습 명령과 데이터 준비는 [학습 README](model_training/README.md)를 따릅니다.

## 출처

통합 기준은 팀 커밋 `5148804`와 개인 개발본의 세션 가중 학습·미세조정·병합 수 표시 기능입니다. [통합 기록](../docs/LOCAL_SNAPSHOT.md)과 [파일별 SHA-256](PROVENANCE.json)을 함께 제공합니다. 팀 구현의 원래 저작권과 [서드파티 고지](THIRD_PARTY_LICENSE.md)를 유지했습니다. 운영 인증 정보·사용자 DB·원본 영상·대규모 학습 데이터·모델 바이너리는 실행 설정과 분리했습니다.
