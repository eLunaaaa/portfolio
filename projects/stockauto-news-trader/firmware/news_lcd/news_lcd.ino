/*
 * 파일 역할: PC의 뉴스/매수 제안을 16×2 LCD에 표시하고 두 버튼의 결정을 전송합니다.
 * 입력: USB Serial(115200), D2=매수, D3=구매 X. 버튼의 다른 쪽은 GND입니다.
 * 출력: I2C LCD(0x27), D8 패시브 부저, PC로 보내는 DECIDE 메시지.
 * 구조: 줄 수신→프레임 검증→LCD 페이지→버튼 디바운스→단발 결정→비차단 알람.
 * 이유: LCD와 부저가 동작하는 중에도 버튼/통신을 처리하고 중복 구매를 방지합니다.
 */
#include <Wire.h>
#include <LiquidCrystal_I2C.h>
#include <stdlib.h>
#include <string.h>
#include <stdio.h>

const uint8_t BUY_PIN=2, NO_PIN=3, BUZZER_PIN=8;
const uint8_t LCD_COLS=16, LCD_ROWS=2;
const unsigned long DEBOUNCE_MS=35;
LiquidCrystal_I2C lcd(0x27,LCD_COLS,LCD_ROWS);

char rx[192], activeId[17]="", lastAlertId[17]="", code[7]="";
char summary[61]="Waiting for PC", resultText[17]="";
uint8_t rxLength=0, importance=0;
bool dropping=false, connected=false, offered=false, armed=false;
unsigned long receivedAt=0, validFor=0, lastDraw=0, lastPing=0, lastReady=0;
unsigned long quantity=0, price=0;
bool alarmRunning=false;
unsigned long alarmStart=0;

struct Button { uint8_t pin; bool raw, stable; unsigned long changed; };
Button buy={BUY_PIN,HIGH,HIGH,0}, reject={NO_PIN,HIGH,HIGH,0};

// 기능: 정확히 16칸 출력. 입력: 행·문자열. 출력: LCD 해당 행.
// 구조·이유: 짧은 문자열 뒤를 공백으로 채워 이전 화면의 글자가 남지 않게 합니다.
void printRow(uint8_t row,const char* text) {
  lcd.setCursor(0,row);
  for(uint8_t i=0;i<LCD_COLS;++i) { if(*text)lcd.write(*text++);else lcd.write(' '); }
}

// 기능: 16자리 제안 ID 검사. 입력: 문자열. 출력: 유효 여부.
// 구조·이유: 잡음/잘린 프레임이 다른 매수 제안으로 해석되지 않게 합니다.
bool validId(const char* text) {
  if(strlen(text)!=16)return false;
  for(uint8_t i=0;i<16;++i)if(!((text[i]>='0'&&text[i]<='9')||(text[i]>='a'&&text[i]<='f')))return false;
  return true;
}

// 기능: 숫자 범위 검사. 입력: 문자열·최댓값·출력 참조. 출력: 성공 여부와 변환값.
// 구조·이유: 빈 값/음수/비숫자와 정수 overflow를 제한합니다.
bool number(const char* text,unsigned long maxValue,unsigned long &value) {
  if(!*text)return false;
  unsigned long result=0;
  for(const char* p=text;*p;++p) {
    if(*p<'0'||*p>'9')return false;
    uint8_t digit=*p-'0';
    if(result>maxValue/10 || (result==maxValue/10 && digit>maxValue%10))return false;
    result=result*10+digit;
  }
  value=result;return true;
}

// 기능: 표시 문자열 검사. 입력: 영문 요약. 출력: 유효 여부.
// 구조·이유: LCD에 표시할 수 있는 ASCII만 받고 버퍼 크기를 넘지 않게 합니다.
bool validText(const char* text) {
  if(strlen(text)>60)return false;
  for(const char* p=text;*p;++p)if(*p<32||*p>126)return false;
  return true;
}

// 기능: 중요 뉴스 알람 시작. 입력: 뉴스 ID·중요도·임계값. 출력: 부저 상태 갱신.
// 구조·이유: 같은 ID는 한 번만 알리고 실제 소리는 updateAlarm에서 비차단 처리합니다.
void startAlarm(const char* id,uint8_t score,uint8_t threshold) {
  if(score<threshold || strcmp(id,lastAlertId)==0)return;
  strcpy(lastAlertId,id);alarmStart=millis();alarmRunning=true;
}

