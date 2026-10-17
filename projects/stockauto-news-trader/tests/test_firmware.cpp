/* 파일 역할: 실제 .ino를 포함해 프레임·버튼·알람·만료를 검사합니다.
 * 입력: 가상 UART, 시간, 버튼 핀. 출력: assert 결과와 PASS.
 * 이유: 별도의 복제 구현 대신 배포하는 펌웨어 함수를 직접 실행합니다.
 */
#include "arduino_stub/Arduino.h"
#include <assert.h>
#include <iostream>
unsigned long fakeMillis=0;
int pinValues[20]={0},toneCount=0;
FakeSerial Serial;
#include "../firmware/news_lcd/news_lcd.ino"

// 기능: 프레임 주입. 입력: 문자열. 출력: 펌웨어 수신 상태.
// 구조·이유: 실제 readSerial의 한 번당 처리량 제한도 그대로 거칩니다.
void frame(const std::string& text){Serial.incoming+=text+"\n";while(Serial.available())readSerial();}
// 기능: 새 승인 화면 준비. 입력: ID·중요도. 출력: 제안 상태.
// 구조·이유: 손을 뗀 버튼 상태로 초기화한 뒤 동일한 UART 프로토콜을 사용합니다.
void proposal(const char* id="abcdef0123456789",int level=6){
  pinValues[2]=pinValues[3]=HIGH;
  buy={BUY_PIN,HIGH,HIGH,fakeMillis};reject={NO_PIN,HIGH,HIGH,fakeMillis};
  frame("RESET");
  frame(std::string("OFFER|")+id+"|005930|2|70000|"+std::to_string(level)+"|5|90|SAMSUNG NEW CONTRACT");
  loop();
}
// 기능: 테스트 진입. 입력: 없음. 출력: PASS 또는 assert 종료.
// 구조·이유: 정상·실패 경계와 버튼 중복을 단계별로 검사합니다.
int main(int argc,char**){
  if(argc>1){
    // 기능: Python 프레임을 실제 펌웨어 수신기로 검사. 입력: stdin 줄. 출력: 성공 코드.
    // 이유: 문자열 검사만으로 놓치기 쉬운 개행/필드 경계를 양쪽 코드로 확인합니다.
    pinValues[2]=pinValues[3]=HIGH;setup();std::string line;
    while(std::getline(std::cin,line))frame(line);
    assert(connected&&offered&&quantity==2&&price==70000&&importance==6);
    std::cout<<"PC frame -> firmware PASS\n";return 0;
  }
  pinValues[2]=pinValues[3]=HIGH;setup();
  proposal();assert(offered&&armed&&alarmRunning);updateAlarm();assert(toneCount>0);
  pinValues[2]=LOW;loop();fakeMillis+=36;loop();assert(!offered);
  assert(Serial.outgoing.find("DECIDE|abcdef0123456789|BUY")!=std::string::npos);
  size_t first=Serial.outgoing.size();fakeMillis+=100;loop();assert(Serial.outgoing.size()==first);
  // 동시 누름은 거절을 우선합니다.
  proposal("1234567890abcdef");Serial.outgoing.clear();
  pinValues[2]=pinValues[3]=LOW;loop();fakeMillis+=36;loop();
  assert(Serial.outgoing.find("|NO")!=std::string::npos);
  // 화면이 새로 떠도 이미 누르고 있던 BUY는 새 승인으로 사용하지 않습니다.
  frame("RESET");pinValues[2]=LOW;buy={BUY_PIN,LOW,LOW,fakeMillis};
  frame("OFFER|ffffffffffffffff|005930|2|70000|6|5|90|SAMSUNG");loop();
  assert(offered&&!armed);
  // 과대 프레임은 전체 폐기하고 다음 정상 줄을 읽습니다.
  frame("RESET");frame(std::string(230,'X'));assert(!offered);
  frame("OFFER|bad|005930|2|70000|6|5|90|SAMSUNG");assert(!offered);
  frame("OFFER|aaaaaaaaaaaaaaaa|005930|2|70000|6|5|999|SAMSUNG");assert(!offered);
  proposal("bbbbbbbbbbbbbbbb");fakeMillis+=90001;loop();assert(!offered);
  // 임계값 미만이면 새 부저 알람이 시작되지 않습니다.
  proposal("cccccccccccccccc",3);assert(!alarmRunning);
  // 악재 NEWS는 알리되 버튼 승인을 활성화하지 않습니다.
  frame("RESET");frame("NEWS|dddddddddddddddd|5|5|SAMSUNG RISK ALERT");assert(!offered&&alarmRunning);
  unsigned long value;assert(!number("4294967296",2000000000UL,value));
  std::cout<<"Firmware protocol / debounce / duplicate / held button / timeout / alarm PASS\n";
}
