#!/usr/bin/env python3
"""Build and test timing_test3 on the Cmod A7 without anyone watching.

    tt.py build NAME --tool nextpnr|vivado --rounds R --freq MHz [--corrupt]
                [--nextpnr-flags "..."]
    tt.py test NAME [--seconds 30] [--port /dev/ttyUSBn]
    tt.py run NAME ...                 build, then test
    tt.py campaign FILE [--seconds 30] one "run" per line of FILE
    tt.py sweep PREFIX --tool T --rounds R --start MHz --step MHz [--max MHz]
                [--base RUN] [--nextpnr-flags "..."] [--seconds 10]
    tt.py summary                      markdown table of every run

--fasm-phase0 (nextpnr only) rewrites every MMCME2_ADV *_CLKOUT1_PHASE_MUX
in nextpnr's FASM to 0 before fasm2frames. nextpnr 0.11 writes the MMCM's
phase as if the default were 1 degree instead of 0; the design uses no
phase shift, so 0 is what Vivado writes. build.json records how many lines
were changed ("fasm_phase0_lines").

Each run lives in runs/NAME/. build writes build.json, test writes
test.json and serial.log. A FILE line is the arguments of "run", e.g.
    np_r4_152_tw60 --tool nextpnr --rounds 4 --freq 152 --nextpnr-flags "--placer-heap-timingweight 60"
Blank lines and lines starting with # are skipped.

--tool vivado_yosys: yosys synthesises (as for nextpnr) and writes EDIF;
Vivado places and routes that netlist, with no Vivado synthesis or
opt_design. --cascade: two MMCMs in series (12 -> 100 MHz -> the fast
clock), for clocks between 384 and 600 MHz that one MMCM can't make from
12 MHz.

--vivado-placement CELLS.tsv (nextpnr only): pin every slice cell where
Vivado placed it in that cells.tsv (from a vivado_yosys run of the same
design), so nextpnr only routes, and places what Vivado's netlist doesn't
have (IO, clocks, cells nextpnr's packer adds). Where nextpnr refuses a
pinned slice ("illegal placement in logic tile"), the cells it names are
unpinned and nextpnr runs again, up to MAX_UNPIN_TRIES times; build.json
lists them ("unpinned"). The same for a pinned cell that legalisation moved
("has BEL ..., but the placement put it at ...").

build --tool vivado --base RUN does not place and route again: it takes
RUN's routed checkpoint, changes only the MMCM settings, and writes a new
bitstream. So the same implementation runs at another clock.

sweep finds where one implementation stops working. It builds and tests
PREFIX_<MHz> at start, start+step, ... until two in a row fail (or max),
then halves the gap between the last pass and the first fail down to
1 MHz. If the start fails, it steps down until a pass first. It stops at
the first ERROR. With --tool vivado, every point uses --base (the
checkpoint of one earlier run). With nextpnr, every point is built from
scratch; nextpnr 0.11 places the same whatever the target, and sweep
checks that against the first point's cells.tsv ("same_placement").

Tools come from the environment, with these defaults:
    YOSYS=yosys  NEXTPNR=nextpnr-himbaechel-xilinx  FASM2FRAMES=fasm2frames
    XC7FRAMES2BIT=xc7frames2bit  PRJXRAY_DB=/usr/share/prjxray/database
    VIVADO=vivado  OPENFPGALOADER=openFPGALoader  BOARD=cmoda7_35t
FASM2FRAMES may be a command with arguments ("python3 .../fasm2frames.py").

test loads the bitstream into the FPGA's configuration RAM (not the flash),
reads the design's once-a-second report from the FTDI's second channel, and
decides:
    PASS   locked, every report answered, the clock and the run rate as
           expected, and no wrong run
    FAIL   the design ran at the right clock but some run was wrong, or it
           finished too few runs (the logic itself misbehaved)
    ERROR  anything else: no reports, not locked, wrong clock, programming
           failed. This is a problem with the setup, not a timing result.
"""
import argparse
import glob
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
PART = "xc7a35tcpg236-1"
TOP = "timing_test3"
SOURCES = ["apply_placement.py", "timing_test3.v", "gen_params.py", "timing3.xdc",
           "timing3_vivado.xdc", "build_vivado3.tcl", "retarget_vivado3.tcl",
           "build_vivado3_yosys.tcl"]
