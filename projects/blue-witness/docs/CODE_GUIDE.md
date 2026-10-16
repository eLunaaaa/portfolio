# 구현 위치와 기여

**이민석:** 모델 학습·배포, 서버·웹, Time Sync, 전체 Fail-safe·Uploader 설계, CI/CD 설계·구축을 담당했습니다. 아래 파일에는 협업 수정도 포함됩니다.

| 영역 | 실제 파일 | 읽을 내용 |
|---|---|---|
| 모델 학습 | [train.py](../source/model_training/train.py) · [export_onnx.py](../source/model_training/export_onnx.py) | 세션 가중 sampling·미세조정·ONNX 계약 |
| 데이터 검수 | [review_annotations.py](../source/model_training/review_annotations.py) · [export_coco.py](../source/model_training/export_coco.py) | 승인·수정·출처·세션별 분리 |
| 서버 | [API](../source/server/backend/app/api) · [services](../source/server/backend/app/services) | 저장·ACK·사건 수집·지연 영상 재병합 |
| 웹 | [frontend](../source/server/backend/frontend) | 사용자·차량 선택·사고 재생·관리자 |
| 시간 상태 | [time_sync.c](../source/time_sync/src/time_sync.c) | Chrony 조회·상태 Snapshot·IPC·종료 |
| 업로드 | [uploader](../source/uploader/README.md) | 개인 설계·팀 구현: spool·영속 큐·전송·ACK·복구 전체 |
| 촬영·추론 | [AI](../source/ai/README.md) · [영상 인계](../source/ai/evidence_handoff.h) | 팀 런타임·개인 모델 배포·설정 연동 |
| 공통 IPC | [ipc](../source/ipc/README.md) · [watchdog](../source/ipc/runtime_watchdog.h) | 팀 공통 소켓·메시지·스레드 감시 |
| CI/CD | [automation](../source/automation/README.md) | 실제 팀 workflow·검사·배포 코드 |

원본 저장소 접근 없이 위 파일과 [파일별 출처](../source/PROVENANCE.json)를 확인할 수 있습니다. 아래 원본 커밋 링크는 협업 이력의 추가 참고 자료입니다.

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


## 협업 범위

전체 Fail-safe와 Uploader 구조는 개인 설계, 모듈 세부 구현·후속 강화는 협업입니다. 촬영·Snapshot·추론 런타임·IPC·공통 watchdog은 팀 구현이며 개인 학습·배포와 연결했습니다. 센서·V2V·위치 필터, 라벨 검수·통합 시나리오는 팀 작업입니다. CI/CD 캐시·SDK·복원 개선과 서버 UI 후속 수정도 원래 협업 기여를 유지합니다.