// 기능: 짧은 3회 알림음. 입력: millis 경과. 출력: D8 주파수.
// 구조·이유: delay 대신 시간 구간으로 제어해 버튼 처리가 멈추지 않습니다.
void updateAlarm() {
  if(!alarmRunning)return;
  unsigned long elapsed=millis()-alarmStart;
  if(elapsed>=900){noTone(BUZZER_PIN);alarmRunning=false;return;}
  if(elapsed%300<120)tone(BUZZER_PIN,2200);else noTone(BUZZER_PIN);
}

// 기능: 버튼 안정화와 눌림 이벤트 검출. 입력: Button 상태. 출력: 새 눌림일 때 true.
// 구조·이유: INPUT_PULLUP의 LOW를 눌림으로 보고 35ms 유지된 변화만 인정합니다.
bool pressed(Button &button) {
  bool value=digitalRead(button.pin);unsigned long now=millis();
  if(value!=button.raw){button.raw=value;button.changed=now;}
  if(value!=button.stable && now-button.changed>=DEBOUNCE_MS){button.stable=value;return value==LOW;}
  return false;
}

// 기능: 뉴스/승인 화면 갱신. 입력: 현재 상태·시간. 출력: LCD 두 줄.
// 구조·이유: 종목/중요도/스크롤 요약과 수량/가격/버튼 안내를 번갈아 보여줍니다.
void draw() {
  unsigned long elapsed=millis()-receivedAt;char top[17];
  if(resultText[0]){printRow(0,resultText);printRow(1,"Waiting for news");return;}
  if(offered && (elapsed/2500)%2==1) {
    if((elapsed/1250)%2==0)snprintf(top,sizeof(top),"QTY %lu",quantity);
    else snprintf(top,sizeof(top),"PRICE %lu",price);
    printRow(0,top);printRow(1,"D2:BUY D3:NO");return;
  }
  if(code[0])snprintf(top,sizeof(top),"%s NEWS %u",code,importance);
  else snprintf(top,sizeof(top),"NEWS LEVEL %u",importance);
  printRow(0,top);
  char window[17];size_t length=strlen(summary);
  uint8_t offset=length>16?(elapsed/400)%(length+4):0;
  for(uint8_t i=0;i<16;++i){size_t pos=offset+i;window[i]=pos<length?summary[pos]:' ';}
  window[16]=0;printRow(1,window);
}

// 기능: PC 프레임 한 줄 처리. 입력: 변경 가능한 수신 버퍼. 출력: 화면/승인 상태.
// 구조·이유: 모든 필드 검증 후에만 현재 제안을 교체합니다. READY 전에는 승인하지 않습니다.
void handleFrame(char* line) {
  if(strcmp(line,"RESET")==0) {
    connected=true;offered=false;armed=false;activeId[0]=0;code[0]=0;
    resultText[0]=0;strcpy(summary,"Waiting for news");receivedAt=millis();
    noTone(BUZZER_PIN);alarmRunning=false;return;
  }
  if(!connected)return;
  char* fields[10];uint8_t count=0;char* start=line;
  for(char* ptr=line;;++ptr) {
    if(*ptr=='|'||*ptr==0) {
      if(count>=10)return;
      fields[count++]=start;
      if(*ptr==0)break;
      *ptr=0;start=ptr+1;
    }
  }
  unsigned long q,p,level,threshold,ttl;
  if(count==9 && strcmp(fields[0],"OFFER")==0) {
    unsigned long numericCode;
    if(!validId(fields[1]) || strlen(fields[2])!=6 || !number(fields[2],999999,numericCode) ||
       !number(fields[3],1000000,q)||q==0||!number(fields[4],2000000000UL,p)||p==0||
       !number(fields[5],10,level)||!number(fields[6],10,threshold)||threshold==0||
       !number(fields[7],300,ttl)||ttl==0||!validText(fields[8]))return;
    if(strcmp(activeId,fields[1])==0)return; // 같은 ID를 다시 받아도 유효시간을 연장하지 않습니다.
    strcpy(activeId,fields[1]);strcpy(code,fields[2]);strcpy(summary,fields[8]);
    quantity=q;price=p;importance=level;validFor=ttl*1000UL;receivedAt=millis();
    resultText[0]=0;offered=true;armed=false;
    startAlarm(activeId,level,threshold);
  } else if(count==5 && strcmp(fields[0],"NEWS")==0) {
    if(offered)return; // 승인 대기 화면을 일반 뉴스가 덮어쓰지 않습니다.
    if(!validId(fields[1])||!number(fields[2],10,level)||!number(fields[3],10,threshold)||threshold==0||!validText(fields[4]))return;
    strcpy(activeId,fields[1]);code[0]=0;strcpy(summary,fields[4]);importance=level;
    resultText[0]=0;receivedAt=millis();offered=false;
    startAlarm(activeId,level,threshold);
  } else if(count==3 && strcmp(fields[0],"RESULT")==0 && strcmp(fields[1],activeId)==0) {
    const char* allowed[]={"ACCEPTED","FAILED","UNKNOWN","REJECTED","EXPIRED"};
    bool valid=false;for(uint8_t i=0;i<5;++i)if(strcmp(fields[2],allowed[i])==0)valid=true;
    if(!valid)return;
    offered=false;armed=false;
    if(strcmp(fields[2],"ACCEPTED")==0)strcpy(resultText,"ORDER SENT");
    else strncpy(resultText,fields[2],sizeof(resultText)-1);
    resultText[16]=0;
  }
  draw();
}

