# 구현 위치와 기여 근거

[프로젝트 개요](../README.md) · [팀 원본 기준 버전](https://github.com/kingwooseok/2026ESWContest_mobility_ACE/tree/5148804)

코드가 바뀌어 설명이 어긋나는 것을 줄이기 위해 아래 링크는 정리 시 확인한 커밋으로 고정했습니다. 팀 저장소의 코드·라이선스·빌드 의존성이 기준이며, 동일 코드를 불완전하게 복제하지 않았습니다.

## 담당 영역별 소스

| 영역 | 원본 구현 | 읽을 부분 |
|---|---|---|
| 관측차 AI | [ai/inference.cpp](https://github.com/kingwooseok/2026ESWContest_mobility_ACE/blob/5148804/ai/inference.cpp) | 촬영·Snapshot, 전처리·ONNX 추론, 시계열 판정, 영상 저장 |
| 사고차 카메라 | [ai/video_processor.cpp](https://github.com/kingwooseok/2026ESWContest_mobility_ACE/blob/5148804/ai/video_processor.cpp) | Sensor 사고 시각과 8+2초 구간 확보 |
| 영상 인계 | [ai/evidence_handoff.h](https://github.com/kingwooseok/2026ESWContest_mobility_ACE/blob/5148804/ai/evidence_handoff.h) | `.part`·`.handoff`, 재시도·재시작 복원 |
| 데이터·라벨 | [model_training](https://github.com/kingwooseok/2026ESWContest_mobility_ACE/tree/5148804/model_training) | register_recordings, review_annotations, export_coco, validate_dataset |
| 학습·배포 | [train.py](https://github.com/kingwooseok/2026ESWContest_mobility_ACE/blob/5148804/model_training/train.py) · [export_onnx.py](https://github.com/kingwooseok/2026ESWContest_mobility_ACE/blob/5148804/model_training/export_onnx.py) | 재현 manifest, MPS, export 계약·해시 |
| 서버 | [server/backend/app](https://github.com/kingwooseok/2026ESWContest_mobility_ACE/tree/5148804/server/backend/app) | api/evidence.py, services/storage.py, collection_service.py, merge_worker.py, video_merger.py |
| 사용자·관리자 웹 | [server/backend/frontend](https://github.com/kingwooseok/2026ESWContest_mobility_ACE/tree/5148804/server/backend/frontend) | portal, admin, incident-playback, video-views |
| 시각 상태 IPC | [time_sync.c](https://github.com/kingwooseok/2026ESWContest_mobility_ACE/blob/5148804/time_sync/src/time_sync.c) | Chrony 측정, 상태 Snapshot, IPC·스레드 종료 |
| CI | [.github/workflows](https://github.com/kingwooseok/2026ESWContest_mobility_ACE/tree/5148804/.github/workflows) · [scripts/ci](https://github.com/kingwooseok/2026ESWContest_mobility_ACE/tree/5148804/scripts/ci) | 변경 경로 검사, 교차 빌드, 서버 계약·TLS 검사 |
| 수동 CD | [scripts/deploy](https://github.com/kingwooseok/2026ESWContest_mobility_ACE/tree/5148804/scripts/deploy) | 배포 사전 검사, canary·순차 적용, smoke·실패 복원 |

## 본인 계정의 대표 기여 커밋

원본 Git 기록의 `elunaaaa` / `eLunaaaa` 작성 커밋을 확인했습니다. 다음은 담당 범위를 보여주는 대표 변경이며, 디렉터리 안의 모든 후속 코드를 개인 단독 작성했다고 의미하지 않습니다.

| 기여 | 커밋 |
|---|---|
| YOLOX-Tiny 학습 파이프라인 추가 | [f0c5e0d](https://github.com/kingwooseok/2026ESWContest_mobility_ACE/commit/f0c5e0d) |
| 촬영·라벨 작업 분담 워크플로 | [e04151c](https://github.com/kingwooseok/2026ESWContest_mobility_ACE/commit/e04151c) |
| fine-tuned Tiny 모델 배포 | [8f37724](https://github.com/kingwooseok/2026ESWContest_mobility_ACE/commit/8f37724) |
| 블러 검토 후 모델 배포 | [59253bd](https://github.com/kingwooseok/2026ESWContest_mobility_ACE/commit/59253bd) |
| Chrony 상태 IPC 서비스 | [fd45a67](https://github.com/kingwooseok/2026ESWContest_mobility_ACE/commit/fd45a67) |
| 실제 장치 chronyc 옵션 호환 수정 | [0c27930](https://github.com/kingwooseok/2026ESWContest_mobility_ACE/commit/0c27930) |
| FastAPI 백엔드 추가 | [c0dece6](https://github.com/kingwooseok/2026ESWContest_mobility_ACE/commit/c0dece6) |
| event ID 업로드 계약 정리 | [2a22d08](https://github.com/kingwooseok/2026ESWContest_mobility_ACE/commit/2a22d08) |
| 사용자·차량·사고 재생 화면 | [c07c0ee](https://github.com/kingwooseok/2026ESWContest_mobility_ACE/commit/c07c0ee) |
| 프로필·모바일·통신 로그 | [9cdfcf4](https://github.com/kingwooseok/2026ESWContest_mobility_ACE/commit/9cdfcf4) |
| 병합 레이아웃·영상 모드·로그인 | [bad6d4c](https://github.com/kingwooseok/2026ESWContest_mobility_ACE/commit/bad6d4c) |
| 모듈 CI·수동 Pi fleet 배포 | [4a6d361](https://github.com/kingwooseok/2026ESWContest_mobility_ACE/commit/4a6d361) |
| 배포 전 비밀번호 없는 재시작 권한 검증 | [360dc82](https://github.com/kingwooseok/2026ESWContest_mobility_ACE/commit/360dc82) |
| 실제 P2P 연결 smoke check | [bc6371a](https://github.com/kingwooseok/2026ESWContest_mobility_ACE/commit/bc6371a) |

## 공동 구현·후속 개선

V2V·Sensor·위치/화각 Filter·Uploader 전체는 본인 단독 기여에서 제외합니다. 최신 구현의 SDK·컴파일러 정렬, CI 캐시·시간 제한, fleet 복원·P2P 재연결 보완, 서버 비동기 UI 회귀 수정과 최종 Confidence 0.60 통일은 팀 후속 변경을 포함합니다. 최종 상태를 설명할 때는 ‘팀 기준 구현’, 기여를 설명할 때는 위 개인 작업으로 구분합니다.
