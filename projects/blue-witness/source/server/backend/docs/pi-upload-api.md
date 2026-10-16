# Raspberry Pi–차량 증거 서버 API 규격

## 1. 네트워크 구성

Raspberry Pi는 팀 전용 공유기를 통해 Windows 서버에 접속함.

```text
Windows Ethernet IP: 192.168.50.2
FastAPI Port: 8000
Base URL: http://192.168.50.2:8000
```

`192.168.50.2`는 예시이므로 최종 시험에서는 Windows Ethernet의 실제 IPv4 주소로 변경해야 함.

---

## 2. 서버 상태 확인

영상 업로드 전에 서버와 SQLite가 정상인지 확인함.

### 요청

```http
GET /api/v1/health
```

전체 URL 예시:

```text
http://192.168.50.2:8000/api/v1/health
```

### 성공 응답

HTTP 상태:

```text
200 OK
```

응답 예시:

```json
{
  "status": "ok",
  "service": "vehicle-evidence-server",
  "version": "0.9.0",
  "database": "ok",
  "server_time_utc": "2026-07-28T10:30:00+00:00",
  "server_time_ns": 1785234600000000000
}
```

### 업로드 가능 조건

```text
HTTP 상태가 200임
status가 ok임
database가 ok임
```

조건을 만족하지 않으면 영상을 전송하지 않고 차량 로컬 저장소에 유지함.

---

## 3. 사고 영상 업로드

### 요청

```http
POST /api/v1/events/{event_id}/evidence
```

전체 URL 예시:

```text
http://192.168.50.2:8000/api/v1/events/EVT_20260728_143000123_CAR01/evidence
```

### Content-Type

```text
multipart/form-data
```

### 필수 요청 헤더

```text
X-Evidence-Node-ID
→ 서버에 등록된 Raspberry Pi 노드 ID

X-Evidence-API-Key
→ 노드를 등록할 때 한 번 발급한 API Key

X-Evidence-Content-SHA256
→ 전송할 MP4의 SHA-256 64자리 문자열
```

서버는 Node ID와 API Key로 Pi에 연결된 차량을 확인함.
`metadata.vehicle_id`가 인증된 차량과 다르면 업로드를 거부함.

### 전송 필드

```text
metadata
→ JSON 문자열

file
→ MP4 영상 파일
→ Content-Type: video/mp4
```

---

## 4. Metadata JSON 규격

```json
{
  "event_id": "EVT_20260728_143000123_CAR01",
  "accident_vehicle_id": "CAR_01",
  "vehicle_id": "CAR_02",
  "event_time_ns": 1785216600123000000,
  "clip_start_time_ns": 1785216595123000000,
  "clip_end_time_ns": 1785216605123000000,
  "time_sync_status": "SYNCED",
  "chrony_offset_ms": 1.25,
  "ai_confidence": 0.91
}
```

### 필드 설명

| 필드 | 자료형 | 필수 | 설명 |
|---|---|---:|---|
| `event_id` | 문자열 | O | Pi가 생성한 공통 사고 ID. `EVT_YYYYMMDD_HHMMSSmmm_CARXX` 원문을 사용함 |
| `accident_vehicle_id` | 문자열 | O | 사고 차량 번호 또는 서버 차량 ID. `1`은 `CAR_01`로 변환됨 |
| `vehicle_id` | 문자열 | O | 영상을 보내는 차량 ID |
| `event_time_ns` | 정수 | O | 사고 발생 Unix Timestamp(ns) |
| `clip_start_time_ns` | 정수 | O | 영상 시작 Unix Timestamp(ns) |
| `clip_end_time_ns` | 정수 | O | 영상 종료 Unix Timestamp(ns) |
| `time_sync_status` | 문자열 | O | 시간 동기화 상태 |
| `chrony_offset_ms` | 실수 | X | chrony 측정 오차(ms) |
| `ai_confidence` | 실수 | X | AI 검증 신뢰도(0~1) |

### 시간 조건

```text
clip_start_time_ns
≤ event_time_ns
≤ clip_end_time_ns
```

사고 시각이 영상 시작과 종료 사이에 있어야 함.

### 시간 동기화 상태