LOG2_ITERS = 20
FREQ_TOL = 0.002          # the measured clock must be within 0.2 %
LINE_RE = re.compile(r"^S([0-9A-F]{4}) L([01])V([01]) R([0-9A-F]{8}) "
                     r"F([0-9A-F]{8}) C([0-9A-F]{6})$")
MAX_UNPIN_TRIES = 150
ILLEGAL_RE = re.compile(r"^ERROR: illegal placement in logic tile \S+: (.*)$", re.M)
QUOTED_RE = re.compile(r"'([^']+)' \(")
MOVED_RE = re.compile(r"^ERROR: cell '([^']+)' has BEL '[^']+', but the placement put it at", re.M)
NEXTPNR_FMAX_RE = re.compile(
    r"Max frequency for clock\s+'([^']+)':\s+([\d.]+) MHz \((PASS|FAIL) at ([\d.]+) MHz\)")


def tool(name, default):
    return shlex.split(os.environ.get(name, default))


def sh(cmd, log, cwd):
    """Run cmd in cwd, appending its output to log. Return the exit code."""
    with open(os.path.join(cwd, log), "a") as f:
        f.write("$ " + " ".join(shlex.quote(c) for c in cmd) + "\n")
        f.flush()
        t = time.time()
        rc = subprocess.call(cmd, cwd=cwd, stdout=f, stderr=subprocess.STDOUT)
        f.write("# exit {} after {:.0f} s\n".format(rc, time.time() - t))
    return rc


def run_dir(name):
    return os.path.join(HERE, "runs", name)


def save(d, fn, obj):
    with open(os.path.join(d, fn), "w") as f:
        json.dump(obj, f, indent=1, sort_keys=True)
        f.write("\n")


def load(d, fn):
    p = os.path.join(d, fn)
    if not os.path.exists(p):
        return None
    with open(p) as f:
        return json.load(f)


def actual_mhz(d):
    with open(os.path.join(d, "params.vh")) as f:
        m = re.search(r"// Fast clock: .* = ([\d.]+) MHz", f.read())
    return float(m.group(1))


# ---------------------------------------------------------------- build

PHASE_MUX_RE = re.compile(
    r"^(\S+\.MMCME2_ADV\.CLK(?:FBOUT|OUT[0-6])_CLKOUT1_PHASE_MUX\[2:0\]) = 3'b(?!000)[01]{3}$")


def fasm_phase0(fn):
    """Set every MMCM CLKOUT1_PHASE_MUX of the FASM file to 0. Return the count."""
    with open(fn) as f:
        lines = f.read().split("\n")
    n = 0
    for i, l in enumerate(lines):
        m = PHASE_MUX_RE.match(l.strip())
        if m:
            lines[i] = m.group(1) + " = 3'b000"
            n += 1
    with open(fn, "w") as f:
        f.write("\n".join(lines))
    return n


