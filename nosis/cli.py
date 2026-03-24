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
    parser.add_argument("--dump-ir", action="store_true", help="print the IR after lowering and exit (no tech mapping or JSON output)")
    parser.add_argument("--check", action="store_true", help="parse and validate only — do not emit any output")
    parser.add_argument("--stats", action="store_true", help="print synthesis statistics")
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

    # --- Technology map ---
    from nosis.techmap import map_to_ecp5
    netlist = map_to_ecp5(design)
    t_map = time.monotonic()
    if args.verbose:
        print(f"map: {netlist.stats()}")
        print(f"map: {t_map - t_opt:.3f}s")

    # --- Emit JSON ---
    from nosis.json_backend import emit_json, emit_json_str
    if args.output:
        path = emit_json(netlist, args.output)
        t_emit = time.monotonic()
        if args.verbose:
            print(f"emit: {path} ({t_emit - t_map:.3f}s)")
    else:
        print(emit_json_str(netlist))
        t_emit = time.monotonic()

    t_total = t_emit - t0

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
        print(f"total: {t_total:.3f}s")

    return 0


def _version() -> str:
    from nosis import __version__
    return __version__


if __name__ == "__main__":
    raise SystemExit(main())
