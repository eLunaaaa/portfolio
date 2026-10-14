/*
 * 습도 기반 wet/dry 추론에 사용할 feature 가중치와 bias를 정의하는 헤더입니다.
 * control_logic.c가 include하며 ml/train_wet_model.py의 export_header로 같은 이름의 계수를 출력합니다.
 */

/* wet/dry 판정 기본 계수. 학습 계수는 ml/train_wet_model.py가 같은 이름의 헤더로 출력합니다. */
#ifndef WET_MODEL_PARAMS_H
#define WET_MODEL_PARAMS_H
#define WET_W0 1.0f
#define WET_W1 0.0f
#define WET_W2 0.0f
#define WET_W3 0.0f
#define WET_BIAS 0.0f
#endif
