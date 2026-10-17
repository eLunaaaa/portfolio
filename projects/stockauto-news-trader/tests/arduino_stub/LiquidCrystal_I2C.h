// 파일 역할: LCD 호출을 호스트 테스트에서 호환하는 최소 대체 객체입니다.
#pragma once
#include "Arduino.h"
struct LiquidCrystal_I2C {
  LiquidCrystal_I2C(int,int,int){} // 입력: 주소·크기. 출력: 대체 객체.
  void init(){} // 기능: 하드웨어 초기화 대체.
  void backlight(){} // 기능: 백라이트 제어 대체.
  void setCursor(int,int){} // 기능: 커서 이동 대체.
  void write(char){} // 기능: 문자 출력 대체. 실제 통신 상태는 펌웨어 변수로 검사.
};
