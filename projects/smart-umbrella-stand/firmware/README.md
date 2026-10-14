# Firmware integration

이 저장소는 보고서에 포함된 사용자 작성 로직을 보드별 모듈로 정리한 공개본이다. STM32CubeMX가 생성하는 startup, linker script, HAL 드라이버 전체는 중복 배포하지 않는다. 각 보드에서 STM32F103, SYSCLK 64 MHz 프로젝트를 만든 뒤 해당 `Core/Inc`, `Core/Src` 파일과 `firmware/common`을 추가한다.

- Slave1: ADC1 scan + circular DMA로 CH0(수위), CH1(습도)를 취득한다. SPI1 slave는 6바이트 센서 프레임을 폴링 교환한다.
- Master: SPI1 master는 DMA1 Channel2/3으로 Slave1 프레임을 동기식 교환한다. SPI2 master는 Slave2와 폴링 교환하며 `HAL_GetTick()` 기반 타임아웃을 둔다.
- Slave2: SPI1 slave는 NSS 하강 시 `slave2_spi_on_select()` 반환값을 DR에 먼저 넣고, RXNE 인터럽트마다 `slave2_spi_on_byte()`의 반환값을 다음 TX 바이트로 적재한다.

핀과 CubeMX 설정은 `docs/TECHNICAL_NOTES.md`를 참고한다. 실제 보드별 `.ioc`가 확보되면 이 모듈 위에 생성 코드를 덧붙이는 방식이 권장된다.

