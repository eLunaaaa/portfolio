# 프로젝트별 LOC 분석

| 프로젝트 | 구현 | 테스트·예제 | 빌드·CI·설정 | 합계 LOC |
|---|---:|---:|---:|---:|
| [Blue Witness](../projects/blue-witness/docs/LOC.md) | 36,189 | 5,230 | 3,686 | 45,105 |
| [CNN 학습·RTL 자동화](../projects/configurable-cnn-rtl/docs/LOC.md) | 790 | 911 | 25 | 1,726 |
| [엣지 AI 쓰레기 분류](../projects/edge-ai-trash-sorter/docs/LOC.md) | 1,391 | 119 | 31 | 1,541 |
| [Arduino 동적 부하 분산](../projects/arduino-load-balancer/docs/LOC.md) | 571 | 187 | 13 | 771 |
| [실시간 Linux 실험 환경](../projects/embedded-linux-system-programming/docs/LOC.md) | 574 | 0 | 17 | 591 |
| [뉴스·물리 버튼 주식 주문](../projects/stockauto-news-trader/docs/LOC.md) | 1,736 | 851 | 14 | 2,601 |
| [Smart Umbrella Stand](../projects/smart-umbrella-stand/docs/LOC.md) | 596 | 77 | 14 | 687 |

## 집계 기준

- **지표:** cloc이 분류한 물리적 코드 줄 수입니다. 주석·빈 줄은 코드에서 제외하고 별도 집계합니다. 한 줄의 문장 수나 파일 byte 수를 LOC로 환산하지 않습니다.
- **대상:** Git으로 추적하는 프로젝트의 C/C++·헤더·Arduino·Python·Verilog·JavaScript·HTML·CSS·SQL과 빌드 스크립트·CI YAML 등입니다. 동일 내용도 서로 다른 파일 경로로 관리하면 각각 셉니다.
- **구분:** 구현, 테스트·예제, 빌드·CI·설정을 분리합니다. 합계는 이 세 범주의 합입니다. 프로그램의 웹 화면 HTML은 포함하고 결과 보고용 HTML은 제외합니다.
- **제외:** 사진·영상·문서·평가 데이터·모델 가중치·로그·실행 파일·의존성 목록, docs/analysis/media/reference/data의 자료는 제외합니다. root의 포트폴리오 관리 코드는 각 프로젝트에 합산하지 않습니다.
- **별도 표시:** YOLOX 등의 third_party/vendor 코드, CNN 생성 top RTL과 우산꽂이 모델 계수 헤더는 합계에서 제외하고 프로젝트 상세 표에서 따로 보여줍니다.
- **기여 범위:** 팀 코드·공통 의존성과 검증 코드를 포함한 현재 포트폴리오의 프로젝트 규모입니다. 개인 담당·설계·공동 구현은 각 프로젝트 README의 기여 설명으로 확인합니다.

## 재집계

[cloc 공식 배포](https://github.com/AlDanial/cloc/releases/tag/v2.10)의 `cloc-2.10.pl`을 준비한 뒤 저장소 루트에서 실행합니다.

```bash
python3 scripts/analyze_loc.py --cloc /path/to/cloc-2.10.pl
python3 scripts/check_portfolio.py
```

[집계 스크립트](../scripts/analyze_loc.py) · [프로젝트·언어별 JSON](../metrics/loc.json) · [파일별 코드·주석·빈 줄·SHA-256](../metrics/loc-files.csv)
