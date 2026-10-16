# ACE 서버 구조 설명

## 1. 구조 설계 목적

ACE 서버는 다음 책임을 분리하도록 구성함.

```text
API
→ HTTP 요청과 응답 처리

Schema
→ 입력 데이터 형식 및 검증

Service
→ 실제 업무 규칙 처리

Repository
→ SQLite 조회 및 저장

Storage
→ 원본·병합 영상 저장

Web
→ 사용자·관리자 화면 제공
```

역할을 분리하면 업로드, 인증, DB, 영상 병합 문제를 각각 독립적으로 추적할 수 있음.

## 2. 요청 처리 구조

```text
브라우저 또는 Raspberry Pi
        ↓
FastAPI Router
        ↓
Pydantic Schema 검증
        ↓
Service 업무 규칙
        ↓
Repository SQLite 처리
        ↓
파일 시스템 또는 DB
        ↓
FastAPI 응답
```

예를 들어 차량 등록 요청은 다음 순서로 처리함.

```text
vehicle-registration.js
        ↓
POST /api/v1/portal/vehicles
        ↓
VehicleCreateRequest
        ↓
create_user_vehicle()
        ↓
vehicles INSERT
        ↓
user_vehicles INSERT
        ↓
VehicleResponse
        ↓
포털 차량 목록 갱신
```

## 3. 주요 디렉터리

### `app/api`

HTTP Endpoint를 정의함.

주요 책임:

- URL과 HTTP Method 정의
- 로그인 사용자 확인
- 요청 Schema 수신
- Service 또는 Repository 호출
- 예외를 HTTP 상태코드로 변환
- 응답 반환

SQL은 Repository, FFmpeg 병합은 Service에 맡기는 것을 원칙으로 함.

### `app/schemas`

Pydantic을 이용해 요청과 응답 데이터 형식을 정의함.

예:

```text
VehicleCreateRequest
→ 차량번호, 차종, 별명, 이미지 키 검증

EventSummary
→ 사용자 화면에 표시할 사고 요약

HeartbeatRequest
→ Raspberry Pi 상태 정보 검증
```

Schema는 잘못된 입력이 Service나 DB까지 도달하지 않도록 하는 첫 번째 검증 계층임.

### `app/services`

프로그램의 업무 규칙을 처리함.

예:

```text
access_service.py
→ 사고 접근 코드 생성·해시·검증

account_service.py
→ 계정 등록·로그인·비밀번호 해시

portal_service.py
→ 사용자 차량과 사고 데이터를 화면용 응답으로 변환

portal_access_service.py
→ 사고 접근 세션과 소유권 검사

video_merger.py
→ Timestamp 기반 병합 계획과 FFmpeg 명령 생성
```

Service의 업무 규칙을 HTTP 처리와 분리하면 CLI와 테스트에서도 재사용할 수 있음.

### `app/db`

SQLite 연결과 SQL 처리를 담당함.

```text
database.py
→ DB 위치, SQLite 연결, 초기화

schema.sql
→ 테이블과 인덱스 정의

*_repository.py
→ 기능별 SELECT, INSERT, UPDATE, DELETE 처리
```

Repository는 DB Row를 Python 객체로 변환하고, Transaction의 commit과 rollback을 관리함.

### `frontend`

HTML, CSS, JavaScript로 사용자 인터페이스를 제공함.

```text
backend/frontend/pages/
→ 로그인, 사용자 포털, 관리자 화면

static/
→ JavaScript, CSS, 차량 사진
```

프론트엔드는 화면을 표시하고, 서버는 요청마다 최종 접근 권한을 검사함.

## 4. 핵심 식별자

### `action_id`

V2V에서 생성되어 모든 목격 차량이 공유하는 외부 사고 식별자임.

업로더와 서버 ACK는 값을 숫자로 재가공하지 않고 문자열 원문 그대로 사용함.

### `event_id`

서버가 사고 시각(KST)과 `action_id`를 조합해 생성하는 내부 사건 식별자임.

형식은 `EVT_YYYYMMDD_HHMMSS_{action_id}`이며 DB, 병합 작업, 프론트엔드 상세 조회에 사용함.

### `vehicle_id`

서버 내부에서 차량을 구분하는 고유 ID임.

IP 주소는 변경될 수 있으므로 차량 식별자로 사용하지 않음.

### `license_plate`

사용자에게 표시하는 실제 차량번호임.

내부 참조에는 `vehicle_id`를 사용하고 화면 표시와 사용자 등록에는 차량번호를 사용함.

## 5. 영상 업로드 흐름

```text
Raspberry Pi
    ↓ multipart/form-data
Evidence API
    ↓
메타데이터 검증
    ↓
임시 파일 저장
    ↓
파일 크기·형식·Checksum 확인
    ↓
날짜 / Event ID / Vehicle ID 경로에 저장
    ↓
SQLite evidence 기록
    ↓
저장 성공 JSON 응답
```