```text
SYNCED
→ chrony 동기화가 정상임

HOLDOVER
→ 연결 단절 후 마지막 보정값으로 시간 유지 중임

UNRELIABLE
→ Timestamp를 신뢰하기 어려움

UNKNOWN
→ 아직 동기화 상태를 측정하지 않음
```

### ID 허용 형식

```text
`EVT_YYYYMMDD_HHMMSSmmm_CARXX`
날짜와 시각은 한국 표준시(KST)
URL과 metadata의 event_id는 동일해야 함
event_id의 차량 번호와 accident_vehicle_id는 동일해야 함
```

정상 예시:

```text
EVT_20260728_143000123_CAR01
```

---

## 5. curl 업로드 예시

```bash
curl -i \
  -H "X-Evidence-Node-ID: NODE_PI_02" \
  -H "X-Evidence-API-Key: ${NODE_API_KEY}" \
  -H "X-Evidence-Content-SHA256: ${VIDEO_SHA256}" \
  -F 'metadata={"event_id":"EVT_20260728_143000123_CAR01","accident_vehicle_id":"CAR_01","vehicle_id":"CAR_02","event_time_ns":1785216600123000000,"clip_start_time_ns":1785216595123000000,"clip_end_time_ns":1785216605123000000,"time_sync_status":"UNKNOWN"}' \
  -F 'file=@test.mp4;type=video/mp4' \
  http://192.168.50.2:8000/api/v1/events/EVT_20260728_143000123_CAR01/evidence
```

---

## 6. 업로드 성공 응답

HTTP 상태:

```text
201 Created
```

응답 예시:

```json
{
  "saved": true,
  "storage_ack": true,
  "event_id": "EVT_20260728_143000123_CAR01",
  "vehicle_id": "CAR_02",
  "upload_status": "STORED",
  "file_path": "storage/originals/2026/07/28/EVT_20260728_143000123_CAR01/CAR_02/clip.mp4",
  "file_size_bytes": 1234567,
  "sha256": "397e..."
}
```

차량은 다음 조건을 모두 확인한 뒤 업로드 완료로 판단함.

```text
HTTP 상태가 201임
saved가 true임
storage_ack가 true임
upload_status가 STORED임
응답 event_id가 요청과 동일함
응답 vehicle_id가 요청과 동일함
```

조건을 만족한 경우에만 차량의 전송 Queue를 `DONE`으로 변경함.

서버가 저장했지만 응답이 Pi에 도착하지 않은 경우에는 같은
`event_id + vehicle_id + SHA-256`으로 재전송함. 서버가 기존 파일과 DB를
검증한 뒤 HTTP 200, `storage_ack: true`, `idempotent_replay: true`를 반환함.

---

## 7. 업로드 ACK 유실 복구 조회

HTTP metadata에는 `action_id`를 넣지 않음. `event_id`는 사고 전체의 서버 식별자이며, 내부 IPC/로컬 Queue의 `action_id`와 분리함.

서버는 신규 evidence의 최초 검증 SHA를 DB에 보관함. 이 값이 요청 SHA와 같지만 디스크 파일이 손상되거나 없으면 복원 대기 상태를 기록하고 GET에 404를 반환함. 클라이언트는 같은 원본과 metadata를 POST하면 됨. 서버가 기존 evidence 행을 유지하며 파일 복원과 병합 재예약을 완료한 후 ACK함.

최초 SHA와 요청 SHA가 다르면 409이며 기존 원본을 유지함. 불완전한 HTTP/JSON이나 검증되지 않은 ACK는 PEND 상태로 간격을 두고 재확인함. 401/403은 인증 또는 차량 매핑 오류로 처리함. 저장 ACK에는 파일 fsync, 최종 경로 게시와 DB commit이 포함됨. POSIX에서는 부모 디렉터리도 fsync하며 Windows에서는 파일 핸들의 fsync를 사용함. 병합은 별도 작업 큐에서 진행함.

파일 게시 후 SQLite 등록이 실패하면 원본 파일을 남겨 동일 요청이 재사용할 수 있게 함. 같은 파일을 재사용한 동시 요청이 먼저 등록을 완료한 경우에도 그 원본을 유지함.

업로드 직후 ACK가 유실되면 저장 상태를 먼저 조회해 대용량 영상의 중복 전송을 줄임.

### 요청

```http
GET /api/v1/events/{event_id}/evidence/status
```

필수 헤더:

