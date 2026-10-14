/* PC 테스트에서 ISR 선언을 일반 함수로 바꾸는 매크로입니다. 테스트가 TWI/Timer 이벤트 함수를 직접 호출합니다. */

#pragma once
#define ISR(vector) void vector(void)
