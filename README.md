# xc7 timing test: where a build really stops working

A self-checking design and a script that find, on a board and without
anyone watching, the highest clock at which a build still computes
correctly. It was written to compare nextpnr's and Vivado's predicted
Fmax with what a Cmod A7-35T (xc7a35tcpg236-1) actually does. The
measurements so far are in `RESULTS.md`.

## The design (`timing_test3.v`)

An MMCM makes the fast clock from the board's 12 MHz. The design runs
2^20 cycles of an add-rotate-xor computation, `ROUNDS` chained 32-bit adds
per cycle, compares the result with the value `gen_params.py` computed in
Python, and starts again, forever. The critical path is the chain of
adders, so fewer rounds means a higher Fmax.

Once a second, logic on the 12 MHz clock sends a line over the UART
(FTDI channel B, 115200 baud):

    S0005 L1V1 R00007829 F00000000 C001E84

- `S`: line number since configuration;
- `L`: MMCM locked;
- `V`: the fast side answered the snapshot request;
- `R`: runs finished;
- `F`: runs with a wrong result;
- `C`: fast-clock cycles / 256.

Consecutive `C` values, one second of the crystal apart, give the real
fast clock to within a few kHz. The counters change at most once every
256 cycles or once per run, so they keep counting correctly while the
adder path fails. The LEDs show the same as text: green while every run
is correct, red after a wrong one.

`gen_params.py --freq MHz --rounds R` picks the MMCM settings and the
expected result (`--corrupt` flips one bit of it, as a negative control).
With `--cascade` it uses two MMCMs in series (12 → 100 MHz → the fast
clock), for the 384–600 MHz that one MMCM can't make from 12 MHz.

## The script (`tt.py`)

    tt.py build NAME --tool nextpnr|vivado|vivado_yosys --rounds R --freq MHz [options]
    tt.py test NAME [--seconds 30]
    tt.py run NAME ...            build, then test
    tt.py campaign FILE           one "run" per line
    tt.py sweep PREFIX --tool T --rounds R --start MHz --step MHz [--base RUN]
    tt.py summary                 markdown table of every run

`test` loads the bitstream into configuration RAM with openFPGALoader
(never the flash), reads the reports, and decides:

- `PASS`: locked, every report present, the clock and the run rate as
  expected, and no wrong run;
- `FAIL`: the design ran at the right clock but a run was wrong, or it
  finished too few runs;
- `ERROR`: anything else (no reports, wrong clock, programming failed).
  This is a setup problem, not a timing result.

`sweep` steps up from `--start` until two points in a row fail, then
halves the gap between the last pass and the first fail down to 1 MHz.
With `--tool vivado`, `--base RUN` reuses that run's routed checkpoint
and changes only the MMCM (`retarget_vivado3.tcl`), so every point is the
same implementation. nextpnr's placement here doesn't depend on the target
frequency, and `sweep` checks that (`same_placement` in `build.json`).

The tools:

- `--tool nextpnr`: yosys `synth_xilinx -flatten -abc9`, nextpnr,
  fasm2frames, xc7frames2bit.
- `--tool vivado`: Vivado synthesis, place and route (`build_vivado3.tcl`).
- `--tool vivado_yosys`: Vivado places and routes yosys's netlist (EDIF),
  with no Vivado synthesis or `opt_design` (`build_vivado3_yosys.tcl`).
- `--vivado-placement CELLS.tsv` (nextpnr): pins the slice cells where a
  `vivado_yosys` run placed them (`apply_placement.py`). nextpnr doesn't
  build that today; see `RESULTS.md`.

Tools come from the environment: `YOSYS`, `NEXTPNR` (for example
`".../nextpnr-himbaechel --chipdb .../chipdb-xc7a50t.bin"`), `FASM2FRAMES`
(it may be `"python3 .../utils/fasm2frames.py"`), `XC7FRAMES2BIT`,
`PRJXRAY_DB` (the directory holding `artix7/`), `VIVADO`,
`OPENFPGALOADER` and `BOARD` (default `cmoda7_35t`). `test` needs
pyserial, and finds the FT2232's channel B by itself (or `--port`).

## A first run

    pip install pyserial
    cat > check.txt <<'EOF'
    np_r4_100          --tool nextpnr --rounds 4 --freq 100
    np_r4_100_corrupt  --tool nextpnr --rounds 4 --freq 100 --corrupt
    EOF
    python3 tt.py campaign check.txt --seconds 10
    python3 tt.py sweep np_r4 --tool nextpnr --rounds 4 --start 140 --step 4
    python3 tt.py summary

The first must be `PASS` and the second `FAIL` with `control OK` (every
run wrong). Each run lives in `runs/NAME/`, with `build.json`,
`test.json` and the raw `serial.log`.

For another board, change the pins in `timing3.xdc` (12 MHz clock, two
LEDs, the RGB LED, a button and the UART TX), `CLKIN_MHZ` in
`gen_params.py`, the part in `tt.py` and the Tcl scripts, and `TICKS`
and `BAUD_DIV` in `timing_test3.v` if the reference clock isn't 12 MHz.