```text
X-Evidence-Node-ID
X-Evidence-API-Key
X-Evidence-Content-SHA256
```

조회 대상 `vehicle_id`는 인증된 Pi의 서버 등록 정보에서 결정함.

```bash
curl -i \
  -H "X-Evidence-Node-ID: NODE_PI_02" \
  -H "X-Evidence-API-Key: ${NODE_API_KEY}" \
  -H "X-Evidence-Content-SHA256: ${VIDEO_SHA256}" \
  http://192.168.50.2:8000/api/v1/events/EVT_20260728_143000123_CAR01/evidence/status
```

### 저장 확인 성공

```json
{
  "stored": true,
  "storage_ack": true,
  "event_id": "EVT_20260728_143000123_CAR01",
  "vehicle_id": "CAR_02",
  "upload_status": "STORED",
  "file_path": "storage/originals/2026/07/28/EVT_20260728_143000123_CAR01/CAR_02/clip.mp4",
  "file_size_bytes": 1234567,
  "sha256": "397e...",
  "event_status": "COLLECTING",
  "evidence_count": 1,
  "collection_deadline_at": "2026-08-14T12:00:00+00:00",
  "merge_job_status": "PENDING"
}
```

### Pi Queue 처리 규칙

```text
200 + stored=true + storage_ack=true + upload_status=STORED
사고 ID·차량 ID·SHA-256이 요청과 일치
→ 서버 저장 완료로 판단하고 Queue를 DONE으로 변경함.

404
→ 저장 기록이 없거나 원본 복원이 필요하므로 같은 영상을 POST함.

409
→ 기존 원본 SHA 또는 사고 metadata와 요청이 충돌함.
→ 원본을 보존하고 PEND 상태로 간격을 두어 조회함. 다른 작업은 계속 진행함.

422
→ 요청 형식·필드 오류이며 PEND 상태로 간격을 두어 POST함.
→ 실제 잘못된 필수값은 원본 정보를 확인해 수정해야 함.

401 또는 403
→ API Key와 노드-차량 매핑을 확인함.

500 또는 네트워크 오류
→ 로컬 영상을 유지하고 지수 백오프로 상태 조회를 재시도함.
```

---

## 8. 주요 오류 응답

| HTTP 상태 | 의미 | 차량 처리 |
|---:|---|---|
| `400` | URL과 JSON 불일치 또는 빈 파일 | 메타데이터 확인 |
| `401` | Node ID 또는 API Key 인증 실패 | 노드 설정 확인 |
| `403` | 인증 차량과 metadata 차량 불일치 | 차량 매핑 확인 |
| `409` | 기존 원본 SHA·사고 metadata와 충돌하거나 전송 SHA 불일치 | PEND 유지, 상태 조회로 동일 원본 확인 |
| `413` | 파일이 100MB를 초과함 | 영상 길이·압축 확인 |
| `415` | MP4 또는 `video/mp4`가 아님 | 파일 형식 확인 |
| `422` | JSON 필드 또는 Timestamp 오류 | PEND 유지·간격 둔 POST, 잘못된 요청 값 수정 |
| `500` | 서버 내부 또는 SQLite 오류 | 영상 유지 후 재시도 |
| `503` | 서버 DB 사용 불가 | 영상 유지 후 재시도 |

---

## 9. 차량 측 권장 처리 순서

```text
1. 서버용 공유기망에 연결함

2. GET /api/v1/health 요청함

3. HTTP 200, status=ok, database=ok를 확인함

4. 사고 메타데이터 JSON을 생성함

5. MP4와 JSON을 multipart/form-data로 전송함

6. 서버의 HTTP 201 또는 동일 원본 재전송의 HTTP 200 응답을 확인함

7. saved=true와 upload_status=STORED를 확인함

8. storage_ack=true와 사고 ID·차량 ID·SHA 일치를 확인함

9. 성공하면 Queue를 DONE으로 변경함

10. 실패하면 MP4를 삭제하지 않고 로컬에 유지함
```

---

## 10. 서버 저장 결과

영상 파일:

```text
storage/originals/{yyyy}/{mm}/{dd}/{event_id}/{vehicle_id}/clip.mp4
```

SQLite:

```text
events
→ 사고 단위 정보

evidence
→ 차량별 영상·시간·업로드 정보
```
