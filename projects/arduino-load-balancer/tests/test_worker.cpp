/*
 * 기능 검산용 테스트 파일입니다.
 * 실제 Slave 코드에 register 대역을 연결해 수신·결과·ACK 수명주기를 검사합니다.
 */

#include <cassert>
#include <cstdio>
#define DEMO_MUL_MS 0
#define DEMO_ADD_MS 0
#include "../firmware/slave/slave.ino"

/**
 * 기능: 테스트 packet의 TWI 수신 상태를 차례로 발생시킵니다.
 * 입력: 테스트가 지정한 packet·길이 또는 작업 식별자.
 * 출력: Slave 대역의 수신·송신·slot 상태 변화.
 * 처리 구조: 실제 Slave 함수에 가짜 register 값을 전달해 프로토콜 흐름을 검사합니다.
 */
static void receive(const uint8_t *data, uint8_t len) {
    TWSR=0x60; TWI_vect();
    for (uint8_t i=0;i<len;++i) {TWSR=0x80;TWDR=data[i];TWI_vect();}
    TWSR=0xa0;TWI_vect();
}
/**
 * 기능: 식별자·값을 넣은 합성곱 job frame을 만들어 Slave 대역에 전달합니다.
 * 입력: 테스트가 지정한 packet·길이 또는 작업 식별자.
 * 출력: Slave 대역의 수신·송신·slot 상태 변화.
 * 처리 구조: 실제 Slave 함수에 가짜 register 값을 전달해 프로토콜 흐름을 검사합니다.
 */
static void job(uint16_t sequence, uint8_t patch, int8_t value) {
    uint8_t bytes[lb::JobBytes]={lb::CmdJob};
    lb::put16(bytes+1,sequence);bytes[3]=patch;
    for (int i=0;i<9;++i) {bytes[4+i]=(uint8_t)value;bytes[13+i]=1;}
    receive(bytes,sizeof(bytes));
}
/**
 * 기능: TWI 읽기 상태를 발생시켜 결과 8바이트 snapshot을 가져옵니다.
 * 입력: 테스트가 지정한 packet·길이 또는 작업 식별자.
 * 출력: Slave 대역의 수신·송신·slot 상태 변화.
 * 처리 구조: 실제 Slave 함수에 가짜 register 값을 전달해 프로토콜 흐름을 검사합니다.
 */
static void read_result(uint8_t bytes[lb::ResultBytes]) {
    TWSR=0xa8;TWI_vect();bytes[0]=TWDR;
    for (uint8_t i=1;i<lb::ResultBytes;++i) {TWSR=0xb8;TWI_vect();bytes[i]=TWDR;}
    TWSR=0xc0;TWI_vect();
}
/**
 * 기능: 지정 결과의 ACK frame을 만들어 slot 해제를 요청합니다.
 * 입력: 테스트가 지정한 packet·길이 또는 작업 식별자.
 * 출력: Slave 대역의 수신·송신·slot 상태 변화.
 * 처리 구조: 실제 Slave 함수에 가짜 register 값을 전달해 프로토콜 흐름을 검사합니다.
 */
static void ack(uint16_t sequence,uint8_t patch) {
    uint8_t bytes[]={lb::CmdAck,0,0,patch};lb::put16(bytes+1,sequence);receive(bytes,4);
}
/**
 * 기능: 이 파일의 기능 검산을 실행합니다.
 * 입력: 고정 표본·경계값과 필요 시 대역 환경.
 * 출력: 검사가 모두 맞으면 성공 로그와 정상 종료, 다르면 assertion 실패.
 * 처리 구조: 테스트 함수 또는 프로토콜 시나리오를 순서대로 실행합니다.
 */
int main() {
    setup();
    assert(ICR1==4999 && OCR1A==250);
    job(400,0,3);job(401,1,-4);
    assert(ready_count==2 && total_load==52);
    job(400,0,3);assert(ready_count==2); // duplicate
    job(402,2,7);assert(ready_count==2); // no free slot
    loop();loop();assert(result_count==2 && total_load==0);
    uint8_t first[8], again[8];read_result(first);read_result(again);
    assert(memcmp(first,again,8)==0 && result_count==2);
    assert(first[0]==lb::ResultHeader && lb::get16(first+1)==400 && lb::get32(first+4)==27);
    ack(999,0);assert(result_count==2);
    ack(400,0);assert(result_count==1);
    job(402,2,7);loop();assert(result_count==2);
    read_result(first);assert(lb::get16(first+1)==401 && lb::get32(first+4)==-36);
    ack(401,1);read_result(first);assert(lb::get16(first+1)==402 && lb::get32(first+4)==63);
    ack(402,2);assert(result_count==0 && !(DDRD & (1<<PD2)));
    job(403,99,1);assert(ready_count==0);
    uint8_t too_long[23]={lb::CmdJob};receive(too_long,23);assert(ready_count==0);
    puts("Worker double-buffer, stable reads, explicit ACK and malformed frame tests passed");
}
