# 코드 읽는 순서

1. `configs/baseline.json`에서 입력 크기와 계층 순서를 확인합니다.
2. `config_utils.load_config`가 채널과 공간 크기, FC 입력 수를 어떻게 추적하는지 확인합니다.
3. `build_model_from_config`에서 같은 설정이 PyTorch 계층으로 바뀌는 과정을 읽습니다.
4. `train_model`에서 전처리·학습·Q3.4 평가·저장 흐름을 읽습니다.
5. `generate_cnn_top`에서 계층별 배열과 채널 우선 flatten, start/done 연결을 확인합니다.
6. RTL의 조합 블록은 수치 계산, 순차 블록은 결과 저장과 상태 전이를 담당합니다. `start`는 한 클록 펄스로 제공하고 `done` 이후 결과를 읽습니다.
7. 테스트벤치와 `tests/run_checks.py`에서 입력·기대값 비교를 확인합니다.
8. `evaluate_design`은 외부 도구를 실행하고, `analyze_synthesis`는 마지막 통계를 JSON으로 저장합니다.

각 Python 함수에는 기능·입력·출력·처리 구조 주석을, RTL에는 파일 역할·포트·연산·상태 전이 설명을 넣었습니다.
