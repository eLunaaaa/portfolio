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

float measureDistanceCm();
void resetAllHome();
void moveServoIdx(int idx, int angle);
void displayClassOnLCD(char c);
void lcdShowReady();

static inline void trigLow()  { PORTB &= ~(1 << PB4); }
static inline void trigHigh() { PORTB |=  (1 << PB4); }
static inline uint8_t echoRead() { return (PIND & (1 << PD4)) != 0; }

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

void lcdShowReady() {
  lcd.clear();
  lcd.setCursor(0, 0);
  lcd.print("System Ready");
  lcd.setCursor(0, 1);
  lcd.print("WAIT_OBJECT");
}

void resetAllHome() {
  servo1.write(SERVO_HOME);
  servo2.write(SERVO_HOME);
  servo3.write(SERVO_HOME);
  servo4.write(SERVO_HOME);
}

void moveServoIdx(int idx, int angle) {
  switch (idx) {
    case 1: servo1.write(angle); break;
    case 2: servo2.write(angle); break;
    case 3: servo3.write(angle); break;
    case 4: servo4.write(angle); break;
  }
}

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
