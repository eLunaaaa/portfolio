# Blue Witness · 다중 시점 사고 영상 수집

사고 차량과 주변 차량의 **사고 전 8초·후 2초 영상**을 모아 촬영 시각에 맞춰 병합하고 웹에서 확인하는 임베디드 팀 프로젝트입니다. 2026 임베디드 소프트웨어 경진대회 본선 참가 프로젝트입니다.

**이민석 담당:** 차량 검출 모델 학습·배포, 서버·웹, 시간 동기화 상태 서비스, 전체 Fail-safe·Uploader 설계, CI/CD 설계·구축.

![서버에서 촬영 시각을 맞춰 병합한 사고차·관측차의 실제 영상](media/completion-report/merged-video.png)

**구현 결과:** 사고차와 관측차의 영상을 같은 화면에서 비교하고, 웹에서 시점·배속을 선택해 확인합니다. [실제 장치·개인 구현 사진과 설명](docs/IMPLEMENTATION_GALLERY.md)

| 먼저 확인할 내용 | 자료 |
|---|---|
| 실제 장치·AI 검수·웹·CI 화면 | [사진으로 보는 개인 구현](docs/IMPLEMENTATION_GALLERY.md) |
| 실제 구현 파일과 읽는 순서 | [코드 안내](source/README.md) · [기여 구분](docs/CODE_GUIDE.md) |
| 대안과 선택 이유 | [설계 결정표](docs/design/DECISIONS.md) |
| 영상 보존·재전송·ACK | [Uploader 전체 모듈](source/uploader/README.md) · [Fail-safe](docs/FAILSAFE.md) |
| 데이터 준비·학습·ONNX | [학습 파이프라인](source/model_training/README.md) · [평가 분석](analysis/README.md) |
| 웹·업로드·영상 병합 | [서버 코드](source/server/README.md) · [서버 설계](docs/design/SERVER.md) |
| 자동 빌드와 회귀 검사 | [이 저장소 Actions](https://github.com/eLunaaaa/portfolio/actions/workflows/portfolio-ci.yml) · [팀 CI/CD 코드](source/automation/README.md) |

## 개인 기여와 협업

| 담당 영역 | 수행한 작업 |
|---|---|
| AI 학습·배포 | 촬영 등록·BBox 검수 도구, COCO 변환·세션별 split, 2-class YOLOX-Tiny 학습·평가·ONNX 내보내기, 모델 배포·검출 설정 |
| 서버·웹 | FastAPI 증거 업로드·저장 ACK, SQLite 사건·사용자·차량 관리, FFmpeg 시간 정렬·재병합, 사용자·관리자 화면 |
| 시간 동기화 | Chrony 상태 조회 C 서비스, offset·상태·측정 시각 IPC, 타임아웃·스레드 종료 |
| 전체 Fail-safe·Uploader 설계 | 스레드 진행 감시, 영속 spool·큐, 로컬/서버 ACK 구분, 단절 재시도·재시작 복구 구조 |
| CI/CD | 변경 영향별 검사·ARM64 빌드, 검증 산출물 기반 수동 배포, 사전 권한 점검·canary·smoke·실패 복원 흐름 |

업로더는 **개인 설계와 팀 구현**, 카메라·추론 런타임과 공통 IPC는 **팀 구현 및 개인 학습·배포·설정 연동**으로 표시했습니다. 라벨 검수·실장치 시나리오·전체 통합은 공동 작업입니다. 원본 기여 커밋과 파일별 출처는 [기여 안내](docs/CODE_GUIDE.md)와 [manifest](source/PROVENANCE.json)에 남겼습니다.

## 전체 동작

```text
사고 감지 → 사고차 촬영 / 주변 차량 위치·화각 선별 → 관측차 AI
                └──────── 사고 전후 Snapshot ─────────┘
                                  ↓
Chrony → Time Sync 상태 IPC → 촬영 시각·영상 메타데이터
                                  ↓
                  Uploader: spool → 영속 큐 → HTTPS·ACK
                                  ↓
                   FastAPI / SQLite → FFmpeg → 웹
```

Raspberry Pi 5 · IMX219 · C/C++ · POSIX IPC · PyTorch · YOLOX-Tiny · ONNX Runtime · FastAPI · SQLite · FFmpeg · Chrony · systemd · GitHub Actions를 사용했습니다.

![데이터 준비부터 ONNX 내보내기까지](media/training-flow.png)

촬영 callback과 추론·인코딩·전송을 분리하고 Snapshot 참조로 사고 구간을 보호합니다. 업로더는 HTTP 성공만으로 완료하지 않고 사고·차량·파일 hash·서버 저장 상태를 확인합니다. 서버는 늦게 도착한 영상을 새 revision으로 재병합합니다. [모듈별 설계](docs/design/README.md)

## 실제 구현 화면

![등록 차량과 사고별 수집·병합 상태를 조회하는 직접 구현한 웹 포털](media/completion-report/portal.png)

FastAPI·SQLite로 차량별 사건과 영상 수집 상태를 관리하고, 별도 Worker·FFmpeg로 병합합니다. 늦게 도착한 영상은 병합본에 반영하며, 웹에서 전체·목격 차량·내 차량 영상과 사고 장면 0.25배속 재생을 제공합니다.

[차량 실장 → AI 학습 → 영속 업로드 → 시간 정렬·웹 → 협업·CI/CD 사진과 코드](docs/IMPLEMENTATION_GALLERY.md)

## 검증 자료

- **코드 검사:** 서버 Python 74개, 웹 Node.js 51개, 학습·런타임 계약 44개 회귀 검사. Uploader 큐 중복·재시작 복구·ACK 불일치·HTTP 전송 검사.
- **팀 통합 기록:** 테스트베드 충돌 시나리오 27회, 사고·목격 영상 81건 서버 저장. [시나리오와 파일 체크섬](source/docs/measurements/20261008-scenarios/README.md)
- **모델 선정 기록:** 12,873프레임에서 Confidence별 유무 판정을 비교해 0.60을 선정했습니다. [조건·혼동행렬·재계산](analysis/README.md)

[검수 내용과 실행 명령](docs/CODE_REVIEW.md) · [측정·기여 자료](docs/EVIDENCE.md)
