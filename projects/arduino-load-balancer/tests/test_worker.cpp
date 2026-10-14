#include <cassert>
#include <cstdio>
#define DEMO_MUL_MS 0
#define DEMO_ADD_MS 0
#include "../firmware/slave/slave.ino"

static void receive(const uint8_t *data, uint8_t len) {
    TWSR=0x60; TWI_vect();
    for (uint8_t i=0;i<len;++i) {TWSR=0x80;TWDR=data[i];TWI_vect();}
    TWSR=0xa0;TWI_vect();
}
static void job(uint16_t sequence, uint8_t patch, int8_t value) {
    uint8_t bytes[lb::JobBytes]={lb::CmdJob};
    lb::put16(bytes+1,sequence);bytes[3]=patch;
    for (int i=0;i<9;++i) {bytes[4+i]=(uint8_t)value;bytes[13+i]=1;}
    receive(bytes,sizeof(bytes));
}
static void read_result(uint8_t bytes[lb::ResultBytes]) {
    TWSR=0xa8;TWI_vect();bytes[0]=TWDR;
    for (uint8_t i=1;i<lb::ResultBytes;++i) {TWSR=0xb8;TWI_vect();bytes[i]=TWDR;}
    TWSR=0xc0;TWI_vect();
}
static void ack(uint16_t sequence,uint8_t patch) {
    uint8_t bytes[]={lb::CmdAck,0,0,patch};lb::put16(bytes+1,sequence);receive(bytes,4);
}
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
