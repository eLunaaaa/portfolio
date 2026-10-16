# 모듈별 상세 설계

[전체 Fail-safe](../FAILSAFE.md) · [소스](../../source/README.md) · [증빙 범위](../EVIDENCE.md)

**먼저 읽기:** [설계 선택·대안 비교·트레이드오프](DECISIONS.md)

| 문서 | 설명 |
|---|---|
| [AI·카메라 인계](AI_HANDOFF.md) | 촬영과 긴 작업 분리, Snapshot 수명, 파일 보존과 로컬 ACK |
| [모델 학습](MODEL_TRAINING.md) | 공동 검수, split 누출 방지, 실행 계약·재현 정보 |
| [Time Sync](TIME_SYNC.md) | 스레드·Queue·상태 Snapshot·timeout·종료 |
| [Uploader 연동 계약](UPLOADER_CONTRACT.md) | 로컬/서버 ACK, spool·재시작·멱등성 경계 |
| [서버](SERVER.md) | 저장·수집 마감·병합 revision·권한·장애 복구 |
| [CI](CI.md) | 변경 영향 검사, 교차 빌드·검증·artifact·실행 경계 |
| [CD](CD.md) | 수동 실행·동일 SHA·canary·권한·smoke·rollback |

각 문서는 요구사항, 상태·책임, 정상 처리, 복구, 코드·검사 순서로 읽을 수 있습니다. AI·Uploader·공통 watchdog의 협업 구현도 source/에 포함했으며 개인 설계와 팀 구현 역할을 표시했습니다.
