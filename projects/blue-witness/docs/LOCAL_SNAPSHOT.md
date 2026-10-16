# 소스 통합 기록

## 기준과 출처

팀 Git 기준은 `514880419f84a5fc0488652f0f2cb873d04e33b8`입니다. 서버와 학습에는 개인 개발본의 추가 기능을 통합했습니다. 파일별 원본 hash와 포함본 hash는 [PROVENANCE.json](../source/PROVENANCE.json)에 기록합니다.

| 모듈 | 반영 내용 |
|---|---|
| 서버 | 팀의 삭제 실패 복원·DB 호환·포털 선택 회귀 수정 + 개인 개발본의 실제 병합 영상 수 표시 |
| 학습 | 팀의 데이터 검수·출처·병합·런타임 계약 + 개인 개발본의 WeightedRandomSampler·parent checkpoint 미세조정·backbone/BatchNorm 동결 |
| Uploader·Time Sync·IPC·AI | 기준 커밋의 전체 모듈 및 공통 의존성 |
| YOLOX | 기준 커밋에 포함된 vendor 소스·SOURCE_COMMIT·라이선스 |
| 자동화 | 팀 운영 CI/CD 보존 + 이 포트폴리오 전용 루트 검사 workflow |

세션 가중치는 train sampling에만 적용합니다. 동일 dataset의 validation/test 분리는 유지합니다. [과거 학습 release manifest](../source/model_training/experiment_evidence/blur_removed_v009_release_manifest.json)의 Confidence 0.55와 이후 팀 운영 설정 0.60은 실험 단계별 기록입니다. 현재 export는 공통 런타임 설정과의 일치 여부를 검사합니다.

[회귀 검사](CODE_REVIEW.md) · [기여 구분](CODE_GUIDE.md)
