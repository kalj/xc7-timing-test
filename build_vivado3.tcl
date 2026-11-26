# Vivado build of timing_test3 for the Cmod A7, run in a directory holding
# timing_test3.v, params.vh, timing3.xdc and timing3_vivado.xdc:
#
#   vivado -mode batch -nojournal -log vivado.log -source build_vivado3.tcl

set top timing_test3
read_verilog ${top}.v
read_xdc timing3.xdc
read_xdc timing3_vivado.xdc
synth_design -top $top -part xc7a35tcpg236-1 -include_dirs .
opt_design
place_design
route_design

report_utilization    -file ${top}.vivado_util.rpt
report_timing_summary -file ${top}.vivado_timing.rpt
set fast [get_clocks -of_objects [get_pins bufg_fast/O]]
report_timing -group $fast -max_paths 20 -nworst 1 -input_pins \
    -path_type full -file paths.rpt

# Predicted Fmax of the fast clock: 1000 / (period - WNS)
set path [get_timing_paths -group $fast -max_paths 1 -setup]
set f [open fmax.txt w]
puts $f "[get_property PERIOD $fast] [get_property SLACK $path]"
close $f

# Placement of every cell, for comparing with nextpnr's cells.tsv
set f [open cells.tsv w]
foreach c [lsort [get_cells -hierarchical -filter {IS_PRIMITIVE}]] {
    puts $f "$c\t[get_property REF_NAME $c]\t[get_property LOC $c]/[get_property BEL $c]"
}
close $f

write_checkpoint -force ${top}.routed.dcp
write_bitstream -force ${top}.bit