// 기능: UART 수신. 입력: USB 바이트. 출력: 완성 프레임 호출.
// 구조·이유: 고정 버퍼를 넘으면 줄 끝까지 폐기하여 뒷부분을 별도 명령으로 실행하지 않습니다.
void readSerial() {
  uint8_t processed=0;
  while(Serial.available() && processed++<64) {
    char ch=Serial.read();
    if(ch=='\n') {
      if(!dropping){rx[rxLength]=0;handleFrame(rx);}
      rxLength=0;dropping=false;
    } else if(ch!='\r' && !dropping) {
      if(rxLength>=sizeof(rx)-1){rxLength=0;dropping=true;}
      else rx[rxLength++]=ch;
    }
  }
}

// 기능: 보드 초기화. 입력: 없음. 출력: 핀·LCD·시리얼 준비.
// 구조·이유: 두 버튼 모두 내부 pull-up을 사용하고 새 연결의 READY를 알립니다.
void setup() {
  pinMode(BUY_PIN,INPUT_PULLUP);pinMode(NO_PIN,INPUT_PULLUP);pinMode(BUZZER_PIN,OUTPUT);
  Serial.begin(115200);lcd.init();lcd.backlight();
  printRow(0,"STOCK NEWS");printRow(1,"Connect PC");Serial.println("READY");
}

// 기능: 반복 실행. 입력: 통신/버튼/시간. 출력: 결정 메시지·LCD·알람.
// 구조·이유: 제안 후 두 버튼을 놓은 상태를 먼저 확인하고, 동시 누름은 구매 X를 우선합니다.
void loop() {
  readSerial();updateAlarm();
  unsigned long now=millis();
  bool buyPressed=pressed(buy),noPressed=pressed(reject);
  if(offered && now-receivedAt>=validFor){offered=false;armed=false;strcpy(resultText,"EXPIRED");draw();}
  if(offered && !armed && buy.stable==HIGH && reject.stable==HIGH && buy.raw==HIGH && reject.raw==HIGH)armed=true;
  if(offered && armed && (buyPressed||noPressed)) {
    // 어느 버튼이든 LOW면 NO를 우선하여 동시 누름/튕김을 매수로 처리하지 않습니다.
    const char* decision=(noPressed||reject.stable==LOW||reject.raw==LOW)?"NO":"BUY";
    Serial.print("DECIDE|");Serial.print(activeId);Serial.print('|');Serial.println(decision);
    offered=false;armed=false;strcpy(resultText,"SENDING...");draw();
  }
  if(connected && now-lastPing>=2000){lastPing=now;Serial.print("PING|");Serial.println(activeId[0]?activeId:"0000000000000000");}
  if(!connected && now-lastReady>=2000){lastReady=now;Serial.println("READY");}
  if(now-lastDraw>=250){lastDraw=now;draw();}
}
