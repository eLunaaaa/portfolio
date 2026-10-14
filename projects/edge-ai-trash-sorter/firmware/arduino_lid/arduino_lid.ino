/*
 * 쓰레기 분류 시스템의 물체 감지·뚜껑·LCD 제어 보드 코드입니다.
 * 초음파로 물체를 감지해 Zybo에 분류를 요청하고 1~6 결과 문자에 따라 서보와 안내 화면을 제어합니다.
 * WAIT_OBJECT, WAIT_CLASS, SERVO_ACTIVE_HOLD의 세 상태를 loop에서 순서대로 관리합니다.
 */

#include <SoftwareSerial.h>
#include <Servo.h>
#include <Wire.h>
#include <LiquidCrystal_I2C.h>
#include <avr/io.h>

const int TRIG_PIN = 12;
const int ECHO_PIN = 4;

SoftwareSerial ZYNQ_Serial(7, 8);

Servo servo1, servo2, servo3, servo4;

const int SERVO1_PIN = 11;
const int SERVO2_PIN = 9;
const int SERVO3_PIN = 5;
const int SERVO4_PIN = 6;

LiquidCrystal_I2C lcd(0x27, 16, 2);

const int SERVO_HOME   = 0;
const int SERVO_ACTIVE = 90;

const float DETECTION_THRESHOLD_CM = 10.0f;

const unsigned long DETECT_HOLD_MS        = 2000;
const unsigned long SERVO_HOLD_MS         = 5000;
const unsigned long WAIT_CLASS_TIMEOUT_MS = 10000;
const unsigned long NONE_SHOW_MS          = 2500;

enum State {
  WAIT_OBJECT,
  WAIT_CLASS,
  SERVO_ACTIVE_HOLD
};

State state = WAIT_OBJECT;

unsigned long detectStart = 0;
unsigned long stateTs     = 0;

int activeServoIdx = 0;
unsigned long noneDisplayStart = 0;

/**
 * 기능: 물체 감지용 초음파 센서의 echo 길이를 거리로 변환합니다.
 * 입력: 없음. trigger/echo 핀과 micros()를 사용합니다.
 * 출력: cm 단위 거리, timeout·측정 범위 초과이면 999.0.
 * 처리 구조: 10 us trigger 이후 echo 상승·하강을 각각 제한 시간 안에 읽고 펄스 길이를 58로 나눕니다.
 */
float measureDistanceCm();
/**
 * 기능: 네 개 쓰레기통 서보를 닫힘 위치로 복귀시킵니다.
 * 입력: 없음. 연결된 servo1~4를 사용합니다.
 * 출력: 모든 서보에 SERVO_HOME 각도를 기록합니다.
 * 처리 구조: 시작·오류·상태 전환 때 다른 뚜껑이 열려 있지 않도록 공통 복귀 동작을 수행합니다.
 */
void resetAllHome();
/**
 * 기능: 선택한 쓰레기 종류의 서보를 지정 각도로 이동합니다.
 * 입력: idx: 서보 번호 1~4, angle: 목표 각도.
 * 출력: 해당 Servo 인스턴스의 write()를 호출합니다.
 * 처리 구조: 번호를 switch로 구분하고 해당하는 서보 한 개만 제어합니다.
 */
void moveServoIdx(int idx, int angle);
/**
 * 기능: 분류 결과 문자를 사람이 읽을 수 있는 LCD 문구로 표시합니다.
 * 입력: c: ASCII 1~5 분류 결과.
 * 출력: LCD에 CAN/GENERAL/GLASS/PET/NONE을 표시합니다.
 * 처리 구조: 화면을 지우고 공통 제목과 종류 문자열을 두 줄에 출력합니다.
 */
void displayClassOnLCD(char c);
/**
 * 기능: 다음 물체를 기다리는 LCD 화면으로 전환합니다.
 * 입력: 없음. LCD 인스턴스를 사용합니다.
 * 출력: System Ready와 WAIT_OBJECT 문구를 표시합니다.
 * 처리 구조: 화면을 지운 뒤 첫째·둘째 줄의 커서를 설정하고 안내 문구를 출력합니다.
 */
void lcdShowReady();

/**
 * 기능: 초음파 센서 trigger 출력을 LOW로 내립니다.
 * 입력: 없음. D12(PB4)를 사용합니다.
 * 출력: PORTB의 PB4 bit를 0으로 만듭니다.
 * 처리 구조: trigger 펄스의 시작 전·종료 후 기준 상태를 설정합니다.
 */
