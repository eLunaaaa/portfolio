/* PC에서 Slave 상태 머신을 검사할 때 사용하는 Arduino API 대역입니다. 실제 MCU 제어 대신 setup을 호출할 수 있도록 sei 자리를 제공합니다. */

#pragma once
#include <stdint.h>
#define F_CPU 16000000UL
/**
 * 기능: native Worker 테스트에서 AVR interrupt enable 호출 자리를 제공합니다.
 * 입력: 없음.
 * 출력: 아무 하드웨어 변경도 하지 않습니다.
 * 처리 구조: PC 테스트가 Slave setup을 호출할 수 있게 하는 빈 대역 함수입니다.
 */
inline void sei() {}
