# 차량 증거 서버 공유기 단절·복구 Fail-Safe 시연

## 1. 검증 목표

공유기 전원이 꺼져 차량–서버 통신이 끊겨도 Pi 원본 영상이 사라지지 않고,
공유기 복구 후 자동으로 서버에 저장되는지 검증함.

성공 기준은 다음과 같음.

```text
통신 단절 중 Pi Queue가 DONE이 되지 않음.
Pi 로컬 MP4가 유지됨.
통신 복구 후 별도 수동 업로드 없이 전송이 재개됨.
서버가 storage_ack=true를 반환한 뒤에만 Queue가 DONE이 됨.
서버 DB에는 event_id + vehicle_id 레코드가 하나만 존재함.
Pi 원본과 서버 MP4의 SHA-256이 일치함.
수집 마감 후 자동 병합 Worker가 정상 동작함.
```

## 2. 역할 구분

### Raspberry Pi

```text
MP4와 Metadata를 로컬 Spool에 먼저 저장함.
Queue를 PEND 상태로 기록함.
서버 연결 실패 시 원본을 유지하고 재시도함.
ACK 유실 시 저장 상태 API를 먼저 조회함.
명시적인 storage_ack=true를 받은 뒤에만 DONE 처리함.
```

### 서버

```text
Node ID와 API Key로 Pi를 인증함.
인증된 Pi와 vehicle_id 매칭을 강제함.
임시 파일 저장, 크기 검사, SHA-256 검사를 수행함.
SQLite commit 완료 후 storage_ack=true를 반환함.
중복 전송을 멱등 처리함.
수집 마감과 영상 병합을 자동 처리함.
```

## 3. 물리적인 공유기 단절 시험

1. 서버와 Pi를 켜고 `/api/v1/health`가 200인지 확인함.
2. Pi Queue에 시험 사고가 `PEND`로 만들어진 것을 확인함.
3. 업로드 직전 공유기 전원선을 분리함.
4. Pi Queue가 `PEND` 또는 재시도 상태이고 MP4가 남아 있는지 확인함.
5. 10초 이상 기다린 뒤 공유기 전원을 다시 연결함.
6. Pi가 Wi-Fi와 서버 연결을 회복할 때까지 기다림.
7. Pi Worker가 저장 상태 조회 또는 업로드를 자동 수행하는지 확인함.
8. 서버 ACK 이후 Pi Queue가 `DONE`이 되는지 확인함.
9. 아래 서버 검증 명령을 실행함.

## 4. 서버 최종 검증 명령

프로젝트의 `backend` 디렉터리에서 실행함.

```bash
python -m app.cli.verify_failsafe_event \
  "${EVENT_ID}" \
  --node-id "${NODE_ID}" \
  --sha256 "${VIDEO_SHA256}"
```

성공 결과의 핵심 항목은 다음과 같음.

```json
{
  "verified": true,
  "upload_status": "STORED",
  "file_size_bytes": 1234567,
  "sha256": "...",
  "problems": []
}
```

`verified=false`이면 `problems` 배열에 DB 누락, 파일 누락, SHA 불일치,
자동 병합 상태 불일치 등의 원인이 표시됨.

## 5. 복구 시간 KPI

다음 두 시각의 차이를 측정함.

```text
시작 시각: 공유기 전원이 다시 들어온 시각
종료 시각: Pi가 storage_ack=true를 받고 Queue를 DONE으로 바꾼 시각

시스템 복구 시간(s) = 종료 시각 - 시작 시각
```

시험은 최소 5회 반복하고 평균, 최댓값, 성공 횟수를 기록하는 것이 적절함.