static inline void trigLow()  { PORTB &= ~(1 << PB4); }
/**
 * 기능: 초음파 센서 trigger 펄스를 HIGH로 올립니다.
 * 입력: 없음. D12(PB4)를 사용합니다.
 * 출력: PORTB의 PB4 bit를 1로 만듭니다.
 * 처리 구조: 측정 함수가 짧게 유지한 뒤 trigLow로 펄스를 종료합니다.
 */
static inline void trigHigh() { PORTB |=  (1 << PB4); }
/**
 * 기능: 초음파 센서 echo 핀의 현재 논리값을 읽습니다.
 * 입력: 없음. D4(PD4)를 사용합니다.
 * 출력: echo가 HIGH이면 1, LOW이면 0.
 * 처리 구조: PIND의 PD4 bit를 검사해 상승·하강 시각 측정에 제공합니다.
 */
static inline uint8_t echoRead() { return (PIND & (1 << PD4)) != 0; }

/**
 * 기능: 쓰레기통 뚜껑 보드의 통신·센서·서보·LCD를 준비합니다.
 * 입력: 없음. 부팅 시 Arduino core가 호출합니다.
 * 출력: UART, trigger/echo pin, 네 Servo와 LCD 대기 화면.
 * 처리 구조: 주변장치를 초기화하고 모든 뚜껑을 닫은 뒤 WAIT_OBJECT 화면을 표시합니다.
 */
void setup() {
  Serial.begin(115200);
  ZYNQ_Serial.begin(115200);

  DDRB |= (1 << DDB4);
  trigLow();

  DDRD &= ~(1 << DDD4);
  PORTD &= ~(1 << PD4);

  servo1.attach(SERVO1_PIN);
  servo2.attach(SERVO2_PIN);
  servo3.attach(SERVO3_PIN);
  servo4.attach(SERVO4_PIN);

  resetAllHome();

  lcd.init();
  lcd.backlight();
  lcdShowReady();
}

/**
 * 기능: 물체 감지→분류 대기→뚜껑 개방·복귀 상태 머신을 실행합니다.
 * 입력: 초음파 거리, millis(), Zybo UART 문자.
 * 출력: 분류 요청 문자, LCD 안내, 해당 서보의 열림/닫힘.
 * 처리 구조: WAIT_OBJECT에서 2초 감지 후 요청, WAIT_CLASS에서 결과/오류/timeout 처리, SERVO_ACTIVE_HOLD에서 5초 후 복귀합니다.
 */
void loop() {
  unsigned long now = millis();
  float dCurrent = (state == WAIT_OBJECT) ? measureDistanceCm() : 999.0f;
  now = millis();

  switch (state) {

    case WAIT_OBJECT: {
      if (noneDisplayStart != 0 && (now - noneDisplayStart >= NONE_SHOW_MS)) {
        noneDisplayStart = 0;
        lcdShowReady();
      }

      if (dCurrent < DETECTION_THRESHOLD_CM) {
        if (detectStart == 0) {
          detectStart = now;
        } else if (now - detectStart >= DETECT_HOLD_MS) {
          while (ZYNQ_Serial.available()) ZYNQ_Serial.read();

          ZYNQ_Serial.print('1');

          lcd.clear();
          lcd.setCursor(0, 0);
          lcd.print("Classifying...");
          lcd.setCursor(0, 1);
          lcd.print("Wait result");

          detectStart = 0;
          stateTs = now;
          state = WAIT_CLASS;
        }
      } else {
        detectStart = 0;
      }
      break;
    }

    case WAIT_CLASS:
      if (WAIT_CLASS_TIMEOUT_MS > 0 &&
          (now - stateTs) > WAIT_CLASS_TIMEOUT_MS) {
        resetAllHome();
        noneDisplayStart = 0;
        lcdShowReady();
        state = WAIT_OBJECT;
        break;
      }

      if (ZYNQ_Serial.available()) {
        char c = ZYNQ_Serial.read();

        if (c == '6') {
          resetAllHome();
          lcd.clear();
          lcd.print("Classify failed");
          noneDisplayStart = now;
          state = WAIT_OBJECT;
          break;
        }
        if (c >= '1' && c <= '5') {
          displayClassOnLCD(c);

          if (c >= '1' && c <= '4') {
            activeServoIdx = c - '0';

            resetAllHome();
            moveServoIdx(activeServoIdx, SERVO_ACTIVE);

            noneDisplayStart = 0;
            stateTs = now;
            state = SERVO_ACTIVE_HOLD;

          } else if (c == '5') {
            noneDisplayStart = now;
            activeServoIdx = 0;
            resetAllHome();
            state = WAIT_OBJECT;
          }
        }
      }
      break;

    case SERVO_ACTIVE_HOLD:
      if (now - stateTs >= SERVO_HOLD_MS) {
        if (activeServoIdx >= 1 && activeServoIdx <= 4) {
          moveServoIdx(activeServoIdx, SERVO_HOME);
        }
        activeServoIdx = 0;
        state = WAIT_OBJECT;
        noneDisplayStart = 0;
        lcdShowReady();
      }
      break;
  }

  delay(10);
}

