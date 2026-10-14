## ----------------------------------------------------------------------------
## 1. PS GPIO (Software Control) - K18
## ----------------------------------------------------------------------------
set_property -dict {PACKAGE_PIN M14 IOSTANDARD LVCMOS33} [get_ports {GPIO_0_0_tri_io[0]}]
set_property -dict {PACKAGE_PIN K18 IOSTANDARD LVCMOS33} [get_ports {GPIO_0_0_tri_io[1]}]
## ----------------------------------------------------------------------------
## LD5 (RGB LED 5)
## ----------------------------------------------------------------------------
# EMIO[2] -> Red
set_property -dict {PACKAGE_PIN P16 IOSTANDARD LVCMOS33} [get_ports {GPIO_0_0_tri_io[2]}]
# EMIO[3] -> Green
set_property -dict {PACKAGE_PIN K19 IOSTANDARD LVCMOS33} [get_ports {GPIO_0_0_tri_io[3]}]
# EMIO[4] -> Blue
set_property -dict {PACKAGE_PIN Y16 IOSTANDARD LVCMOS33} [get_ports {GPIO_0_0_tri_io[4]}]

set_property -dict {PACKAGE_PIN V16 IOSTANDARD LVCMOS33} [get_ports {GPIO_0_0_tri_io[5]}]
set_property -dict {PACKAGE_PIN F17 IOSTANDARD LVCMOS33} [get_ports {GPIO_0_0_tri_io[6]}]
set_property -dict {PACKAGE_PIN M17 IOSTANDARD LVCMOS33} [get_ports {GPIO_0_0_tri_io[7]}]

## ----------------------------------------------------------------------------
## 3. Pmod JE (ArduCAM SPI/I2C)
## ----------------------------------------------------------------------------
set_property -dict {PACKAGE_PIN V12 IOSTANDARD LVCMOS33} [get_ports SPI_0_0_ss_io]
set_property -dict {PACKAGE_PIN W16 IOSTANDARD LVCMOS33} [get_ports SPI_0_0_io0_io]
set_property -dict {PACKAGE_PIN J15 IOSTANDARD LVCMOS33} [get_ports SPI_0_0_io1_io]
set_property -dict {PACKAGE_PIN H15 IOSTANDARD LVCMOS33} [get_ports SPI_0_0_sck_io]
set_property -dict {PACKAGE_PIN T14 IOSTANDARD LVTTL} [get_ports IIC_0_0_sda_io]
set_property -dict {PACKAGE_PIN T15 IOSTANDARD LVTTL} [get_ports IIC_0_0_scl_io]

# Dummy Pins
set_property -dict {PACKAGE_PIN T17 IOSTANDARD LVCMOS33} [get_ports SPI_0_0_ss1_o]
set_property -dict {PACKAGE_PIN Y17 IOSTANDARD LVCMOS33} [get_ports SPI_0_0_ss2_o]


set_property PULLTYPE PULLUP [get_ports IIC_0_0_scl_io]
set_property PULLTYPE PULLUP [get_ports IIC_0_0_sda_io]



set_property IOSTANDARD LVCMOS33 [get_ports UART_0_0_rxd]
set_property IOSTANDARD LVCMOS33 [get_ports UART_0_0_txd]
set_property PACKAGE_PIN W14 [get_ports UART_0_0_rxd]
set_property PACKAGE_PIN Y14 [get_ports UART_0_0_txd]
