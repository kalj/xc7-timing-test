# Results on a Cmod A7-35T

Measured on 2026-11-26 on one Cmod A7-35T (xc7a35tcpg236-1), at room
temperature, with 10 s or 30 s of runs per point. "Works up to" is the
highest clock at which every run was correct. The clock was measured by
the design itself against the board's crystal, and matched the requested
value at every point.

## Tools

- nextpnr: openXC7/nextpnr main at 7077312 (1.0.0-396-g7077312a), with the
  chipdb built for die xc7a50t from openXC7/prjxray-db b12466df, the
  revision openXC7's `demos.yml` pins.
- yosys 0.66 (Debian), `synth_xilinx -flatten -abc9`.
- fasm2frames from openXC7/prjxray 6dabf706.
- Vivado 2025.2. Each Vivado column is one placement and routing, run at
  every clock by changing only the MMCM in the routed checkpoint.

## Fmax, predicted and on the board (MHz)

```
                         nextpnr     nextpnr      Vivado      Vivado
                         predicted   works up to  predicted   works up to
ROUNDS=4                   142.8       147          125.8       244
ROUNDS=4, timingweight     140.7       165
ROUNDS=2                   215.5       253          205.8       391.7 (a)
ROUNDS=2, timingweight     194.3       228
```

"timingweight" is `--placer-heap-timingweight 60`. (a) With two MMCMs in
series (`--cascade`), since one can't make 384–600 MHz from 12 MHz.

Vivado placing and routing yosys's netlist (`--tool vivado_yosys`, no
Vivado synthesis or `opt_design`):

```
                         Vivado      Vivado
                         predicted   works up to
ROUNDS=4                   126.3       252
ROUNDS=2 (a)               204.7       400
```

## Findings

1. **nextpnr's predicted Fmax leaves little margin.** At ROUNDS=4 the
   build fails 3% above its own prediction, at room temperature; at
   ROUNDS=2, 17% above. Vivado's predictions leave about 1.9×.
2. **The gap between the tools is in place and route, not in yosys.**
   Vivado reaches 252 / 400 MHz on yosys's netlist, at least as much as on
   its own synthesis (244 / 391.7), against nextpnr's 147 / 253.
3. **Placement.** nextpnr packs the design into a block 9 slices wide,
   with 20–25 CARRY4s stacked in each of 4 columns. Vivado gives each
   32-bit adder its own column, 8 CARRY4s tall, across 10 columns. In
   nextpnr's ROUNDS=4 build (7.00 ns), the path between the two carry
   chains on the critical path has two LUTs and 1.48 ns of routing.
4. **`--placer-heap-timingweight 60`** helps at ROUNDS=4 (147 → 165 MHz)
   and hurts at ROUNDS=2 (253 → 228 MHz).
5. **One build's result moves with small changes.** Adding a second MMCM
   to the ROUNDS=2 design moved nextpnr's edge from 253 to 280 MHz, and
   Vivado's from 391.7 to 400.
6. **nextpnr can't take Vivado's placement.** Pinning every slice cell
   where Vivado put it (`--vivado-placement`) doesn't build:
   - `xilinx_place.cc` refuses a main FF fed over the bypass next to a
     used 5FF, because its router can't set that FF's input mux. Vivado
     uses the pairing (reproducer: `examples/cmoda7-bypass-5ff` on branch
     `xilinx-bypass-5ff-example` of kalj/nextpnr).
   - `pack_carry.cc` re-creates every CARRY4 without its `BEL`, so
     `check_user_bels()` stops the build. LUTs pinned in a carry chain's
     cluster are moved with it.
7. **No clock-skew data for this die.** nextpnr logs `clock-tree: no
   clock delays measured for xc7a50t, global clocks keep their routed
   delay`.

Debian's nextpnr 0.11.1 also runs a `CLKFBOUT_MULT_F` ending in .875 one
multiplier step slow: it takes an unset `*_PHASE` as 1°. openXC7/nextpnr
main has fixed that.
