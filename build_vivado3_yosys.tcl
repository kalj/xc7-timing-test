# Vivado place and route of yosys's netlist of timing_test3, without Vivado
# synthesis or opt_design, so only place and route differ from nextpnr.
# Run in a directory holding timing_test3.edf and the two .xdc files:
#
#   vivado -mode batch -nojournal -log vivado.log -source build_vivado3_yosys.tcl

set top timing_test3
read_edif ${top}.edf
link_design -top $top -part xc7a35tcpg236-1
read_xdc timing3.xdc
read_xdc timing3_vivado.xdc
place_design
route_design

report_utilization    -file ${top}.vivado_util.rpt
report_timing_summary -file ${top}.vivado_timing.rpt
set fast [get_clocks -of_objects [get_pins bufg_fast/O]]
report_timing -group $fast -max_paths 20 -nworst 1 -input_pins \
    -path_type full -file paths.rpt

set path [get_timing_paths -group $fast -max_paths 1 -setup]
set f [open fmax.txt w]
puts $f "[get_property PERIOD $fast] [get_property SLACK $path]"
close $f

set f [open cells.tsv w]
foreach c [lsort [get_cells -hierarchical -filter {IS_PRIMITIVE}]] {
    puts $f "$c\t[get_property REF_NAME $c]\t[get_property LOC $c]/[get_property BEL $c]"
}
close $f

write_checkpoint -force ${top}.routed.dcp
write_bitstream -force ${top}.bit