영상 파일은 파일 시스템에, 다음 메타데이터는 DB에 저장함.

```text
event_id
action_id
vehicle_id
clip_start_time_ns
clip_end_time_ns
file_path
upload_status
time_sync_status
chrony_offset_ms
received_at
```

## 6. 시간 정렬 원리

각 차량 영상은 다음 절대시각을 포함함.

```text
event_time_ns
clip_start_time_ns
clip_end_time_ns
```

영상 안에서 사고가 발생한 위치는 다음처럼 계산함.

```text
사고 시점 오프셋
= event_time_ns - clip_start_time_ns
```

서버는 차량별 오프셋을 비교해 동일한 절대시각이 최종 영상의 같은 재생 시점에 나타나도록 FFmpeg PTS를 조정함.

정렬 기준은 차량이 기록한 절대 촬영 시각임. Raspberry Pi 시스템 시각의 오차가 영상 정렬에 반영되므로 chrony 상태와 오차를 함께 저장함.

## 7. 병합 모드

### `common`

모든 카메라 영상이 동시에 존재하는 공통 구간만 사용함.

장점:

- 모든 화면이 계속 존재함.
- 빈 화면이 적음.

단점:

- 일부 카메라가 더 길게 촬영한 구간을 버릴 수 있음.

### `extended`

가장 길게 촬영된 증거 구간을 유지함.

장점:

- 사고 전후 상황을 더 길게 확인할 수 있음.
- 일부 카메라만 남아 있어도 유효한 증거를 제공함.

단점:

- 특정 시점에는 일부 화면이 검은색일 수 있음.

## 8. 인증 구조

### 사용자 로그인 세션

```text
쿠키: ace_user_session
목적: 사용자 계정 로그인 유지
```

### 사고 접근 세션

```text
쿠키: ace_session
목적: 특정 사고 영상의 잠금 해제
```

사고 접근 인증 흐름:

```text
사고 확인
→ 기존 사고 세션 삭제
→ 본인인증 완료 가정
→ 5분 접근 코드 발급
→ 접근 코드 검증
→ ace_session 발급
→ 영상 재생·다운로드
```

재생 API와 다운로드 API는 모두 다음을 다시 검사함.

- 사용자 로그인
- 사고 소유권
- 사고 접근 세션
- 실제 병합 영상 파일
- 0바이트 파일 여부

## 9. 차량 등록 흐름

사용자는 다음 정보를 입력함.

```text
차량번호
차종
차량 별명
차량 이미지 또는 일반 아이콘
```

서버는 내부 Primary Key로 사용할 고유 `vehicle_id`를 생성함.

```text
CAR_A1B2C3D4
```

사용자와 차량의 관계는 `user_vehicles` 연결 테이블로 관리함.

## 10. 오류 추적 순서

### 화면에 데이터가 나오지 않을 때

```text
1. 브라우저 Console 확인
2. JavaScript 파일 요청 확인
3. /api/v1/portal/overview 요청 확인
4. 로그인 쿠키 확인
5. Uvicorn 로그 확인
```

### API가 404일 때

```text
1. app.main에 Router가 등록되었는지 확인
2. Router prefix 확인
3. 실제 app.routes 목록 확인
4. 업로드 요청 URL의 action_id 확인
```

### API가 422일 때

```text
1. 브라우저 Network의 Response 확인
2. Pydantic 요청 Schema 확인
3. JSON 필드명 확인
4. 필수 필드 누락 확인
5. Field Validator의 대상 필드 확인
```

### 영상이 재생되지 않을 때

```text
1. 영상 API HTTP 상태 확인
2. ace_session 유효성 확인
3. 병합 파일 경로 확인
4. 파일 크기 확인
5. ffprobe로 MP4 유효성 확인
```

### SQLite 조회가 다를 때

```text
1. DATABASE_PATH 출력
2. 실행 위치 확인
3. .tables 확인
4. PRAGMA table_info 확인
5. 서버와 CLI가 같은 DB를 보는지 확인
```

## 11. 설계상 주의점

- FastAPI 요청 안에서 무거운 FFmpeg 병합을 장시간 실행하지 않음.
- SQLite에는 구조화된 상태와 메타데이터를 저장함.
- 원본·병합 영상은 파일 시스템에 저장함.
- 사용자 입력값을 파일 경로에 그대로 사용하지 않음.
- 사용자 로그인과 사고 접근 인증을 분리함.
- IP 주소를 차량 고유 식별자로 사용하지 않음.
- API 성공 응답을 받은 후에만 Raspberry Pi 전송 큐를 DONE으로 변경함.
- 차량별 Node ID/API Key 인증은 현재 구현되어 있음. 사용자 로그인과 별도로 Pi 업로드 권한을 확인함.
