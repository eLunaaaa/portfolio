# AI에서 직접 변경한 부분과 연동 계약

[학습 코드](../model_training) · [AI 연동 설계](../../docs/design/AI_HANDOFF.md)

- [afca0e4 patch](contribution-patches/afca0e4-inference.patch): 본인 threshold 0.75 → 0.70 변경
- [59253bd patch](contribution-patches/59253bd-inference.patch): 본인 블러 검토 모델 배포 시 threshold 0.70 → 0.55 변경
- [inference_config.h](inference_config.h): 최종 팀 기준 0.60·NMS 0.65·sampling 6 계약. 모델 내보내기·테스트 의존성으로 포함했으며 개인 단독 작성 코드 아님

patch는 과거 실험 이력의 근거이며 최신 설정에 무작정 적용할 변경이 아닙니다. 본인의 주요 AI 구현은 전체 [model_training](../model_training) 파이프라인과 모델 배포입니다. 촬영·Snapshot·추론 runtime 본체는 팀 구현이라 이 폴더에 복제하지 않았습니다.
