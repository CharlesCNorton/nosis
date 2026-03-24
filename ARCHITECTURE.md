# Nosis Architecture

## Overview

Nosis is a pure-Python FPGA synthesis tool. It reads SystemVerilog and Verilog, converts behavioral hardware descriptions into a technology-independent intermediate representation, optimizes and analyzes the IR, maps it onto ECP5 FPGA primitives, and emits a JSON netlist consumable by nextpnr-ecp5 for place and route.

The pipeline is strictly sequential: each stage consumes the output of the previous stage and produces input for the next. There are no feedback loops between stages. This makes the pipeline easy to test, debug, and reason about — any stage can be run in isolation with a known input.

## Data Flow

```
Source files (.sv, .v)
       │
       ▼
   ┌────────┐
   │ pyslang │  External: IEEE 1800-2017 parse + elaborate + type-check
   └────┬───┘
        │  Elaborated AST (pyslang objects)
        ▼
   ┌──────────┐
   │ frontend │  nosis/frontend.py — walk AST, emit IR cells and nets
   └────┬─────┘
        │  Design { Module { cells: dict[str, Cell], nets: dict[str, Net] } }
        ▼
   ┌────────┐
   │ passes │  nosis/passes.py — constant fold, identity simplify, CSE, DCE
   └────┬───┘
        │  Same Design, fewer cells/nets
        ▼
   ┌──────────────┐
   │ fsm/bram/dsp │  nosis/fsm.py, bram.py, dsp.py, carry.py — annotate cells
   │    /carry    │  (no structural changes, only params added)
   └──────┬───────┘
          │  Same Design, cells have inference annotations
          ▼
   ┌─────────┐
   │ lutpack │  nosis/lutpack.py — merge cascaded 2-input ops
   └────┬────┘
        │  Same Design, fewer cells
        ▼
   ┌─────────┐
   │ techmap │  nosis/techmap.py — IR cells → ECP5Cell instances
   └────┬────┘
        │  ECP5Netlist { cells: dict[str, ECP5Cell], nets: dict[str, ECP5Net] }
        ▼
   ┌──────────────┐
   │ json_backend │  nosis/json_backend.py — serialize to nextpnr JSON
   └──────┬───────┘
          │  JSON string or file
          ▼
     nextpnr-ecp5
```

## Key Design Decisions

### Why pyslang instead of a custom parser

SystemVerilog is a 1,300-page IEEE specification. Writing a correct parser and elaborator is a multi-year effort. Slang is the most complete open-source implementation, handles the full IEEE 1800-2017 standard, and is actively maintained. By using slang as the frontend, nosis gets correct parsing, elaboration, parameter resolution, type checking, and constant evaluation without reimplementing any of it.

The tradeoff: pyslang is a C++ library with Python bindings that must be built from source. This adds a build dependency. The alternative — accepting incorrect or incomplete parsing — is worse.

### Why a flat IR instead of hierarchical

The IR represents one module as a flat graph of cells and nets. Module instantiation is resolved during lowering — if module A instantiates module B, B's logic is inlined into A's IR. This simplifies every downstream pass: optimization, inference, mapping, and equivalence checking all operate on a single flat graph with no hierarchy to traverse.

The cost is that very large designs (hundreds of thousands of cells) will have large flat modules. This is acceptable for ECP5 designs (up to ~85K LUTs in the largest variant).

### Why annotation-only inference

The BRAM, DSP, carry chain, and FSM passes do not restructure the IR. They add parameters to existing cells (`bram_config`, `dsp_config`, `carry_config`, `fsm_state`). The technology mapper reads these annotations to decide which ECP5 primitives to emit.

This separation means:
- The inference passes cannot introduce bugs — they only add metadata.
- The technology mapper has all the information it needs in the cell params.
- The order of inference passes does not matter.
- Debugging is simple: print the cell params after inference to see what was detected.

### Why LUT truth tables are computed from PrimOp

Each IR operation (AND, OR, XOR, MUX, etc.) has a known Boolean function. The technology mapper computes the LUT4 INIT value directly from the PrimOp, not from a generic Boolean optimization step. This means:
- The mapping is deterministic — the same IR operation always produces the same LUT4 INIT.
- There is no intermediate Boolean representation that could be mis-optimized.
- The truth table is trivially verifiable: for each of the 16 input combinations, the LUT output matches the PrimOp semantics.

