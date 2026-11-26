#!/usr/bin/env python3
"""Pin the cells of a yosys JSON netlist where Vivado placed them.

    apply_placement.py NETLIST.json VIVADO_CELLS.tsv OUT.json STATS.json

VIVADO_CELLS.tsv is the cells.tsv that build_vivado3_yosys.tcl writes
(name, type, LOC/BEL) for Vivado's place and route of the same yosys
netlist. Every LUT, flip-flop, SRL and CARRY4 that Vivado placed in a slice
gets nextpnr's BEL attribute, e.g. SLICE_X5Y104/SLICEL.AFF becomes
SLICE_X5Y104/AFF. Other cells (IO, BUFG, MMCM) are left for nextpnr.
STATS.json says how many cells of each type were pinned and which slice
cells had no match.
"""
import collections
import json
import re
import sys

SLICE_TYPES = re.compile(r"^(LUT[1-6]|FD[RSCP]E|SRL16E|SRLC32E|CARRY4|MUXF[78])$")
LOCBEL = re.compile(r"^(SLICE_X\d+Y\d+)/SLICE[LM]\.(\w+)$")


def main():
    fn_in, fn_tsv, fn_out, fn_stats = sys.argv[1:5]
    with open(fn_in) as f:
        j = json.load(f)
    top = [m for m in j["modules"].values()
           if m.get("attributes", {}).get("top")]
    cells = (top[0] if top else list(j["modules"].values())[0])["cells"]

    placed = {}
    with open(fn_tsv) as f:
        for line in f:
            w = line.rstrip("\n").split("\t")
            if len(w) < 3 or not SLICE_TYPES.match(w[1]):
                continue
            m = LOCBEL.match(w[2])
            if m:
                # Vivado's Tcl prints a backslash in a name as two
                placed[w[0].replace("\\\\", "\\")] = "{}/{}".format(*m.groups())

    pinned, unmatched = collections.Counter(), []
    for name, bel in sorted(placed.items()):
        c = cells.get(name)
        if c is None:
            unmatched.append(name)
            continue
        c.setdefault("attributes", {})["BEL"] = bel
        pinned[c["type"]] += 1
    stats = {"vivado_slice_cells": len(placed), "pinned": sum(pinned.values()),
             "pinned_by_type": dict(pinned), "unmatched": unmatched}
    with open(fn_out, "w") as f:
        json.dump(j, f)
    with open(fn_stats, "w") as f:
        json.dump(stats, f, indent=1)
    print("pinned {} of {} Vivado slice cells, {} unmatched".format(
        stats["pinned"], len(placed), len(unmatched)))
    sys.exit(0 if len(unmatched) <= len(placed) // 50 else 1)


if __name__ == "__main__":
    main()
