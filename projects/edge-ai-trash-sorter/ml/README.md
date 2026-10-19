# Model preparation

학습 모델은 MobileNetV2(alpha=0.5, include_top=False, ImageNet weights)를 backbone으로 사용하고 GlobalAveragePooling2D, BatchNormalization, Dropout(0.3), Dense(128, ReLU), BatchNormalization, Dropout(0.3), Dense(5, softmax)를 연결한 구조다.

- 이미지: RGB 128×128, bilinear resize, `(pixel - 127.5) / 127.5` 정규화
- 클래스 순서: can, general, glass, pet, empty
- 학습: 분류기 학습과 backbone 상위 부분 fine-tuning
- 데이터 출처 설명: TrashNet, RealWaste, TrashBox 및 Empty 배경 이미지

Zybo에 추가할 model_data.h는 TFLite flatbuffer를 `g_model` 배열로 제공해야 한다. 입력 FLOAT32 [1,128,128,3], 출력 FLOAT32 5개와 클래스 순서를 확인한 뒤 현재 resolver에 모든 operator가 등록되어 있는지 검사한다. 다른 타입이나 형태는 현재 실행 코드가 거부한다.

학습 결과 accuracy는 0.7229이다. 데이터셋 재배포 권한과 원 출처를 확인해 사용하는 데이터만 별도 확보한다.