The cost is that the LUT packing is not optimal — a chain of two 2-input operations that could fit in one LUT4 will instead use two. This is a future optimization target.

### Why exhaustive + random equivalence checking instead of SAT

SAT-based equivalence checking (via CNF formulation and a solver like CaDiCaL) is the standard approach for large designs. Nosis currently uses:
- Exhaustive simulation for designs with ≤ 16 input bits (provably complete).
- Random simulation with 10,000 vectors for larger designs (probabilistic).

The exhaustive path is correct by construction. The random path has a nonzero probability of missing a mismatch on a specific input combination. SAT-based checking is planned as a third method that provides completeness for arbitrary-size designs.

The reason for starting with simulation: it reuses the same `_eval_cell` function used by constant folding, providing a single source of truth for cell semantics. A simulation-based equivalence checker that uses the same evaluation functions as the optimizer cannot disagree with it about what a cell does — any inconsistency between optimization and checking would require a bug in `_eval_cell` itself, which is independently testable.

## Module Contracts

### `nosis/ir.py`

- `Net.driver` is either `None` (undriven) or a single `Cell`. A net never has two drivers.
- `Cell.inputs` and `Cell.outputs` are dicts from port name to `Net`. The same net may appear as input to multiple cells but as output of at most one cell.
- `Module.add_net` and `Module.add_cell` reject duplicate names.
- `Module.connect(cell, port, net, direction="output")` sets `net.driver = cell`.
- `Design.top_module()` returns the designated top or the only module if there is exactly one. Raises `ValueError` if ambiguous.

### `nosis/frontend.py`

- `parse_files` raises `FrontendError` if pyslang reports any error-severity diagnostic (excluding suppressed codes like `MissingTimeScale`).
- `lower_to_ir` produces a `Design` where every `Cell` in every `Module` has correct `inputs`/`outputs` connectivity. No dangling ports.
- The lowering never creates a net with width 0.
- Constants from pyslang's `SVInt` type are converted to Python `int` through `_svint_to_int`, which handles decimal, binary (`1'b1`), hex (`32'hFF`), and octal formats.

### `nosis/passes.py`

- `constant_fold` only replaces cells whose inputs are **all** driven by `CONST` cells. It never touches `FF`, `INPUT`, or `OUTPUT` cells.
- `dead_code_eliminate` never removes `INPUT`, `OUTPUT`, or cells reachable from output ports.
- `run_default_passes` returns a dict with counts for each sub-pass. A count of 0 means no changes were made.

### `nosis/techmap.py`

- `map_to_ecp5` produces an `ECP5Netlist` where every cell is either `TRELLIS_SLICE` or `TRELLIS_FF`.
- Every `ECP5Cell` has a `type`, `parameters`, `ports` (with bit-level connections), and `port_directions`.
- Bit index 0 is constant 0, bit index 1 is constant 1, indices ≥ 2 are signal bits.
- `IR INPUT/OUTPUT` cells become port declarations, not physical cells.

### `nosis/json_backend.py`

- `emit_json_str` produces valid JSON parseable by `json.loads`.
- The output conforms to the nextpnr JSON schema: top-level `creator` and `modules` keys, each module has `attributes`, `ports`, `cells`, `netnames`.
- The `top` attribute is set to `"00000000000000000000000000000001"` (32-bit binary 1).
- All bit references in connections are integers ≥ 0.

### `nosis/equiv.py`

- `check_equivalence_exhaustive` is provably complete for designs within the input bit limit.
- `_simulate_combinational` evaluates cells in topological order. INPUT and CONST cells are evaluated before all others.
- A cell's `_eval_cell` function uses the same arithmetic as `constant_fold` in `passes.py`. They share semantics by implementation — any change to one must be reflected in the other.

## Test Organization

Tests are split by scope:

