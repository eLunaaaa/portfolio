# Blue Witness · 개인 작성 LOC

**프로젝트 전체 45,105 LOC 중 개인 작성 확인 33,418 LOC**입니다. 개인 구현 담당 범위 안에서 본인 Git 계정이 작성자로 남아 있고 현재 포함 코드와 줄 대응이 확인된 수치입니다.

| 구분 | LOC | 집계 의미 |
|---|---:|---|
| **개인 작성 확인** | **33,418** | 서버·웹·학습·시간 동기화·Uploader·CI/CD의 개인 계정 작성 이력 |
| 개인 담당 모듈 내 협업 | 4,340 | 같은 모듈에서 다른 팀원 계정의 작성 이력이 남은 코드 |
| 팀 공통 구현 | 5,545 | 촬영/추론 런타임·공통 IPC |
| 개발본 추가·통합 변경 | 1,802 | 원본 기준 버전과 행 대응이 확정되지 않은 추가·수정분 |

## 모듈별 구분

| 모듈 | 전체 | 개인 작성 확인 | 담당 모듈 내 협업 | 팀 구현·공통 | 개발본·통합 |
|---|---:|---:|---:|---:|---:|
| ai | 4,907 | 0 | 0 | 4,907 | 0 |
| automation | 1,815 | 331 | 1,483 | 0 | 1 |
| ipc | 638 | 0 | 0 | 638 | 0 |
| model_training | 7,272 | 5,150 | 543 | 0 | 1,579 |
| server | 22,006 | 20,117 | 1,701 | 0 | 188 |
| time_sync | 1,026 | 792 | 234 | 0 | 0 |
| uploader | 7,441 | 7,028 | 379 | 0 | 34 |

## 기준

- 원본 기준 commit: `514880419f84a5fc0488652f0f2cb873d04e33b8`. 본인 계정 두 가지 표기인 `elunaaaa`·`eLunaaaa`의 Git 작성자 식별자를 사용합니다.
- 누적 추가 줄 수가 아니라 **현재 포트폴리오에 남아 있는 코드행**을 집계합니다. 주석·빈 줄·외부·자동 생성 코드는 전체 LOC와 같은 기준으로 제외합니다.
- `git blame -w -M`으로 공백 변경과 파일 내부 이동을 추적합니다. cloc의 코드행과 원본의 동일 행 대응이 확인된 줄만 개인 작성 확인에 포함합니다.
- Uploader는 개인 전체 개발 범위에 포함하고 이후 CD 연동 등의 팀 수정은 작성자 이력으로 분리합니다. 설계·통합·모델 학습·실험 기여는 LOC와 함께 별도 담당 설명으로 제시합니다.

## 검산과 재집계

[파일별 집계·해시](../../../metrics/blue-witness-personal-loc-files.csv) · [코드행별 commit 근거](../../../metrics/blue-witness-personal-loc-lines.csv) · [집계 JSON](../../../metrics/blue-witness-personal-loc.json)

```bash
python3 scripts/analyze_personal_loc.py --upstream /path/to/original-repository --cloc /path/to/cloc-2.10.pl
python3 scripts/analyze_loc.py --cloc /path/to/cloc-2.10.pl
```

[개인 담당과 협업](CODE_GUIDE.md) · [전체 LOC](LOC.md)
