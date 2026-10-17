// 파일 역할: 실제 UNO 코드를 PC에서 검사하기 위한 시간·핀·시리얼 대체 환경입니다.
#pragma once
#include <stdint.h>
#include <stddef.h>
#include <string>
#include <sstream>
#include <algorithm>
#define HIGH 1
#define LOW 0
#define INPUT_PULLUP 2
#define OUTPUT 1
extern unsigned long fakeMillis;
extern int pinValues[20],toneCount;
// 입력: 없음. 출력: 테스트 시간. 이유: delay 없이 경과 시간을 직접 진행합니다.
inline unsigned long millis(){return fakeMillis;}
// 입력: 핀과 모드. 출력: 없음. 이유: 테스트에서는 하드웨어 설정을 생략합니다.
inline void pinMode(uint8_t,int){}
// 입력: 핀. 출력: 테스트가 설정한 값. 이유: 버튼 누름을 재현합니다.
inline int digitalRead(uint8_t pin){return pinValues[pin];}
// 입력: 핀·주파수. 출력: 호출 수 증가. 이유: 중요도에 따른 알람 여부를 검사합니다.
inline void tone(uint8_t,unsigned int){++toneCount;}
// 입력: 핀. 출력: 없음. 이유: 부저 정지 호출을 호환합니다.
inline void noTone(uint8_t){}
struct FakeSerial {
  std::string incoming,outgoing;
  void begin(unsigned long){} // 입력: 통신속도. 출력: 없음. 구조: 테스트 통신 초기화 대체.
  int available(){return incoming.size();} // 출력: 대기 중인 바이트 수.
  char read(){char c=incoming.front();incoming.erase(0,1);return c;} // 출력: 첫 수신 바이트.
  template<class T>void print(T value){std::ostringstream stream;stream<<value;outgoing+=stream.str();} // 입력을 전송 문자열로 기록.
  template<class T>void println(T value){print(value);outgoing+='\n';} // 줄 단위 송신 기록.
};
extern FakeSerial Serial;
