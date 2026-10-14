# 코드 읽기 안내

이 프로젝트는 PC가 보낸 행렬을 Master가 9개 패치로 나누고 네 Slave가 계산한 결과를 다시 합치는 프로그램입니다. 입력 5×5와 필터 3×3으로 출력 3×3을 만듭니다.

## 먼저 볼 파일

| 파일 | 담당 역할 | 먼저 볼 함수 |
|---|---|---|
| [Master](../firmware/master/master.ino) | UART 입력, 실제 I²C 통신, 전체 실행 연결 | setup → loop → dispatch/collect |
| [공통 로직](../firmware/libraries/LoadBalancerCore/src/LoadBalancerCore.h) | 행렬별 buffer, 부하 계산, queue·pending 관리 | Scheduler::accept → next_job → mark_sent → complete |
| [Slave](../firmware/slave/slave.ino) | 수신·연산·결과 보관, PWM 표시 | TWI_vect → commit_receive → loop |
| [PC 호스트](../host/stream_matrices.py) | 입력 전송과 결과 검산 | main → packet/reference |

## 한 작업의 입력·출력 흐름

PC의 input/filter → Master UART frame → Scheduler::accept의 배치 snapshot → dispatch의 22바이트 작업 → Slave Ready slot → loop의 signed 합성곱 → Result slot → collect의 8바이트 읽기·ACK → Scheduler::complete → RESULT 출력.

Queue는 아직 보내지 않은 작업, pending은 Slave에 보냈지만 결과가 돌아오지 않은 작업입니다. batch slot은 행렬의 저장 위치이며 sequence는 해당 입력 세대를 구분합니다. 같은 slot을 재사용해도 sequence로 결과를 구별합니다.

## 소스 설명 형식

파일 첫머리는 전체 역할과 호출 관계를 설명합니다. 각 함수 위에는 기능, 입력, 출력, 처리 구조를 같은 순서로 적었습니다. 구현과 분리된 테스트는 tests에 있으며 register 대역은 PC 검산용으로 사용합니다.