def build_nextpnr(d, flags, phase0=False, vplace=None):
    r = {}
    rc = sh(tool("YOSYS", "yosys") + [
        "-q", "-l", "yosys.log", "-p",
        "synth_xilinx -flatten -abc9 -arch xc7 -top {0}; write_json {0}.json".format(TOP),
        TOP + ".v"], "build.log", d)
    if rc:
        return dict(r, error="yosys exit {}".format(rc))
    if vplace:
        shutil.copy(vplace, os.path.join(d, "vivado_cells.tsv"))
        os.rename(os.path.join(d, TOP + ".json"), os.path.join(d, TOP + ".unplaced.json"))
        rc = sh([sys.executable, "apply_placement.py", TOP + ".unplaced.json",
                 "vivado_cells.tsv", TOP + ".json", "placement_stats.json"], "build.log", d)
        st = load(d, "placement_stats.json") or {}
        r["vivado_placement"] = {k: st.get(k) for k in ("vivado_slice_cells", "pinned")}
        if rc:
            return dict(r, error="apply_placement: too many cells unmatched")
    unpinned = []
    for attempt in range(MAX_UNPIN_TRIES + 1):
        rc = sh(tool("NEXTPNR", "nextpnr-himbaechel-xilinx") + [
            "--device", PART, "--timing-allow-fail"] + shlex.split(flags) + [
            "-o", "xdc=timing3.xdc", "-o", "fasm={}.fasm".format(TOP),
            "--json", TOP + ".json", "--write", TOP + ".routed.json",
            "--report", "report.json", "--log", "nextpnr.log"], "build.log", d)
        if rc == 0 or not vplace:
            break
        with open(os.path.join(d, "nextpnr.log")) as f:
            log = f.read()
        m = ILLEGAL_RE.search(log)
        names = QUOTED_RE.findall(m.group(1)) if m else MOVED_RE.findall(log)
        if not names:
            break
        fn = os.path.join(d, TOP + ".json")
        with open(fn) as f:
            j = json.load(f)
        mods = [x for x in j["modules"].values() if x.get("attributes", {}).get("top")]
        cells = (mods[0] if mods else list(j["modules"].values())[0])["cells"]
        mod = mods[0] if mods else list(j["modules"].values())[0]
        bitname = {}
        for nn, info in mod.get("netnames", {}).items():
            for k, bit in enumerate(info.get("bits", [])):
                bitname.setdefault(bit, "{}[{}]".format(nn, k))
        changed = False
        for n in names:
            if "BEL" in cells.get(n, {}).get("attributes", {}):
                q = cells[n].get("connections", {}).get("Q", [None])[0]
                unpinned.append([n, cells[n]["attributes"]["BEL"], bitname.get(q, "")])
                del cells[n]["attributes"]["BEL"]
                changed = True
        if not changed:
            break
        with open(fn, "w") as f:
            json.dump(j, f)
    if vplace:
        r["unpinned"] = unpinned
        r["vivado_placement"]["unpinned"] = len(unpinned)
    if rc:
        return dict(r, error="nextpnr exit {}".format(rc))
    fmax = {}
    with open(os.path.join(d, "nextpnr.log")) as f:
        for m in NEXTPNR_FMAX_RE.finditer(f.read()):
            fmax[m.group(1)] = float(m.group(2))     # the last report wins
    r["predicted_fmax_mhz"] = fmax
    dump_nextpnr_cells(d)
    if phase0:
        shutil.copy(os.path.join(d, TOP + ".fasm"), os.path.join(d, TOP + ".orig.fasm"))
        r["fasm_phase0_lines"] = fasm_phase0(os.path.join(d, TOP + ".fasm"))
    db = os.environ.get("PRJXRAY_DB", "/usr/share/prjxray/database")
    with open(os.path.join(d, TOP + ".frames"), "w") as out:
        cmd = tool("FASM2FRAMES", "fasm2frames") + [
            "--part", PART, "--db-root", db + "/artix7", TOP + ".fasm"]
        with open(os.path.join(d, "build.log"), "a") as f:
            f.write("$ " + " ".join(cmd) + " > {}.frames\n".format(TOP))
        rc = subprocess.call(cmd, cwd=d, stdout=out,
                             stderr=open(os.path.join(d, "fasm2frames.err"), "w"))
    if rc:
        return dict(r, error="fasm2frames exit {}".format(rc))
    rc = sh(tool("XC7FRAMES2BIT", "xc7frames2bit") + [
        "--part_file", "{}/artix7/{}/part.yaml".format(db, PART),
        "--part_name", PART, "--frm_file", TOP + ".frames",
        "--output_file", TOP + ".bit"], "build.log", d)
    if rc:
        return dict(r, error="xc7frames2bit exit {}".format(rc))
    return r


