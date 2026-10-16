# AI·카메라 연동 — 검출보다 먼저 영상이 남아야 한다

[개인 AI 변경 patch](../../source/ai/contribution-patches) · [학습 코드](../../source/model_training) · [팀 AI 런타임](../../source/ai)

## 담당 경계

본인 코드는 모델 학습·내보내기·배포와 검출 설정 변경입니다. 카메라 Ring·Snapshot·추론 Worker·공통 인계의 런타임 본체는 팀 구현입니다. 이 문서는 전체 Fail-safe 설계 관점에서 해당 모듈과 어떻게 연결했는지를 설명합니다.

## 선택 1. 촬영은 빠르게, 추론·저장은 별도로

촬영 callback에서 추론·인코딩·IPC ACK 대기를 모두 수행하면 새 프레임을 제때 받지 못합니다. 팀 구현은 callback → Ring → 이벤트 Queue → Worker → 인계로 분리합니다. 스레드 분리 자체보다 **각 데이터의 소유권·끝나는 조건·반환 시점**이 중요합니다.

최근 영상을 Ring에 유지하므로 사고 알림이 온 뒤에도 이전 8초를 꺼낼 수 있습니다. 사고 후 2초까지 기다린 같은 10초 Snapshot으로 AI 판단과 인코딩을 진행해, 판단에 쓴 구간과 저장 구간이 달라지지 않게 합니다.

## 선택 2. Snapshot deep copy 대신 참조 보호

매 사고마다 모든 NV12 프레임을 복사하면 메모리 대역폭·지연이 늘어납니다. Snapshot은 프레임 참조와 timestamp를 보유하고 Ring 교체 시 참조가 살아 있는 슬롯은 여유 버퍼로 바꿉니다. 비용은 refcount·pool 고갈·수명 관리의 복잡성과 약 422MiB의 frame 공간입니다. 모든 처리에서 복사가 0회라는 의미는 아닙니다.

## 선택 3. 일정 간격 추론 + 누적 판정

단일 frame은 흔들림·가림·유사 물체에 취약하고 모든 frame 추론은 장치 비용이 큽니다. 팀 최종 기준은 6프레임 간격, 최근 3회 중 2회 target 검출입니다. Confidence는 최종 0.60이며 본인이 배포 과정에서 바꾼 0.70·0.55 등의 patch는 **실험·배포 이력**으로 보존합니다. 과거 patch의 수치를 최신 권장값으로 쓰지 않습니다.

## 파일과 인계 상태

```text
Snapshot 보호
   → .part 인코딩·flush·trailer·파일 fsync
   → 최초 payload를 .handoff에 원자 기록
   → 최종 .mp4 rename·디렉터리 fsync
   → 별도 인계 스레드: Time Sync 확인·Uploader IPC
   → 로컬 ACK 확인 → 인계 기록 제거, 원본 MP4 보존
```

파일이 완성되기 전에 최종 이름으로 노출하면 다른 프로세스가 불완전 파일을 가져갈 수 있습니다. `.part`와 최종 공개를 분리하고, 인계 정보를 메모리에만 두지 않아 재시작 후 미완료 작업을 찾을 수 있게 합니다. 최초 metadata를 유지하는 것은 retry가 다른 시각·사고로 바뀌지 않게 하기 위함입니다.

## 감시와 복구

| 상황 | 동작·선택 이유 |
|---|---|
| Uploader 미기동·ACK timeout | 원본·인계 기록 보존 후 재시도. 촬영을 같이 중단하지 않음 |
| 인계 process 종료 | `.handoff`와 파일 공개 상태를 대조해 재등록 |
| 카메라 정상 frame 무수신 | 일정 시간 후 카메라 재초기화, 실패하면 process 실패 종료 |
| 중요한 Worker hang | 진행 deadline 만료 → watchdog 통지 중단 → systemd 복구 |
| EOF·잘못된 IPC | 해당 연결 정리, payload type·길이·sequence·version 검사 |

네트워크가 끊겨도 이미 Uploader에 인계된 영상은 Uploader가 보존·재시도합니다. 카메라의 로컬 ACK를 서버 저장 완료로 표시하지 않습니다. 실제 카메라 재시작에서 모든 사고 구간이 무손실이라는 주장은 하지 않습니다.

## 검증

27회 시나리오·81건 저장 자료는 전체 영상 인계 흐름의 통합 근거입니다. watchdog·Snapshot의 실제 코드는 source/에 포함했고 팀 구현으로 표시했습니다.