/**
 * 기능: 분류 결과 문자를 사람이 읽을 수 있는 LCD 문구로 표시합니다.
 * 입력: c: ASCII 1~5 분류 결과.
 * 출력: LCD에 CAN/GENERAL/GLASS/PET/NONE을 표시합니다.
 * 처리 구조: 화면을 지우고 공통 제목과 종류 문자열을 두 줄에 출력합니다.
 */
void displayClassOnLCD(char c) {
  lcd.clear();
  lcd.setCursor(0, 0);
  lcd.print("Detected:");
  lcd.setCursor(0, 1);

  switch (c) {
    case '1': lcd.print("CAN");     break;
    case '2': lcd.print("GENERAL"); break;
    case '3': lcd.print("GLASS");   break;
    case '4': lcd.print("PET");     break;
    case '5': lcd.print("NONE");    break;
    default:  lcd.print("UNKNOWN"); break;
  }
}

/**
 * 기능: 다음 물체를 기다리는 LCD 화면으로 전환합니다.
 * 입력: 없음. LCD 인스턴스를 사용합니다.
 * 출력: System Ready와 WAIT_OBJECT 문구를 표시합니다.
 * 처리 구조: 화면을 지운 뒤 첫째·둘째 줄의 커서를 설정하고 안내 문구를 출력합니다.
 */
void lcdShowReady() {
  lcd.clear();
  lcd.setCursor(0, 0);
  lcd.print("System Ready");
  lcd.setCursor(0, 1);
  lcd.print("WAIT_OBJECT");
}

/**
 * 기능: 네 개 쓰레기통 서보를 닫힘 위치로 복귀시킵니다.
 * 입력: 없음. 연결된 servo1~4를 사용합니다.
 * 출력: 모든 서보에 SERVO_HOME 각도를 기록합니다.
 * 처리 구조: 시작·오류·상태 전환 때 다른 뚜껑이 열려 있지 않도록 공통 복귀 동작을 수행합니다.
 */
void resetAllHome() {
  servo1.write(SERVO_HOME);
  servo2.write(SERVO_HOME);
  servo3.write(SERVO_HOME);
  servo4.write(SERVO_HOME);
}

/**
 * 기능: 선택한 쓰레기 종류의 서보를 지정 각도로 이동합니다.
 * 입력: idx: 서보 번호 1~4, angle: 목표 각도.
 * 출력: 해당 Servo 인스턴스의 write()를 호출합니다.
 * 처리 구조: 번호를 switch로 구분하고 해당하는 서보 한 개만 제어합니다.
 */
void moveServoIdx(int idx, int angle) {
  switch (idx) {
    case 1: servo1.write(angle); break;
    case 2: servo2.write(angle); break;
    case 3: servo3.write(angle); break;
    case 4: servo4.write(angle); break;
  }
}

/**
 * 기능: 물체 감지용 초음파 센서의 echo 길이를 거리로 변환합니다.
 * 입력: 없음. trigger/echo 핀과 micros()를 사용합니다.
 * 출력: cm 단위 거리, timeout·측정 범위 초과이면 999.0.
 * 처리 구조: 10 us trigger 이후 echo 상승·하강을 각각 제한 시간 안에 읽고 펄스 길이를 58로 나눕니다.
 */
float measureDistanceCm() {
  trigLow();
  delayMicroseconds(2);
  trigHigh();
  delayMicroseconds(10);
  trigLow();

  const unsigned long timeout_us = 25000UL;

  unsigned long t0 = micros();
  while (!echoRead()) {
    if (micros() - t0 > timeout_us) return 999.0f;
  }

  unsigned long tRise = micros();
  while (echoRead()) {
    if (micros() - tRise > timeout_us) return 999.0f;
  }
  unsigned long tFall = micros();

  unsigned long duration = tFall - tRise;
  float cm = duration / 58.0f;
  return (cm > 400.0f) ? 999.0f : cm;
}
