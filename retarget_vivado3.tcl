# Change only the MMCM of a routed timing_test3 checkpoint and write a new
# bitstream, so one Vivado placement and routing runs at another clock.
# Run in a directory holding base.dcp and params.vh:
#
#   vivado -mode batch -nojournal -log vivado.log -source retarget_vivado3.tcl

set f [open params.vh r]
set params [read $f]
close $f
set m mmcm
if {[regexp {`define CASCADE} $params]} { set m mmcm2 }
regexp "`define [string toupper $m]_MULT\\s+(\[0-9.\]+)" $params -> mult
regexp "`define [string toupper $m]_DIV\\s+(\[0-9.\]+)" $params -> div

open_checkpoint base.dcp
set mmcm [get_cells $m]
set_property CLKFBOUT_MULT_F $mult $mmcm
set_property CLKOUT0_DIVIDE_F $div $mmcm
puts "retarget: [get_property REF_NAME $mmcm] CLKFBOUT_MULT_F=[get_property CLKFBOUT_MULT_F $mmcm] CLKOUT0_DIVIDE_F=[get_property CLKOUT0_DIVIDE_F $mmcm]"

report_timing_summary -file timing_test3.vivado_timing.rpt
set fast [get_clocks -of_objects [get_pins bufg_fast/O]]
set path [get_timing_paths -group $fast -max_paths 1 -setup]
set f [open fmax.txt w]
puts $f "[get_property PERIOD $fast] [get_property SLACK $path]"
close $f
write_bitstream -force timing_test3.bit