def dump_nextpnr_cells(d):
    """cells.tsv: every cell of the routed design with its type and bel."""
    try:
        with open(os.path.join(d, TOP + ".routed.json")) as f:
            j = json.load(f)
    except (OSError, ValueError):
        return
    with open(os.path.join(d, "cells.tsv"), "w") as out:
        for mod in j.get("modules", {}).values():
            for name, c in sorted(mod.get("cells", {}).items()):
                bel = c.get("attributes", {}).get("NEXTPNR_BEL", "")
                out.write("{}\t{}\t{}\n".format(name, c.get("type", ""), bel))


def build_vivado(d, base=None, yosys=False):
    script = "build_vivado3.tcl"
    if yosys and not base:
        script = "build_vivado3_yosys.tcl"
        rc = sh(tool("YOSYS", "yosys") + [
            "-q", "-l", "yosys.log", "-p",
            "synth_xilinx -flatten -abc9 -arch xc7 -top {0}; write_edif -pvector bra {0}.edf".format(TOP),
            TOP + ".v"], "build.log", d)
        if rc:
            return {"error": "yosys exit {}".format(rc)}
    if base:
        script = "retarget_vivado3.tcl"
        shutil.copy(os.path.join(run_dir(base), TOP + ".routed.dcp"),
                    os.path.join(d, "base.dcp"))
    rc = sh(tool("VIVADO", "vivado") + [
        "-mode", "batch", "-nojournal", "-log", "vivado.log",
        "-source", script], "build.log", d)
    r = {}
    p = os.path.join(d, "fmax.txt")
    if os.path.exists(p):
        with open(p) as f:
            w = f.read().split()
        period, wns = float(w[0]), float(w[1])
        r.update(period_ns=period, wns_ns=wns,
                 predicted_fmax_mhz={"clk_fast": round(1000 / (period - wns), 1)})
    if rc or not os.path.exists(os.path.join(d, TOP + ".bit")):
        r["error"] = "vivado exit {}".format(rc)
    return r


def cmd_build(a):
    d = run_dir(a.name)
    if os.path.exists(d):
        sys.exit("{} exists; use a new name".format(d))
    os.makedirs(d)
    for s in SOURCES:
        shutil.copy(os.path.join(HERE, s), d)
    gen = [sys.executable, "gen_params.py", "--freq", str(a.freq),
           "--rounds", str(a.rounds)] + (["--corrupt"] if a.corrupt else []) + (
           ["--cascade"] if getattr(a, "cascade", False) else [])
    with open(os.path.join(d, "params.vh"), "w") as f:
        subprocess.check_call(gen, cwd=d, stdout=f)
    t = time.time()
    base = getattr(a, "base", None)
    if a.tool == "nextpnr":
        if base:
            sys.exit("--base is for Vivado only")
        vp = getattr(a, "vivado_placement", None)
        r = build_nextpnr(d, a.nextpnr_flags, getattr(a, "fasm_phase0", False),
                          os.path.abspath(vp) if vp else None)
    else:
        r = build_vivado(d, base, yosys=a.tool == "vivado_yosys")
    r.update(name=a.name, tool=a.tool, rounds=a.rounds, freq=a.freq,
             corrupt=a.corrupt, nextpnr_flags=a.nextpnr_flags, base=base,
             cascade=getattr(a, "cascade", False),
             actual_mhz=actual_mhz(d), build_s=round(time.time() - t))
    if "error" not in r and not os.path.exists(os.path.join(d, TOP + ".bit")):
        r["error"] = "no bitstream"
    save(d, "build.json", r)
    print(json.dumps(r, sort_keys=True))
    return "error" not in r


# ---------------------------------------------------------------- test

def find_port():
    from serial.tools import list_ports
    ports = [p for p in list_ports.comports()
             if p.vid == 0x0403 and p.pid == 0x6010
             and (p.location or "").endswith(".1")]
    if len(ports) != 1:
        sys.exit("expected one FT2232 channel B, found: {}".format(
            [(p.device, p.location) for p in list_ports.comports()]))
    return ports[0].device


