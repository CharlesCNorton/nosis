"""Nosis command-line interface.

Entry point for the synthesis pipeline. Accepts one or more SystemVerilog
or Verilog source files and runs the full pipeline:

  1. Parse and elaborate via pyslang
  2. Lower the elaborated AST to the Nosis IR
  3. Optimize (constant folding, dead code elimination)
  4. Map to ECP5 technology (TRELLIS_SLICE LUT4, TRELLIS_FF)
  5. Emit nextpnr-compatible JSON

Flags control optimization (--no-opt), output path (-o), top module
selection (--top), preprocessor defines (-D), include paths (-I),
and verbosity (--verbose, --stats).
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Nosis — correctness-first FPGA synthesis")
    parser.add_argument("--version", action="version", version=f"%(prog)s {_version()}")
    parser.add_argument("input", nargs="+", help="SystemVerilog or Verilog source files")
    parser.add_argument("--top", help="top module name (auto-detected if omitted)")
    parser.add_argument("--target", default="ecp5", help="target FPGA family (default: ecp5)")
    parser.add_argument("-o", "--output", help="output JSON netlist path")
    parser.add_argument("-D", "--define", action="append", default=[], help="preprocessor define (NAME or NAME=VALUE)")
    parser.add_argument("-I", "--include", action="append", default=[], help="include search directory")
    parser.add_argument("--no-opt", action="store_true", help="skip optimization passes")
    parser.add_argument("--dump-ir", action="store_true", help="print the IR after lowering and exit")
    parser.add_argument("--emit-verilog", action="store_true", help="emit Verilog text output for the IR and exit")
    parser.add_argument("--check", action="store_true", help="parse and validate only — do not emit any output")
    parser.add_argument("--stats", action="store_true", help="print synthesis statistics")
    parser.add_argument("--benchmark", action="store_true", help="emit machine-readable JSON with cell counts, timing, and wall-clock time per stage")
    parser.add_argument("--ecppack", help="run ecppack on the output to produce a .bit bitstream file at this path")
    parser.add_argument("--snapshot", help="save an IR snapshot for incremental compilation")
    parser.add_argument("--delta", help="compare against a previous IR snapshot and print delta")
    parser.add_argument("-v", "--verbose", action="store_true", help="verbose output")
    args = parser.parse_args(argv)

    if args.target != "ecp5":
        print(f"error: unsupported target '{args.target}' (only 'ecp5' is supported)", file=sys.stderr)
        return 1

    t0 = time.monotonic()

    # --- Parse ---
    from nosis.frontend import FrontendError, parse_files, lower_to_ir

    defines: dict[str, str] = {}
    for d in args.define:
        if "=" in d:
            k, v = d.split("=", 1)
            defines[k] = v
        else:
            defines[d] = ""

    try:
        result = parse_files(
            args.input,
            top=args.top,
            defines=defines,
            include_dirs=args.include or None,
        )
    except FrontendError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if result.errors:
        for err in result.errors:
            print(f"error: {err}", file=sys.stderr)
        return 1

    t_parse = time.monotonic()
    if args.verbose:
        print(f"parse: {len(result.top_instances)} top instance(s) in {t_parse - t0:.3f}s")

    # --- Lower to IR ---
    design = lower_to_ir(result, top=args.top)
    mod = design.top_module()
    t_lower = time.monotonic()
    if args.verbose:
        print(f"lower: {mod.stats()}")
        print(f"lower: {t_lower - t_parse:.3f}s")

    # --- Optimize ---
    if not args.no_opt:
        from nosis.passes import run_default_passes
        opt_stats = run_default_passes(mod)
        t_opt = time.monotonic()
        if args.verbose:
            print(f"opt: {opt_stats}")
            print(f"opt: {t_opt - t_lower:.3f}s")
    else:
        t_opt = time.monotonic()

    # --- Check mode: validate only, no output ---
    if args.check:
        print(f"check: {mod.name} — {mod.stats()['cells']} cells, {mod.stats()['nets']} nets, {len(mod.ports)} ports")
        return 0

    # --- Dump IR mode ---
    if args.dump_ir:
        print(f"module {mod.name}:")
        print(f"  nets: {mod.stats()['nets']}")
        print(f"  cells: {mod.stats()['cells']}")
        print(f"  ports: {len(mod.ports)}")
        print(f"  port list: {', '.join(sorted(mod.ports))}")
        print(f"  stats: {mod.stats()}")
        print()
        for cell in mod.cells.values():
            ins = ", ".join(f"{k}={v.name}" for k, v in cell.inputs.items())
            outs = ", ".join(f"{k}={v.name}" for k, v in cell.outputs.items())
            params = ", ".join(f"{k}={v}" for k, v in cell.params.items()) if cell.params else ""
            print(f"  {cell.name}: {cell.op.name} ({ins}) -> ({outs}){f' [{params}]' if params else ''}")
        return 0

    # --- Verilog text output ---
    if args.emit_verilog:
        from nosis.ir import emit_verilog
        print(emit_verilog(mod))
        return 0

    # --- Snapshot / Delta ---
    if args.snapshot or args.delta:
        from nosis.incremental import snapshot_module, compute_delta, save_snapshot, load_snapshot
        snap = snapshot_module(mod)
        if args.snapshot:
            save_snapshot(snap, args.snapshot)
            if args.verbose:
                print(f"snapshot: saved to {args.snapshot}")
        if args.delta:
            prev = load_snapshot(args.delta)
            delta = compute_delta(prev, snap)
            for line in delta.summary_lines():
                print(line)

    # --- Dead module elimination ---
    dead = design.eliminate_dead_modules()
    if dead and args.verbose:
        print(f"dead modules removed: {', '.join(dead)}")

    # --- Inference (annotate cells for specialized mapping) ---
    from nosis.bram import infer_brams
    from nosis.dsp import infer_dsps
    from nosis.carry import infer_carry_chains
    from nosis.fsm import extract_fsms, annotate_fsm_cells

    n_bram = infer_brams(mod)
    n_dsp = infer_dsps(mod)
    n_carry = infer_carry_chains(mod)
    fsms = extract_fsms(mod)
    n_fsm = annotate_fsm_cells(mod, fsms)
    t_infer = time.monotonic()
    if args.verbose:
        print(f"infer: bram={n_bram} dsp={n_dsp} carry={n_carry} fsm={n_fsm} ({len(fsms)} FSMs)")
        print(f"infer: {t_infer - t_opt:.3f}s")

    # --- LUT packing (IR level) ---
    if not args.no_opt:
        from nosis.lutpack import pack_luts_ir
        n_packed = pack_luts_ir(mod)
        t_pack = time.monotonic()
        if args.verbose:
            print(f"pack: merged {n_packed} LUT pairs")
            print(f"pack: {t_pack - t_infer:.3f}s")
    else:
        t_pack = time.monotonic()

    # --- Technology map (on a fresh IR copy to avoid DCE-degraded module) ---
    from nosis.techmap import map_to_ecp5
    design_for_map = lower_to_ir(result, top=args.top)
    if not args.no_opt:
        # Apply inference passes to the fresh copy
        mod_map = design_for_map.top_module()
        infer_brams(mod_map)
        infer_dsps(mod_map)
        infer_carry_chains(mod_map)
        if not args.no_opt:
            pack_luts_ir(mod_map)
    netlist = map_to_ecp5(design_for_map)
    t_map = time.monotonic()
    if args.verbose:
        print(f"map: {netlist.stats()}")
        print(f"map: {t_map - t_pack:.3f}s")

    # --- Slice packing (PFUMX + L6MUX21) after tech mapping ---
    if not args.no_opt:
        from nosis.slicepack import pack_slices
        slice_stats = pack_slices(netlist)
        t_slice = time.monotonic()
        if args.verbose:
            print(f"slicepack: {slice_stats}")
            print(f"slicepack: {t_slice - t_map:.3f}s")
    else:
        t_slice = time.monotonic()

    # --- Emit JSON ---
    from nosis.json_backend import emit_json, emit_json_str
    if args.output:
        path = emit_json(netlist, args.output)
        t_emit = time.monotonic()
        if args.verbose:
            print(f"emit: {path} ({t_emit - t_map:.3f}s)")
    else:
        if not args.benchmark:
            print(emit_json_str(netlist))
        t_emit = time.monotonic()

    # --- ecppack integration ---
    if args.ecppack and args.output:
        import subprocess
        json_path = str(Path(args.output).resolve())
        bit_path = str(Path(args.ecppack).resolve())
        try:
            subprocess.run(
                ["ecppack", "--input", json_path, "--bit", bit_path],
                check=True, capture_output=True, text=True,
            )
            if args.verbose:
                print(f"ecppack: {bit_path}")
        except FileNotFoundError:
            print("warning: ecppack not found in PATH", file=sys.stderr)
        except subprocess.CalledProcessError as e:
            print(f"error: ecppack failed: {e.stderr}", file=sys.stderr)
            return 1

    t_total = time.monotonic() - t0

    if args.stats or args.verbose:
        nl_stats = netlist.stats()
        print(f"--- nosis synthesis complete ---")
        print(f"top: {mod.name}")
        print(f"IR cells: {mod.stats()['cells']}, IR nets: {mod.stats()['nets']}")
        print(f"ECP5 cells: {nl_stats['cells']}")
        for cell_type in sorted(k for k in nl_stats if k not in ("cells", "nets", "ports")):
            print(f"  {cell_type}: {nl_stats[cell_type]}")
        print(f"ECP5 nets: {nl_stats['nets']}")
        print(f"ports: {nl_stats['ports']}")
        from nosis.resources import calculate_area, report_utilization
        area = calculate_area(netlist)
        for line in area.summary_lines():
            print(line)
        report = report_utilization(netlist, "25k")
        for line in report.summary_lines():
            print(line)
        from nosis.timing import analyze_timing
        timing = analyze_timing(mod)
        for line in timing.summary_lines():
            print(line)
        print(f"total: {t_total:.3f}s")

    # --- --benchmark mode ---
    if args.benchmark:
        from nosis.resources import calculate_area
        from nosis.timing import analyze_timing
        nl_stats = netlist.stats()
        area = calculate_area(netlist)
        timing = analyze_timing(mod)
        bench = {
            "total_s": round(t_total, 4),
            "cells": nl_stats.get("cells", 0),
            "luts": nl_stats.get("TRELLIS_SLICE", 0),
            "ffs": nl_stats.get("TRELLIS_FF", 0),
            "slices": area.slices_total,
            "max_freq_mhz": round(timing.max_frequency_mhz, 1),
            "critical_path_ns": round(timing.max_delay_ns, 3),
        }
        print(json.dumps(bench, indent=2))

    return 0


def _version() -> str:
    from nosis import __version__
    return __version__


if __name__ == "__main__":
    raise SystemExit(main())
