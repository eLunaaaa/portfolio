# Architecture

## 분류 요청부터 결과까지

1. Arduino 1이 물체를 2초 동안 감지해 UART 문자 `1`을 전송한다.
2. Zybo polling task가 UART FIFO를 읽어 RX queue로 전달한다. 한 번에 최대 100바이트를 처리하고 1 tick을 대기한다.
3. processing task가 요청을 읽어 `xSem_CaptureRequest`를 준다.
4. CameraTask가 요청을 기다렸다가 촬영한다. 정상 JPEG만 공유 버퍼에 넣고 `xSem_DataReady`를 준다.
5. AI task가 이미지 전처리·추론을 수행하고 결과를 TX queue로 전달한다.
6. AI task는 성공·실패 모두에서 `xSem_AI_Done`을 준다. 카메라는 이 신호를 받은 뒤에만 공유 버퍼를 재사용한다.
7. UART TX task가 명령 바이트를 보낸다. Arduino 1이 서보와 LCD를 제어한다.

초기화는 `xSem_AI_Ready`와 준비 상태를 통해 먼저 확인한다. 모델 초기화에 실패하면 촬영 요청에 오류 코드를 응답한다. Binary semaphore이므로 중복 촬영 요청은 합쳐질 수 있으며, 모든 요청을 누적 처리하는 큐 구조는 아니다.

## 태스크와 우선순위

| 태스크 | 우선순위 | 대기/역할 |
|---|---:|---|
| ColorTask | 2 | 500 ms 주기로 heartbeat LED 갱신 |
| ButtonTask | 3 | 50 ms 주기로 버튼 확인 |
| CameraTask | 2 | 촬영 요청·AI 완료 대기 |
| ArduinoTxTask | 2 | TX queue 대기, UART 송신 mutex 사용 |
| ArduinoPollingTask | 3 | UART FIFO 확인, RX queue 전달 |
| ArduinoProcessingTask | 2 | RX queue 대기, 촬영 요청 전달 |
| AI task | 3 | 프레임 준비 대기, 전처리·추론·결과 생성 |

원 보고서의 UART interrupt 설명과 달리 Zybo 코드는 polling이다. Arduino 2의 USART 수신은 인터럽트 기반이다.

## 통신 명령

| 방향 | 바이트 | 의미 |
|---|---|---|
| Arduino 1 → Zybo | `1` | 촬영·분류 요청 |
| Zybo → Arduino 1 | `1` | 캔 |
| Zybo → Arduino 1 | `2` | 일반 쓰레기 |
| Zybo → Arduino 1 | `3` | 유리 |
| Zybo → Arduino 1 | `4` | PET |
| Zybo → Arduino 1 | `5` | Empty, 뚜껑 열지 않음 |
| Zybo → Arduino 1 | `6` | 촬영·전처리·추론 오류 |

문자 ASCII를 주고받는다. 요청과 결과가 서로 다른 방향이라 같은 `1`을 쓰는 구조이다. 로그 문자열은 분류 결과 UART로 보내지 않는다.

## 적재 상태와 서버

Arduino 2는 `ge/ca/gl/pe`의 초음파 센서를 순서대로 읽는다. 기본값은 2 cm 이하에서 full, 4 cm 이상에서 empty, 중간 구간에서 직전 성공 보고 상태 유지이다. 측정 실패 시 직전 상태를 유지하고 보고하지 않는다.

HTTP 200 응답을 확인한 뒤 해당 상태를 확정한다. 실패하면 다음 주기에 다시 시도하며 3번의 실패 주기 이후 Wi-Fi를 재연결한다. 시작 시 각 센서 상태를 보고하고 30초 간격으로 주기적 재전송한다. 부팅 때 서버 전체 상태를 무조건 empty로 덮어쓰지 않는다.

Flask는 `t1/t2` 각각의 4개 종류 상태를 가진다. `None`은 미수신, `False`는 여유, `True`는 수거 필요를 뜻한다. 전체 조회는 `/status`, 센서 갱신은 `/<zone>/<fill|empty>/<type>`이다. `al`은 전체 종류이며, `/reset`은 POST로 미확인 상태를 만든다.

상태는 단일 프로세스 메모리에 있고 lock으로 접근을 보호한다. 여러 worker나 서버 재시작에서 상태를 공유·보존하지 않는다. GET 갱신 경로는 기존 ESP8266 코드와의 호환을 위해 유지했다. 운영 서비스로 확장할 때는 인증된 POST와 지속 저장소를 적용해야 한다.