def cmd_test(a):
    import serial
    d = run_dir(a.name)
    b = load(d, "build.json")
    port = a.port or find_port()
    expect_hz = actual_mhz(d) * 1e6
    res = {"name": a.name, "port": port, "seconds": a.seconds,
           "expected_mhz": expect_hz / 1e6}

    rc = sh(tool("OPENFPGALOADER", "openFPGALoader") + [
        "-b", os.environ.get("BOARD", "cmoda7_35t"), TOP + ".bit"],
        "program.log", d)
    t_prog = time.time()
    res["program_exit"] = rc
    lines = []
    if rc == 0:
        ser = None
        for _ in range(20):                     # the port may re-appear
            try:
                ser = serial.Serial(port, 115200, timeout=0.5)
                break
            except serial.SerialException:
                time.sleep(0.25)
        if ser is None:
            res["error"] = "cannot open " + port
        else:
            ser.reset_input_buffer()
            buf = b""
            with open(os.path.join(d, "serial.log"), "w") as log:
                while time.time() - t_prog < a.seconds + 1.5:
                    buf += ser.read(256)
                    while b"\n" in buf:
                        raw, buf = buf.split(b"\n", 1)
                        s = raw.decode("ascii", "replace").strip()
                        log.write("{:.3f} {}\n".format(time.time() - t_prog, s))
                        lines.append((time.time() - t_prog, s))
            ser.close()
    res.update(judge(lines, expect_hz))
    if rc:
        res["verdict"], res["error"] = "ERROR", "openFPGALoader exit {}".format(rc)
    if b and b.get("corrupt"):
        # A negative control is correct when every run fails.
        res["control_ok"] = (res.get("runs", 0) > 0
                             and res.get("fail_runs") == res.get("runs"))
    save(d, "test.json", res)
    print(json.dumps(res, sort_keys=True))
    return res.get("verdict") != "ERROR"


def judge(lines, expect_hz):
    recs, bad = [], 0
    for t, s in lines:
        m = LINE_RE.match(s)
        if not m:
            if recs:            # the first line read may have been cut off
                bad += 1
            continue
        recs.append(dict(t=t, seq=int(m.group(1), 16), locked=m.group(2) == "1",
                         valid=m.group(3) == "1", runs=int(m.group(4), 16),
                         fail=int(m.group(5), 16), fc=int(m.group(6), 16)))
    r = {"lines": len(recs), "garbled_lines": bad}
    # The first lines after configuration describe snapshots taken before the
    # first report, so freq and run rate use the lines from the third one on.
    use = [x for x in recs if x["seq"] >= 3]
    if not recs or len(use) < 3:
        return dict(r, verdict="ERROR", error="fewer than 5 reports")
    r["first_seq"] = recs[0]["seq"]
    last = use[-1]
    r.update(runs=last["runs"], fail_runs=last["fail"],
             locked=all(x["locked"] for x in use),
             valid=all(x["valid"] for x in use))
    seq_ok = all(y["seq"] == x["seq"] + 1 for x, y in zip(use, use[1:]))
    freqs, rates = [], []
    for x, y in zip(use, use[1:]):
        if y["seq"] == x["seq"] + 1:
            freqs.append(((y["fc"] - x["fc"]) % (1 << 24)) * 256.0)
            rates.append(y["runs"] - x["runs"])
    f = sum(freqs) / len(freqs)
    r.update(measured_mhz=round(f / 1e6, 4),
             measured_min_mhz=round(min(freqs) / 1e6, 4),
             measured_max_mhz=round(max(freqs) / 1e6, 4),
             freq_error_ppm=round((f / expect_hz - 1) * 1e6),
             runs_per_s_min=min(rates), runs_per_s_max=max(rates),
             runs_per_s_expected=round(f / ((1 << LOG2_ITERS) + 1), 1),
             seq_contiguous=seq_ok)
    if last["runs"]:
        r["fail_fraction"] = last["fail"] / last["runs"]
    # A stale design (programming had no effect) would not restart at S0001.
    fresh = recs[0]["seq"] <= 3
    r["fresh_after_program"] = fresh
    if not (r["locked"] and r["valid"] and fresh and seq_ok and not bad):
        r["verdict"] = "ERROR"
    elif abs(f / expect_hz - 1) > FREQ_TOL:
        r["verdict"] = "ERROR"
        r["error"] = "clock not the expected frequency"
    elif last["fail"] or min(rates) < r["runs_per_s_expected"] - 2:
        r["verdict"] = "FAIL"
    else:
        r["verdict"] = "PASS"
    return r


