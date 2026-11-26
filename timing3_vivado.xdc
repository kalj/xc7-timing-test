# Vivado only: the 12 MHz reporting logic and the fast clock exchange data
# through a toggle handshake, so their crossing paths are not real paths.
# Without this, Vivado times them between related clocks.
set_clock_groups -asynchronous -group [get_clocks sys_clk] -group [get_clocks -of_objects [get_pins bufg_fast/O]]
