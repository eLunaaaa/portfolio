/* PC 테스트 전용 AVR 레지스터 대역입니다. register 변수와 bit 상수로 Slave 코드를 그대로 호출해 buffer·결과·ACK 상태를 검사합니다. */

#pragma once
#include <stdint.h>
// Register stand-ins for the native worker state-machine test only.
static uint8_t PORTD, DDRD, DDRB, TCCR1A, TCCR1B, TCCR2A, TCCR2B;
static uint8_t OCR2A, TCNT2, TIMSK2, UBRR0H, UBRR0L, UCSR0A, UCSR0B, UCSR0C;
static uint8_t TWAR, TWCR, TWSR, TWDR;
static uint16_t ICR1, OCR1A;
enum {PD2=2,PB1=1,WGM11=1,COM1A1=7,WGM13=4,WGM12=3,CS11=1,CS10=0,
 WGM21=1,CS22=2,OCIE2A=1,U2X0=1,TXEN0=3,UCSZ01=2,UCSZ00=1,
 TWGCE=0,TWEN=2,TWIE=0,TWEA=6,TWINT=7,TWSTO=4};