# ---------------------------------------------------------------- campaign

def add_build_args(p):
    p.add_argument("name")
    p.add_argument("--tool", choices=["nextpnr", "vivado", "vivado_yosys"], required=True)
    p.add_argument("--cascade", action="store_true")
    p.add_argument("--rounds", type=int, required=True)
    p.add_argument("--freq", type=float, required=True)
    p.add_argument("--corrupt", action="store_true")
    p.add_argument("--nextpnr-flags", default="")
    p.add_argument("--base", help="Vivado: reuse this run's routed checkpoint")
    p.add_argument("--vivado-placement", help="nextpnr: pin slice cells as in this cells.tsv")
    p.add_argument("--fasm-phase0", action="store_true",
                   help="nextpnr: set the MMCM PHASE_MUX features in the FASM to 0")


def cmd_run(a):
    if cmd_build(a):
        return cmd_test(a)
    return False


def cmd_campaign(a):
    p = argparse.ArgumentParser(prog="campaign line")
    add_build_args(p)
    with open(a.file) as f:
        jobs = [l.strip() for l in f if l.strip() and not l.lstrip().startswith("#")]
    for l in jobs:
        sub = p.parse_args(shlex.split(l))
        sub.seconds, sub.port = a.seconds, a.port
        print("=== " + l, flush=True)
        if os.path.exists(run_dir(sub.name)):
            print("skip: already exists", flush=True)
            continue
        cmd_run(sub)


def placement(name):
    p = os.path.join(run_dir(name), "cells.tsv")
    if not os.path.exists(p):
        return None
    with open(p) as f:
        return sorted((w[0], w[2]) for w in (l.rstrip("\n").split("\t") for l in f)
                      if len(w) >= 3)


def cmd_sweep(a):
    results = {}                     # MHz -> verdict
    first = []

    def point(mhz):
        if mhz in results:
            return results[mhz]
        name = "{}_{:g}".format(a.prefix, mhz)
        sub = argparse.Namespace(name=name, tool=a.tool, rounds=a.rounds,
                                 freq=mhz, corrupt=False,
                                 nextpnr_flags=a.nextpnr_flags, base=a.base,
                                 fasm_phase0=a.fasm_phase0, cascade=a.cascade,
                                 vivado_placement=a.vivado_placement,
                                 seconds=a.seconds, port=a.port)
        print("=== sweep point " + name, flush=True)
        if os.path.exists(run_dir(name)):
            t = load(run_dir(name), "test.json") or {}
            v = t.get("verdict", "ERROR")
        elif not cmd_build(sub):
            v = "ERROR"
        else:
            cmd_test(sub)
            v = (load(run_dir(name), "test.json") or {}).get("verdict", "ERROR")
        if a.tool == "nextpnr":
            if not first:
                first.append(name)
            else:
                b = load(run_dir(name), "build.json") or {}
                b["same_placement"] = placement(name) == placement(first[0])
                save(run_dir(name), "build.json", b)
        results[mhz] = v
        print("=== {} MHz: {}".format(mhz, v), flush=True)
        if v == "ERROR":
            raise SystemExit("sweep stopped: ERROR at {} MHz".format(mhz))
        return v

    f = a.start
    if point(f) != "PASS":
        while f - a.step > 0 and point(f - a.step) != "PASS":
            f -= a.step
        f -= a.step
    last_pass, first_fail, fails = f, None, 0
    while f + a.step <= a.max:
        f += a.step
        if point(f) == "PASS":
            last_pass, first_fail, fails = f, None, 0
        else:
            if fails == 0:
                first_fail = f
            fails += 1
            if fails == 2:
                break
    if first_fail is not None and first_fail - last_pass > 1:
        lo, hi = last_pass, first_fail
        while hi - lo > 1:
            mid = (lo + hi) // 2
            if point(float(mid)) == "PASS":
                lo = float(mid)
            else:
                hi = float(mid)
        last_pass, first_fail = lo, hi
    out = {"prefix": a.prefix, "last_pass_mhz": last_pass,
           "first_fail_mhz": first_fail,
           "points": {"{:g}".format(k): v for k, v in sorted(results.items())}}
    with open(os.path.join(HERE, "runs", a.prefix + ".sweep.json"), "w") as fo:
        json.dump(out, fo, indent=1)
    print(json.dumps(out), flush=True)


