# 서버 담당 코드

[설계 선택·상태 전이·복구](../../docs/design/SERVER.md) · [실행 방법](../README.md#서버-실행테스트)

- API: [backend/app/api](backend/app/api)
- 저장·병합·권한: [backend/app/services](backend/app/services)
- DB schema·repository: [backend/app/db](backend/app/db)
- 사용자·관리자 화면: [backend/frontend](backend/frontend)
- 검증: [backend/tests](backend/tests)

2026-10-16 로컬 `Desktop/embeded_software_server/backend` 기준입니다. 실제 병합 수 집계 등 로컬 변경을 포함하고 운영 데이터·인증서는 제외했습니다. 원격의 일부 후속 회귀 개선이 로컬에는 없으므로 [차이 기록](../../reference/github-regressions/README.md)을 함께 읽습니다. GitHub에만 있던 테스트를 로컬 소스의 시험으로 혼동하지 않도록 분리했습니다.
