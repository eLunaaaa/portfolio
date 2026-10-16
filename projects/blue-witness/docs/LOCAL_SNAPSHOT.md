# 로컬 개발본 반영 기록 · 2026-10-16

## 기준

| 모듈 | 실제 기준 폴더 | 반영 범위 |
|---|---|---|
| 서버 | Desktop/embeded_software_server/backend | app·frontend·tests·설정·요구 패키지 |
| 모델 학습 | Desktop/ACE_Ai/vehicle_model | Python 도구·시험·YAML 실험 설정·Makefile |
| 나머지 | 기존 원격 고정 커밋 | Time Sync·팀 AI 참고 설정·측정 자료·CI/CD 운영본 `5148804` |

개발 폴더에는 코드를 쓰지 않고 읽어서 복제했습니다. 실제 DB·영상·key·cert·쿠키·학습 dataset·모델 가중치·가상환경은 업로드하지 않습니다. source/PROVENANCE.json에는 로컬 파일명·원본 hash·복제 hash를 기록하며, 로컬 파일에 존재하지 않는 Git commit을 임의로 붙이지 않습니다.

## 학습 설계의 추가 내용

1. 학습 이미지의 COCO 순서와 session을 연결해 `WeightedRandomSampler` 가중치를 설정합니다. 설정이 없으면 기존 shuffle이며, replacement sampling으로 train 이미지 수만큼 뽑습니다.
2. 디오라마·취약 장면의 3배·6배 노출 설정을 별도 YAML에 남깁니다. 데이터를 복제하는 대신 학습 확률만 조정하고 validation/test 자료를 그대로 둡니다.
3. parent checkpoint hash·class·입력 크기를 확인한 뒤 미세조정하며 전이 출처를 기록합니다.
4. backbone parameter뿐 아니라 BatchNorm running 통계도 동결할 수 있게 합니다.
5. export는 동일 dataset·해상도 validation 평가와 명시 threshold를 검사·기록합니다. 원격의 `runtime_contract.py` 공통 header import 방식은 이 로컬본에 없습니다.
6. 블러 검수 설정은 5 epochs·batch 8·warmup 0·backbone 동결과 낮은 학습률을 사용합니다. 로컬 release threshold 0.55와 팀 최종 평가 threshold 0.60은 별도 이력입니다.

실제 로컬 실험의 [release manifest](../source/model_training/experiment_evidence/blur_removed_v009_release_manifest.json)에 dataset·checkpoint·ONNX hash와 threshold 0.55가 남아 있습니다. 무거운 모델 파일 없이 학습·export 조건의 근거로 포함했습니다. 이 manifest를 현재 장치 설정이나 새로운 평가 결과로 간주하지 않습니다.

## 서버 변경과 원격 차이

실제 병합 수를 현재 revision의 비대체 입력으로 계산해 수신 수와 구분하는 로컬 변경을 반영했습니다. 원격에는 있으나 로컬에는 없는 삭제 실패 복원·DB 경로·포털 선택 관련 테스트는 [reference/github-regressions](../reference/github-regressions)에 보존했습니다.

로컬 소스와 원격 전체 Python 시험을 섞었을 때 74개 중 실패 7·오류 3이 있었습니다. 현재 로컬 자체 시험 64개 통과를 원격의 모든 회귀 개선까지 포함한다는 뜻으로 해석하지 않습니다. 이번 작업은 로컬 기준 포트폴리오 갱신이라 기능·안전 수정의 임의 재통합은 하지 않았습니다.

## 검증

- 서버 복제본: Python 64개, Node 36개 통과.
- 학습 로컬 원본: pipeline 30개 통과.
- 학습 복제 코드: 로컬 YOLOX·사전학습 fixture 경로를 사용해 pipeline 30개 재실행 통과. 모든 test 자료는 임시 경로 사용.
- 전체 모델 재학습·실장치 배포·실사고 시연은 실행하지 않음.
- 원격에서 가져온 팀 평가 점수는 재계산된 새 모델 점수가 아니라 당시 공개 자료입니다.