def cmd_summary(a):
    print("| run | tool | flags | R | MHz | predicted Fmax | verdict | runs | wrong runs | measured MHz | ppm | build s |")
    print("|---|---|---|---|---|---|---|---|---|---|---|---|")
    for d in sorted(filter(os.path.isdir, glob.glob(os.path.join(HERE, "runs", "*"))),
                    key=os.path.getmtime):
        b = load(d, "build.json") or {}
        t = load(d, "test.json") or {}
        pf = b.get("predicted_fmax_mhz", {})
        pf = ", ".join("{}: {}".format(k, v) for k, v in sorted(pf.items()))
        verdict = t.get("verdict", "not tested")
        if b.get("error"):
            verdict = "build error: " + b["error"]
        elif t.get("error"):
            verdict += " (" + t["error"] + ")"
        if "control_ok" in t:
            verdict += ", control " + ("OK" if t["control_ok"] else "NOT OK")
        print("| {} | {} | {} | {} | {} | {} | {} | {} | {} | {} | {} | {} |".format(
            os.path.basename(d), b.get("tool", ""),
            " ".join(x for x in [b.get("nextpnr_flags") or "",
                                 "base " + b["base"] if b.get("base") else "",
                                 "cascade" if b.get("cascade") else "",
                                 "Vivado placement {}/{}, {} unpinned".format(b["vivado_placement"]["pinned"], b["vivado_placement"]["vivado_slice_cells"], b["vivado_placement"].get("unpinned", 0)) if b.get("vivado_placement") else "",
                                 "phase0 x{}".format(b["fasm_phase0_lines"]) if "fasm_phase0_lines" in b else "",
                                 "placement differs" if b.get("same_placement") is False else ""] if x),
            b.get("rounds", ""),
            b.get("actual_mhz", ""), pf, verdict, t.get("runs", ""),
            t.get("fail_runs", ""), t.get("measured_mhz", ""),
            t.get("freq_error_ppm", ""), b.get("build_s", "")))


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    s = p.add_subparsers(dest="cmd", required=True)
    for name in ("build", "run"):
        q = s.add_parser(name)
        add_build_args(q)
        q.add_argument("--seconds", type=float, default=30)
        q.add_argument("--port")
    q = s.add_parser("test")
    q.add_argument("name")
    q.add_argument("--seconds", type=float, default=30)
    q.add_argument("--port")
    q = s.add_parser("campaign")
    q.add_argument("file")
    q.add_argument("--seconds", type=float, default=30)
    q.add_argument("--port")
    q = s.add_parser("sweep")
    q.add_argument("prefix")
    q.add_argument("--tool", choices=["nextpnr", "vivado", "vivado_yosys"], required=True)
    q.add_argument("--cascade", action="store_true")
    q.add_argument("--rounds", type=int, required=True)
    q.add_argument("--start", type=float, required=True)
    q.add_argument("--step", type=float, required=True)
    q.add_argument("--max", type=float, default=500)
    q.add_argument("--nextpnr-flags", default="")
    q.add_argument("--base")
    q.add_argument("--fasm-phase0", action="store_true")
    q.add_argument("--vivado-placement")
    q.add_argument("--seconds", type=float, default=10)
    q.add_argument("--port")
    s.add_parser("summary")
    a = p.parse_args()
    ok = {"build": cmd_build, "test": cmd_test, "run": cmd_run,
          "campaign": cmd_campaign, "sweep": cmd_sweep,
          "summary": cmd_summary}[a.cmd](a)
    sys.exit(0 if ok in (None, True) else 1)


if __name__ == "__main__":
    main()
