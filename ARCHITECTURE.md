# Nosis Architecture

## Overview

Nosis is a pure-Python FPGA synthesis tool. It reads SystemVerilog and Verilog, converts behavioral hardware descriptions into a technology-independent intermediate representation, optimizes the IR through 16 passes (including HoTT-inspired equivalence merging, SAT-based constant proof, and cut-based LUT remapping), maps onto ECP5 FPGA primitives, and emits a JSON netlist consumable by nextpnr-ecp5 for place and route.

The pipeline is strictly sequential: each stage consumes the output of the previous stage and produces input for the next. There are no feedback loops between stages.

## Data Flow

```
Source files (.sv, .v)
       |
       v
   [pyslang]       External: IEEE 1800-2017 parse + elaborate + type-check
       |
       v
   [frontend]      nosis/frontend.py -- walk AST, emit IR cells and nets
       |            Handles hierarchy flattening, FF Q-redirect, concat LHS
       v
   [optimization]  nosis/passes.py -- 12 passes x 6 rounds + 4 post-passes
       |            constant fold, identity, boolean, CSE, HIT merge,
       |            don't-care, MUX merge, reqmerge, SAT const, cut-map
       v
   [inference]     nosis/fsm.py, bram.py, dsp.py, carry.py -- annotate cells
       |
       v
   [tech mapping]  nosis/techmap.py -- IR primitives to LUT4, TRELLIS_FF,
       |            CCU2C, MULT18X18D, DP16KD, ALU54B
       v
   [slice packing] nosis/slicepack.py -- chain merge, dedup, const simplify,
       |            buffer absorb, dead LUT eliminate
       v
   [JSON backend]  nosis/json_backend.py -- nextpnr-compatible JSON
       |
       v
   nextpnr-ecp5 -> ecppack -> bitstream
```

## Key Design Decisions

### Why pyslang instead of a custom parser

SystemVerilog is a 1,300-page IEEE specification. Slang is the most complete open-source implementation. By using slang as the frontend, nosis gets correct parsing, elaboration, parameter resolution, type checking, and constant evaluation without reimplementing any of it.

### Why a flat IR instead of hierarchical

Module instantiation is resolved during lowering -- if module A instantiates module B, B's logic is inlined into A's IR with prefixed net/cell names. This simplifies every downstream pass.

### Why LUT4 cells instead of TRELLIS_SLICE

nextpnr-ecp5 expects `LUT4` cells with ports `A`, `B`, `C`, `D`, `Z` and a 16-bit binary `INIT` parameter. The `TRELLIS_SLICE` name is an internal nextpnr abstraction that the JSON input format does not use. yosys emits `LUT4` cells; nosis does the same.

### Why annotation-only inference

The BRAM, DSP, carry chain, and FSM passes do not restructure the IR. They add parameters to existing cells. The technology mapper reads these annotations to decide which ECP5 primitives to emit. This means inference passes cannot introduce bugs.

### Why single evaluation semantics

Every PrimOp has exactly one evaluation function in `eval.py`. Constant folding, equivalence checking, simulation, truth table computation, and cut-based remapping all use the same code path. Any inconsistency between optimization and verification would require a bug in `eval.py` itself.

### Why HoTT-inspired optimization

Five optimization techniques are derived from Homotopy Type Theory concepts:
- Quotient types for reachable-state equivalence merging
- Higher Inductive Types for truth table equivalence
- Encode-decode method for don't-care input elimination
- Cofiber/zero object for dead LUT bit elimination
- Duality principle for backward don't-care propagation

These are not cosmetic labels. Each technique directly maps a mathematical construction to a specific netlist transformation with a provable correctness argument.

## Module Contracts

### `nosis/ir.py`

- `Module` contains `cells: dict[str, Cell]`, `nets: dict[str, Net]`, `ports: dict[str, Net]`
- Every `Net` has at most one driver (`net.driver`)
- Every `Cell` has `op: PrimOp`, `inputs: dict[str, Net]`, `outputs: dict[str, Net]`, `params: dict`
- 30 PrimOp variants covering all synthesizable operations

### `nosis/frontend.py`

- Input: pyslang elaborated AST
- Output: `Design` with flattened `Module` instances
- Contract: every FF has CLK and D inputs; every net has width > 0; every port has a corresponding INPUT or OUTPUT cell

### `nosis/passes.py`

- Input/output: `Module` mutated in place
- Contract: cell count is monotonically non-increasing per pass; functional equivalence is preserved
- 570 tests verify this across all RIME designs

### `nosis/techmap.py`

- Input: `Design` with technology-independent IR
- Output: `ECP5Netlist` with `LUT4`, `TRELLIS_FF`, `CCU2C`, `MULT18X18D`, `DP16KD` cells
- Contract: every IR cell produces at least one ECP5 cell (or is pure wiring)

### `nosis/json_backend.py`

- Input: `ECP5Netlist`
- Output: JSON string consumable by `nextpnr-ecp5 --json`
- Contract: valid JSON; all parameters are strings; connection bits are integers (signals) or strings ("0"/"1"/"x" constants); INIT values are 16-char binary strings

## Test Architecture

570 tests organized by module:
- Unit tests: IR construction, evaluation, individual pass behavior
- Integration tests: full pipeline on real RIME hardware designs
- Property tests: Hypothesis-generated random inputs verify invariants
- Regression tests: locked cell counts prevent undetected changes
- Structural tests: connectivity, monotonicity, port survival

CI runs lint + unit tests across Python 3.11-3.13 with pyslang installed.