- **Unit tests** (`test_ir.py`, `test_eval.py`, `test_passes.py`, `test_techmap.py`, `test_json_backend.py`, `test_fsm.py`, `test_bram.py`, `test_dsp.py`, `test_carry.py`, `test_equiv.py`, `test_cse.py`, `test_lutpack.py`, `test_clocks.py`, `test_blackbox.py`, `test_resources.py`, `test_timing.py`, `test_diff.py`, `test_readmem.py`): test individual modules in isolation using hand-constructed IR. No external dependencies beyond Python. Run in CI.

- **Frontend tests** (`test_frontend.py`): test pyslang parsing and IR lowering against real SystemVerilog files from the RIME repository. Require pyslang and RIME source.

- **Regression tests** (`test_regression.py`): end-to-end synthesis of real designs with structural assertions (port counts, cell counts, IR statistics, JSON validity, FSM detection, CCU2C emission, SSHR/REPEAT handling, enum lowering, hierarchy prefixing, MEMORY dimensions, LUT packing, clock domains, distributed RAM). Each test class covers one design and makes increasingly specific claims about the synthesis output.

- **Connectivity tests** (`test_connectivity.py`): 12 structural invariants verified on every design — net widths, driver counts, FF completeness, port presence. Plus JSON netlist invariant verification.

- **Property-based tests** (`test_property.py`): Hypothesis-driven random testing of evaluation determinism, width masking, algebraic identities (De Morgan, add/sub inverse, double NOT), comparison complementarity, MUX selection, shift inverse, div/mod reconstruction, optimization correctness.

- **Mutation tests** (`test_mutation.py`): verifies that every distinct pair of operations is distinguishable by the equivalence checker. Counterexample extraction validated. No false positives.

- **Torture tests** (`test_torture.py`): adversarial inputs across every pipeline stage — degenerate IR structures, empty modules, 100-gate chains, wide fanout, deep MUX trees, optimization stress, equivalence checker edge cases, JSON edge cases, resource reporting boundaries.

- **Validation tests** (`test_validate.py`): iverilog simulation harness infrastructure.

All test paths are configurable via `NOSIS_RIME_ROOT` and `NOSIS_PYSLANG_PATH` environment variables (see `tests/conftest.py`).

## Module Inventory

| Module | Lines | Purpose |
|--------|-------|---------|
| `ir.py` | 160 | IR: Design, Module, Cell, Net, 32 PrimOps, attributes |
| `eval.py` | 170 | Shared cell evaluation — single source of truth for all PrimOp semantics |
| `frontend.py` | 1066 | pyslang IEEE 1800-2017 parse, elaborate, RTL-to-IR lowering, hierarchy |
| `passes.py` | 300 | Constant fold, identity simplify, dead code elimination |
| `cse.py` | 90 | Common subexpression elimination |
| `fsm.py` | 280 | FSM extraction, encoding classification, cell annotation |
| `bram.py` | 100 | DP16KD and DPR16X4 inference |
| `dsp.py` | 65 | MULT18X18D inference |
| `carry.py` | 65 | CCU2C carry chain inference |
| `lutpack.py` | 150 | IR-level LUT packing — merge cascaded 2-input operations |
| `clocks.py` | 110 | Clock domain analysis, CDC detection |
| `techmap.py` | 600 | ECP5 mapping: TRELLIS_SLICE, TRELLIS_FF, CCU2C, MULT18X18D, DP16KD, PMUX |
| `json_backend.py` | 120 | nextpnr-compatible JSON serialization |
| `equiv.py` | 330 | Equivalence checking: exhaustive, SAT (PySAT Glucose3), random simulation |
| `validate.py` | 250 | iverilog RTL simulation harness, testbench generation |
| `resources.py` | 230 | Exact area calculation, slice packing, device utilization reporting |
| `timing.py` | 200 | Static timing analysis, critical path identification |
| `diff.py` | 90 | Netlist comparison between synthesis runs |
| `readmem.py` | 55 | $readmemh / $readmemb parser for BRAM initialization |
| `blackbox.py` | 350 | Black box registry, 48 ECP5 vendor primitives, user-defined black boxes |
| `cli.py` | 200 | Command-line interface with 10 pipeline stages |
| `ecp5_prims.sv` | 140 | Vendor primitive stubs for slang elaboration |
