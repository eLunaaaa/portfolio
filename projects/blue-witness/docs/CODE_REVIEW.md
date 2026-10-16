# 코드 검수와 회귀 검사

서버·학습 코드를 팀 기준 `5148804`의 수정과 통합했습니다. 개인 개발본의 병합 수 표시, 세션 가중 sampling, parent checkpoint 미세조정·BatchNorm 동결 기능을 함께 유지했습니다.

## 반영한 수정

| 문제 | 반영 내용 | 검사 위치 |
|---|---|---|
| 사건 삭제 중 파일 처리 실패 | 파일 staging·복원과 DB 삭제 순서 통합 | `test_admin_cleanup.py` |
| 기존 DB·저장 경로 호환 | 경로 해석과 이전 DB 접근 규칙 통합 | `test_database_path_compatibility.py` |
| 웹의 비동기 차량 선택 경쟁 | 이전 응답이 최신 선택을 덮지 않도록 요청·선택 상태 관리 | `test_portal_selection.cjs` |
| 수신 영상 수와 병합 수 혼동 | 선택 revision의 비대체 입력만 병합 수로 집계 | `test_merged_counts.py` |
| 학습·배포 설정 불일치 | 공통 런타임 header 기반 Confidence·shape 계약 검사 | `test_runtime_contract.py` |
| 업로드 완료 오판 | 큐 재시작·중복 등록·ACK 식별자 불일치·HTTP 전송 검사 추가 | [Uploader 검사](../../../scripts/check_uploader.py) |

## 검사 구성

| 검사 | 내용 |
|---|---|
| 서버 Python 74개 | 저장·ACK·인증·병합·삭제 복구·DB 호환 |
| 웹 Node.js 51개 | 영상 모드·이벤트·차량 선택·비동기 UI 회귀 |
| 모델 파이프라인 44개 | 데이터 검수·split·학습·checkpoint·ONNX·런타임 계약 |
| Uploader | 중복 등록, 재시작 상태 복구, ACK 일치·불일치, HTTP health/status/upload |
| Linux 빌드 | Uploader·Time Sync 및 UDP 실험 모듈 |

[포트폴리오 Actions](https://github.com/eLunaaaa/portfolio/actions/workflows/portfolio-ci.yml)에서 각 commit의 실제 결과를 확인할 수 있습니다. 원본 팀의 ARM64·장치 배포 코드는 [automation](../source/automation/README.md)에 보존했습니다.

## 직접 실행

저장소 루트에서 서버 검사를 실행합니다.

```sh
cd projects/blue-witness/source/server/backend
python -m unittest discover -s tests -v
node --test tests/*.cjs
```

모델 fixture 준비·44개 검사 명령은 [루트 workflow](../../../.github/workflows/portfolio-ci.yml)에 정의했습니다. Uploader HTTP 검사는 저장소 루트에서 `python scripts/check_uploader.py`로 실행합니다.

모델 평가 CSV와 팀 실장치 시나리오는 [측정 자료](EVIDENCE.md)로 연결했습니다. 프레임별 유무 판정, 소프트웨어 회귀, 장치 통합 시나리오의 지표를 각각 표시합니다.
