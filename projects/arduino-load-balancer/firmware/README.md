# Firmware integration

Master와 Slave는 Arduino AVR core 위에서 실행하는 UNO 스케치다. USART·TWI·인터럽트·타이머는 레지스터로 직접 제어하며 Wire/Servo 라이브러리를 사용하지 않는다. 공통 로직은 LoadBalancerCore header-only 라이브러리로 제공한다.

## 빌드

프로젝트 폴더에서 `arduino-cli compile --fqbn arduino:avr:uno --libraries firmware/libraries firmware/master`를 실행한다. Slave는 `--build-property compiler.cpp.extra_flags=-DSLAVE_ADDRESS=1`과 같이 주소 1~4를 각각 지정한다. 주소를 바꿀 때 build path를 구분해 각 ELF/HEX를 보관한다.

## Master

Timer0는 Arduino core의 millis를 사용한다. USART RX ISR이 고정 길이 38바이트 frame을 받아 완료 플래그를 설정한다. TWI는 100 kHz polling이며 각 단계의 status와 20 ms deadline을 확인한다. INT0/INT1/PCINT ISR은 완료 flag만 설정한다. I²C와 result 처리, 후속 dispatch는 loop에서 실행한다.

## Slave

Timer1은 50 Hz 서보 PWM이며 D9 OC1A를 사용한다. Timer2는 CTC 1 ms tick이며 시연 지연의 기준이다. TWI ISR은 고정 길이 수신 및 TX snapshot을 처리한다. loop는 연산을 수행하며 zero-skip을 적용한다. 결과 slot은 명시적 ACK까지 유지한다.

기본 시연 지연은 곱셈 2,000 ms·덧셈 1,000 ms다. 빌드 시 `DEMO_MUL_MS`와 `DEMO_ADD_MS`로 바꿀 수 있다. Master/Slave 통신 규격과 값의 범위는 docs/PROTOCOL.md에 있다.
