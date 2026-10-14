# Architecture

## 처리 단위

입력 5×5와 필터 3×3에서 stride 1, valid 영역으로 9개 패치를 생성한다. CNN의 관례에 따라 필터를 뒤집지 않는 sliding dot product를 수행한다. 결과는 행 우선의 3×3 행렬이다.

Master는 배치 버퍼 2개와 Slave별 FIFO queue 16개 항목을 가진다. 큐 항목은 버퍼 slot과 patch index를 가리킨다. Slave별 전송 후 미완료 작업은 최대 2개이며, 스케줄러는 해당 작업들의 부하도 합계에 포함한다.

## 입력과 버퍼 소유권

UART frame을 끝까지 받은 후에만 입력을 배치 버퍼로 복사한다. 같은 버퍼에 input, filter, load, result, done, sequence를 보관한다. 배치의 9개 결과가 모두 모여 출력될 때 버퍼를 해제하고 호스트에 새 입력 credit를 보낸다.

패치를 보낼 때 입력과 필터를 함께 snapshot해 Slave에 전달한다. 새 행렬의 필터가 이전 행렬의 연산에 섞이지 않는다. 결과는 batch sequence, patch index, worker의 pending job 목록이 일치할 때 반영한다.

## 완료 이벤트

Slave는 결과가 있을 때 D2를 LOW로 유지하고, 결과 ACK 후 큐가 비면 입력으로 풀어준다. Master는 D2/D3의 external interrupt, D9/D10의 pin-change interrupt를 사용한다. 10 ms 간격으로 pending worker의 결과를 확인하는 경로도 있다.

완료 인터럽트는 플래그만 설정한다. 실제 I²C 읽기와 스케줄러 갱신은 Master loop에서 수행한다. 버튼에만 10 ms 디바운스를 적용한다.

## Slave 이중 버퍼

두 작업 slot은 Free → Ready → Processing → Result → Free의 수명주기를 가진다. TWI 수신은 frame 길이·patch index를 확인한 후 빈 slot에 넣고 Ready FIFO에 추가한다. loop는 FIFO 순서대로 연산한다. Result slot은 명시적 ACK가 오기 전까지 재사용하지 않는다.

TWI read 시작 때 결과 8바이트를 별도 TX snapshot에 복사한다. 연속 read 또는 읽기 중 다른 연산 완료에도 같은 frame을 전달한다. ISR에서는 UART 로그나 시연 지연을 실행하지 않는다.

## 연산과 시각화

입력 또는 필터가 0인 항은 건너뛴다. 곱셈에 2단위, 첫 항을 제외한 덧셈에 1단위의 부하를 사용한다. 기본 시연 지연은 2,000/1,000 ms이며 Timer2의 1 ms tick을 기준으로 기다린다. 지연 중에도 인터럽트는 동작하며 PWM을 갱신한다.

서보 PWM은 Timer1 Fast PWM mode 14, prescaler 64, ICR1=4999로 50 Hz다. OCR1A=250~500은 1~2 ms 펄스에 해당한다. 최대 부하 52를 상한으로 사용한다.
