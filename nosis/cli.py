"""Nosis command-line interface."""

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
