# Digilent Cmod A7 (15T and 35T share the CPG236 pinout)
# Pin assignments follow the Digilent Cmod-A7 master XDC.
# One property per line: the nextpnr-xilinx XDC parser is simpler than Vivado's.

# 12 MHz oscillator
set_property PACKAGE_PIN L17 [get_ports clk]
set_property IOSTANDARD LVCMOS33 [get_ports clk]

create_clock -period 83.333 -name sys_clk [get_ports clk]

# User LEDs
set_property PACKAGE_PIN A17 [get_ports {led[0]}]
set_property IOSTANDARD LVCMOS33 [get_ports {led[0]}]
set_property PACKAGE_PIN C16 [get_ports {led[1]}]
set_property IOSTANDARD LVCMOS33 [get_ports {led[1]}]

# RGB LED (active low)
set_property PACKAGE_PIN B17 [get_ports led0_b]
set_property IOSTANDARD LVCMOS33 [get_ports led0_b]
set_property PACKAGE_PIN B16 [get_ports led0_g]
set_property IOSTANDARD LVCMOS33 [get_ports led0_g]
set_property PACKAGE_PIN C17 [get_ports led0_r]
set_property IOSTANDARD LVCMOS33 [get_ports led0_r]

# Buttons
set_property PACKAGE_PIN A18 [get_ports {btn[0]}]
set_property IOSTANDARD LVCMOS33 [get_ports {btn[0]}]
set_property PACKAGE_PIN B18 [get_ports {btn[1]}]
set_property IOSTANDARD LVCMOS33 [get_ports {btn[1]}]

# UART to the PC: FTDI channel B (uart_rxd_out in the Digilent master XDC)
set_property PACKAGE_PIN J18 [get_ports uart_tx]
set_property IOSTANDARD LVCMOS33 [get_ports uart_tx]
