# Nosis

Correctness-first open-source FPGA synthesis targeting Lattice ECP5.

Nosis synthesizes SystemVerilog and Verilog into technology-mapped netlists for the ECP5 FPGA family. It prioritizes synthesis correctness over optimization — every output is verifiable against the input RTL through built-in equivalence checking.

## Architecture

```
  SystemVerilog / Verilog source
           |
     [1. Frontend]         — pyslang IEEE 1800-2017 parse, elaborate, type-check
           |
     [2. IR Lowering]      — behavioral HDL to technology-independent netlist
           |
     [3. Optimization]     — constant folding, dead code elimination
           |
     [4. Inference]        — FSM, BRAM, DSP, carry chain recognition
           |
     [5. Tech Mapping]     — IR primitives to ECP5 cells (LUT4, FF, DP16KD, MULT18X18D, CCU2C)
           |
     [6. JSON Backend]     — nextpnr-compatible netlist
           |
     [7. Verification]     — equivalence checking, RTL simulation comparison
           |
     nextpnr-ecp5 → ecppack → bitstream
```

Nosis covers the synthesis step. Place-and-route (nextpnr) and bitstream packing (ecppack) handle the rest.

## Synthesis Pipeline

### Stage 1: Frontend (`nosis/frontend.py`)

Parses SystemVerilog and Verilog source files through [slang](https://github.com/MikePopoloski/slang), a complete IEEE 1800-2017 compiler. Slang handles the full language specification: preprocessing, parsing, elaboration, parameter resolution, type checking, and constant evaluation.

The frontend:

- Accepts one or more `.sv` or `.v` source files
- Resolves parameters and generate blocks at elaboration time
- Type-checks all expressions with full width inference
- Reports errors with exact source locations via slang diagnostics
- Rejects designs with undeclared identifiers, type mismatches, or structural errors that other tools silently accept
- Provides ECP5 vendor primitive stubs (`ecp5_prims.sv`) so designs that instantiate USRMCLK, EHXPLLL, OSCG, DTR, SEDGA, and other Lattice-specific cells can elaborate without external libraries
- Suppresses `MissingTimeScale` diagnostics since timescale is not relevant to synthesis

**Input:** SystemVerilog/Verilog source files.
**Output:** Elaborated AST with resolved types, widths, and constants.

### Stage 2: IR Lowering (`nosis/frontend.py`, `nosis/ir.py`)

Walks the elaborated AST and converts it into the Nosis intermediate representation: a flat, technology-independent netlist of primitive cells connected by typed nets.

The IR has 30 primitive operations organized into categories:

| Category | Operations |
|----------|------------|
| Combinational logic | AND, OR, XOR, NOT, MUX, PMUX, REDUCE_AND, REDUCE_OR, REDUCE_XOR |
| Arithmetic | ADD, SUB, MUL, SHL, SHR, SSHR |
| Comparison | EQ, NE, LT, LE, GT, GE |
| Bit manipulation | CONCAT, SLICE, REPEAT, ZEXT, SEXT |
| Sequential | FF, LATCH |
| Memory | MEMORY |
| Constants | CONST |
| Ports | INPUT, OUTPUT |

Each cell has typed input and output ports. Each net has a name and a bit width. Every net has at most one driver (the cell whose output port connects to it).

The lowering handles:

- **`always_ff` blocks:** Extracts the clock edge from `TimingControlKind.SignalEvent`. Collects all non-blocking assignments within the block. For each assigned target, creates an FF cell with the clock, the combinational D input (derived from the RHS expression tree), and optionally a synchronous reset (inferred from `if (rst)` patterns in the true branch).
- **`always_comb` blocks:** Lowers as pure combinational wiring. Assignments become direct net connections.
- **`if/else` statements:** Converts to MUX trees. The condition becomes the selector; the true and false branches become the MUX inputs. Nested if/else chains produce cascaded MUX cells.
- **`case` statements:** Converts to parallel MUX chains. Each case label produces an EQ comparison against the selector, and the case body's assignments are multiplexed with the default.
- **Non-blocking assignments (`<=`):** Create FF cells. The LHS is the Q output; the RHS expression tree drives the D input.
- **Blocking assignments (`=`):** Create direct combinational connections.
- **Binary operators:** Map to the corresponding IR primitive (ADD, SUB, MUL, AND, OR, XOR, EQ, NE, LT, LE, GT, GE, SHL, SHR, SSHR).
- **Unary operators:** NOT, REDUCE_AND, REDUCE_OR, REDUCE_XOR, unary minus (SUB from zero).
- **Ternary operator (`? :`):** MUX cell.
- **Concatenation (`{a, b, c}`):** CONCAT cell with indexed inputs.
- **Bit/range selection (`a[7:0]`, `a[i]`):** SLICE cell with offset and width.
- **Type conversions:** ZEXT or SLICE depending on whether the target is wider or narrower.
- **Parameters and constants:** CONST cells with evaluated integer values. Handles Verilog literal formats (`1'b1`, `32'hDEADBEEF`, `8'd255`) through a dedicated SVInt-to-Python-int converter.
- **Module ports:** INPUT cells for inputs, OUTPUT cells for outputs. Port direction, width, and name are preserved.
- **Continuous assignments (`assign`):** Lowered as combinational expressions.
- **Reset inference:** When an `always_ff` block contains `if (rst) begin ... end else begin ... end`, the blocking assignments in the true branch are extracted as reset values for the non-blocking assignments in the false branch. The resulting FF cells have RST and RST_VAL inputs.
- **Multiple assignments to the same target:** Deduplicated by target name within each procedural block. The last assignment wins, matching Verilog semantics.

**Input:** Elaborated pyslang AST.
**Output:** `Design` containing one or more `Module` instances, each a flat netlist of cells and nets.

### Stage 3: Optimization (`nosis/passes.py`)

Two passes that reduce the netlist while preserving functional equivalence.

**Constant folding:** Iterates over all cells in the module. For each non-sequential cell whose inputs are all driven by CONST cells, evaluates the operation and replaces the cell with a CONST. Runs to fixed point — folding one cell may expose new constant inputs to downstream cells. Handles all combinational operations including MUX with constant selector (selects the appropriate branch statically).

Supported operations for constant evaluation: AND, OR, XOR, NOT, ADD, SUB, MUL, SHL, SHR, EQ, NE, LT, LE, GT, GE, REDUCE_AND, REDUCE_OR, REDUCE_XOR, ZEXT, SEXT, MUX.

**Dead code elimination:** Computes backward reachability from all output ports and FF data inputs. Any cell whose outputs are entirely unreachable from these sinks is removed. Nets with no remaining consumers are also removed.

The default pipeline runs: constant fold → DCE → constant fold → DCE (second pass catches constants exposed by the first DCE).

**Invariant:** The optimization passes never change the functional behavior of the module. Every removed cell was provably dead. Every folded cell is replaced with its exact evaluated result.

**Input/Output:** `Module` (mutated in place). Returns pass statistics.

### Stage 4: Inference (`nosis/fsm.py`, `nosis/bram.py`, `nosis/dsp.py`, `nosis/carry.py`)

Four analysis passes that tag cells for specialized technology mapping.

**FSM extraction (`fsm.py`):** Identifies flip-flops involved in state machine feedback loops. Walks backward from each FF's D input through the combinational logic cone, looking for the FF's own Q output. When a feedback loop through MUX/EQ cells is found, the FF is classified as a state register. The MUX tree is traversed to collect known state values from EQ comparisons against constants.

State encoding is classified as:
- **sequential:** values are 0, 1, 2, 3, ... in order
- **one-hot:** each value has exactly one bit set
- **Gray:** consecutive values differ by exactly one bit
- **binary:** values fit in minimum bits, no specific pattern

The pass annotates cells with `fsm_state`, `fsm_encoding`, and `fsm_transition` parameters. It does not re-encode, reorder, or restructure the state machine. The designer's encoding survives synthesis unchanged.

**Invariant:** FSM extraction never adds, removes, or modifies any cell or net. It is a pure annotation pass.

**BRAM inference (`bram.py`):** Scans for MEMORY cells and determines whether they fit ECP5 DP16KD block RAM configurations. Supported configurations:

| Configuration | Address bits | Data width | Max depth |
|---------------|-------------|------------|-----------|
| 16Kx1 | 14 | 1 | 16,384 |
| 8Kx2 | 13 | 2 | 8,192 |
| 4Kx4 | 12 | 4 | 4,096 |
| 2Kx9 | 11 | 9 (8+1 parity) | 2,048 |
| 1Kx18 | 10 | 18 (16+2 parity) | 1,024 |
| 512x36 | 9 | 36 (32+4 parity) | 512 |

Arrays smaller than 256 bits total are left as distributed RAM (LUT-based). Arrays too large for a single DP16KD are tiled across multiple instances.

**DSP inference (`dsp.py`):** Tags MUL cells for MULT18X18D hard multiplier mapping. Multiplies up to 18x18 bits map to a single MULT18X18D. Multiplies up to 36x36 bits are decomposed into four MULT18X18D instances with addition. The ECP5-25F has 28 available multipliers.

**Carry chain inference (`carry.py`):** Tags ADD and SUB cells for CCU2C carry chain mapping. Each CCU2C handles 2 bits of addition with carry propagation. An N-bit adder uses `ceil(N/2)` CCU2C cells. Single-bit additions are left as LUTs.

**Input/Output:** `Module` (cells annotated in place). Returns count of tagged cells.

### Stage 5: Technology Mapping (`nosis/techmap.py`)

Converts the technology-independent IR into ECP5-specific cells.

**LUT mapping:** Each combinational IR cell (AND, OR, XOR, NOT, MUX, EQ, NE, comparisons, shifts) is mapped to TRELLIS_SLICE cells containing LUT4 instances. The LUT4 truth table (16-bit INIT value) is computed from the IR operation. For multi-bit operations, one TRELLIS_SLICE is emitted per output bit.

LUT4 truth table computation maps IR operations to 4-input Boolean functions:
- AND: `INIT = 0x8888` (A & B, C/D don't-care)
- OR: `INIT = 0xEEEE`
- XOR: `INIT = 0x6666`
- NOT: `INIT = 0x5555` (invert A)
- MUX: `INIT = 0xCACA` (A=sel, B=false, C=true)

**FF mapping:** Each FF cell in the IR is mapped to TRELLIS_FF cells, one per bit. Clock, data, reset, and enable connections are wired. Parameters include GSR mode, clock mux, reset mux, and set/reset mode.

**Constant mapping:** CONST cells become tied bit values (literal `"0"` or `"1"` in the netlist). No physical cells are needed.

**Wiring operations:** CONCAT, SLICE, ZEXT, SEXT, and REPEAT are pure wiring — they reassign bit indices without creating physical cells.

**Bit numbering:** Bit index 0 is constant `0`, bit index 1 is constant `1`, indices ≥ 2 are signal bits. This matches the nextpnr JSON convention.

**Input:** `Design` with technology-independent IR.
**Output:** `ECP5Netlist` containing `ECP5Cell` instances (TRELLIS_SLICE, TRELLIS_FF) and `ECP5Net` instances with bit-level connectivity.

### Stage 6: JSON Backend (`nosis/json_backend.py`)

Serializes the ECP5 netlist to the nextpnr JSON format.

The output format:

```json
{
  "creator": "nosis 0.0.1",
  "modules": {
    "<top_module>": {
      "attributes": {"top": "00000000000000000000000000000001"},
      "ports": {
        "<port_name>": {"direction": "input|output", "bits": [2, 3, 4, ...]}
      },
      "cells": {
        "<cell_name>": {
          "type": "TRELLIS_SLICE|TRELLIS_FF",
          "parameters": {"LUT0_INITVAL": "0x8888", ...},
          "port_directions": {"A0": "input", "F0": "output", ...},
          "connections": {"A0": [5], "F0": [12], ...}
        }
      },
      "netnames": {
        "<net_name>": {"bits": [2, 3, 4, ...], "hide_name": 0}
      }
    }
  }
}
```

Port directions for ECP5 cells are determined by name convention: `Q`, `F0`, `F1`, `OFX0`, `OFX1`, `FCO`, `CO` are outputs; all others are inputs.

**Input:** `ECP5Netlist`.
**Output:** JSON string or file, directly consumable by `nextpnr-ecp5 --json`.

### Stage 7: Verification (`nosis/equiv.py`, `nosis/validate.py`)

Two verification mechanisms.

**Equivalence checking (`equiv.py`):** Compares two IR modules (typically pre- and post-optimization, or original vs. synthesized) by simulating both with identical inputs and comparing outputs.

For designs with ≤ 16 input bits: exhaustive simulation of all `2^N` input combinations. Provably complete — any functional difference is found.

For larger designs: random simulation with 10,000 test vectors from a deterministic seed. Not exhaustive, but catches structural mismatches with high probability.

The simulator evaluates cells in topological order. Constants and INPUT cells are initialized first, then combinational cells propagate values forward. Each cell's evaluation function matches the IR operation semantics exactly.

When a mismatch is detected, the checker returns a counterexample: the specific input assignment that produces different outputs from the two modules.

**RTL simulation harness (`validate.py`):** Generates deterministic Verilog testbenches, compiles and runs them through iverilog, and compares cycle-by-cycle outputs.

The testbench generator:
- Accepts a module name and port list with widths and directions
- Detects clock ports (`clk`, `clock`) and generates oscillating clock
- Detects reset ports (`rst`, `reset`, `rstn`) and generates an initial reset pulse (active-high or active-low inferred from name)
- Applies random input vectors from a seeded PRNG (deterministic across runs)
- Captures all output values per cycle to a file
- Compiles with `iverilog -g2012` for SystemVerilog support

**Input:** Source files and/or IR modules.
**Output:** `EquivalenceResult` (equivalent/not, method, counterexample) or `ValidationResult` (passed/failed, cycle count, mismatches).

## ECP5 Primitive Coverage

Initial target is Lattice ECP5. Full primitive coverage:

- **Logic:** TRELLIS_SLICE (2x LUT4 + 2x FF + carry), CCU2C (carry chain unit), PFUMX (LUT5 passthrough mux), L6MUX21 (LUT6 mux), TRELLIS_COMB, TRELLIS_FF, TRELLIS_DPR16X4 (distributed RAM)
- **Block RAM:** DP16KD (true dual-port 16Kbit), PDPW16KD (pseudo dual-port wide), SP16KD (single-port), FIFO16KD (asynchronous FIFO)
- **DSP:** MULT18X18D (18x18 signed/unsigned multiply), ALU54B (54-bit ALU with accumulator)
- **PLL/Clock:** EHXPLLL (primary PLL), EHXPLLJ (JTAG-configurable PLL), CLKDIVF (clock divider), DCSC (dynamic clock stop), DQSCE (DQS clock enable), ECLKSYNCB (edge clock sync), ECLKBRIDGECS (edge clock bridge), OSCG (internal oscillator, 2.4-133 MHz), DCCA/DCC (dedicated clock buffers), PCSCLKDIV (PCS clock divider)
- **I/O buffers:** TRELLIS_IO, BB/IB/OB/OBZ (bidirectional, input, output, tristate), BBPU/BBPD/IBPU/IBPD (with pull-up/pull-down), LVDS pairs
- **I/O registers:** IFS1P3BX/IFS1P3DX (input FF), OFS1P3BX/OFS1P3DX (output FF)
- **DDR I/O:** IDDRX1F/IDDRX2F (input DDR 1:1, 1:2), IDDR71B (input DDR 1:7), ODDRX1F/ODDRX2F (output DDR 1:1, 1:2), ODDR71B (output DDR 1:7), OSHX2A (output serializer), ISHX2A (input deserializer), TSHX2DQA/TSHX2DQSA (tristate DDR)
- **I/O delay:** DELAYF (input programmable delay), DELAYG (output programmable delay), DQSBUFM (DQS buffer manager for DDR memory)
- **System:** USRMCLK (user SPI flash clock), JTAGG (JTAG interface), GSR (global set/reset), SGSR (slice global set/reset), PUR (power-up reset), DTR (die temperature readout), SEDGA (soft error detection), START (startup sequence control), TSALL (tristate all outputs), EXTREFB (external reference clock buffer)
- **SerDes:** DCUA (dual-channel universal transceiver, 5G variants)
- **Configuration:** BCINRD, sysCONFIG interface primitives

Additional FPGA families are planned.

## Design Principles

1. **Correctness over optimization.** A correct but larger netlist beats an optimized but broken one. Every optimization pass preserves functional equivalence.

2. **Respect the RTL.** If the designer wrote a one-hot FSM, synthesize a one-hot FSM. Designer intent is preserved through synthesis — state encodings, explicit structure, and named signals survive into the netlist.

3. **Verify the output.** Built-in equivalence checking compares the output netlist against the input RTL. If the check fails, synthesis fails.

4. **Pure Python first pass.** Correctness is easier to verify in Python than C++. Performance optimization comes after the pipeline is proven correct.

## Validated Designs

Nosis has been tested against real hardware designs from the [RIME](https://github.com/CharlesCNorton/rime) project (Resident IcePi Management Environment for Lattice ECP5).

| Design | Source | IR cells | ECP5 LUTs | ECP5 FFs | Time |
|--------|--------|----------|-----------|----------|------|
| uart_tx | 1 file, 76 lines | 82 | 475 | 46 | 56 ms |
| uart_rx | 1 file, 79 lines | 87 | 539 | 47 | 61 ms |
| sdram_bridge | 1 file, 130 lines | 106 | 504 | 348 | 72 ms |
| rime_pcpi_crc32 | 1 file, 50 lines | 19 | 1 | 34 | 52 ms |
| rime_v (RV32IMC CPU) | 1 file, 870 lines | 693 | 5,246 | 1,727 | 140 ms |
| Thaw (flash service) | 7 files | 2,424 | 9,111 | 3,328 | 244 ms |
| PicoRV32 SoC | 13 files, full board image | 5,647 | 19,677 | 5,513 | 594 ms |

## Analysis Passes

Beyond synthesis, nosis provides analysis passes that run on the IR or mapped netlist:

### Timing (`nosis/timing.py`)

Static timing analysis using ECP5 cell-level delay models (-6 speed grade). Forward-propagation computes arrival times at every net. The critical path is traced from the highest-delay FF input or output port back through combinational logic. Reports max delay (ns), max frequency (MHz, logic-only), path depth, and per-cell-type delay breakdown.

### Routing Estimation (`nosis/wirelength.py`)

Wire-length model derived from cell count and net fanout. Base interconnect delay of 0.3 ns/hop, scaled by sqrt(fanout), with global routing overhead for high-fanout nets (>16 consumers). Combines with logic delay from timing.py for a total critical-path estimate including routing.

### Area (`nosis/resources.py`)

Exact physical area calculation from mapped cell counts. Each ECP5 slice holds 2 LUT4, 2 FF, 1 CCU2C. Slice count is the maximum of ceil(LUTs/2), ceil(FFs/2), and CCU2C count — the binding constraint. BRAM tiles are 1:1 with DP16KD. DSP tiles hold 2 MULT18X18D. Reports packing efficiency and the binding resource.

### Power (`nosis/power.py`)

Static and dynamic power estimation from ECP5 cell power model (1.1V core, -6 speed grade). Static power from leakage per cell type. Dynamic power from cell count, toggle rate (default 12.5%), and clock frequency. Per-cell-type breakdown.

### Congestion (`nosis/congestion.py`)

Fanout distribution analysis: histogram of net degrees, identification of high-fanout (>16) and very-high-fanout (>64) nets. Density score from weighted combination of average fanout, high-fanout percentage, and maximum fanout.

### Clock Domains (`nosis/clocks.py`)

Groups FFs by clock net. Detects clock domain crossings by tracing combinational logic cones from each FF's D input to find FFs in other domains. Reports domain count and crossing list.

### Design Warnings (`nosis/warnings.py`)

Multi-clock detection, undriven nets, floating output ports, high-fanout threshold violations. Returns categorized warnings.

### Equivalence Checking (`nosis/equiv.py`)

Three methods: exhaustive simulation (complete for ≤16 input bits), SAT-based via PySAT Glucose3 (CNF encoding of AND, OR, XOR, NOT, EQ, NE, MUX, LT, LE, GT, GE), and random simulation fallback. Counterexample extraction on non-equivalence.

### Formal Verification (`nosis/formal.py`)

Simulation-based bounded model checking. Assertion checking (output net equals expected value over N cycles). Reachability analysis (can an output ever produce a target value).

### Constraints (`nosis/constraints.py`, `nosis/sdc.py`)

LPF parsing: LOCATE (pin assignment), IOBUF (I/O standard, drive, pull, slew), FREQUENCY, SYSCONFIG. SDC parsing: create_clock, set_input_delay, set_output_delay, set_false_path. Port validation against the synthesized design.

## Repository Surface

| File | Role |
|------|------|
| `nosis/__init__.py` | Package version |
| `nosis/cli.py` | Command-line interface: parse → lower → optimize → infer → map → emit |
| `nosis/ir.py` | Intermediate representation: `Design`, `Module`, `Cell`, `Net`, 30 `PrimOp` variants |
| `nosis/frontend.py` | pyslang frontend: parse, elaborate, RTL-to-IR lowering |
| `nosis/passes.py` | Optimization: constant folding, dead code elimination |
| `nosis/fsm.py` | FSM extraction, encoding classification, cell annotation |
| `nosis/bram.py` | BRAM inference: array patterns to DP16KD |
| `nosis/dsp.py` | DSP inference: multiply patterns to MULT18X18D |
| `nosis/carry.py` | Carry chain inference: addition/subtraction to CCU2C |
| `nosis/techmap.py` | ECP5 technology mapping: LUT4 (TRELLIS_SLICE), FF (TRELLIS_FF) |
| `nosis/json_backend.py` | nextpnr-compatible JSON netlist serialization |
| `nosis/equiv.py` | Equivalence checking: exhaustive and random simulation |
| `nosis/validate.py` | RTL simulation harness: iverilog testbench generation and comparison |
| `nosis/ecp5_prims.sv` | ECP5 vendor primitive stubs for slang elaboration |
| `tests/test_ir.py` | IR dataclass and graph construction tests (11) |
| `tests/test_frontend.py` | pyslang parsing and IR lowering tests against real HDL (11) |
| `tests/test_passes.py` | Constant folding and DCE tests (7) |
| `tests/test_fsm.py` | FSM extraction and encoding classification tests (7) |
| `tests/test_bram.py` | BRAM inference tests (5) |
| `tests/test_dsp.py` | DSP inference tests (4) |
| `tests/test_carry.py` | Carry chain inference tests (5) |
| `tests/test_equiv.py` | Equivalence checking tests (5) |
| `tests/test_techmap.py` | ECP5 technology mapping tests (5) |
| `tests/test_json_backend.py` | JSON output format tests (6) |
| `tests/test_validate.py` | Simulation harness tests (6) |
| `tests/test_regression.py` | Regression tests against real RIME designs (38) |
| `.github/workflows/ci.yml` | CI: lint + unit tests across Python 3.11-3.13 |

110 tests total.

## Dependencies

**Runtime:**
- Python 3.11+
- pyslang (slang Python bindings, built from source with `-DSLANG_INCLUDE_PYLIB=ON`)

**Build (for pyslang):**
- CMake 3.24+
- C++20 compiler (MSVC 14.44+, GCC 11+, Clang 14+)
- Python development headers

**Validation (optional):**
- iverilog (Icarus Verilog, for RTL simulation)
- vvp (iverilog runtime)

**Development:**
- pytest
- ruff

All dependencies are MIT, BSD, or ISC licensed. No GPL code is linked or imported.

## Install

```
pip install -e ".[dev]"
```

Set `NOSIS_PYSLANG_PATH` to the directory containing the built `pyslang` module if it is not installed system-wide:

```
export NOSIS_PYSLANG_PATH=/path/to/slang/build/lib
```

## Usage

Synthesize a single file:

```
nosis input.sv --top top --target ecp5 -o output.json
```

Synthesize a multi-file design:

```
nosis top.sv uart_rx.sv uart_tx.sv flash_spi.sv --top top -o output.json
```

With preprocessor defines and include paths:

```
nosis design.sv -DWIDTH=32 -DDEBUG -I./includes --top top -o output.json
```

Skip optimization (useful for debugging the lowering):

```
nosis input.sv --top top --no-opt -o output.json
```

Print synthesis statistics:

```
nosis input.sv --top top --stats
```

Verbose output (timing per stage):

```
nosis input.sv --top top --stats --verbose
```

The output JSON is consumed by nextpnr:

```
nextpnr-ecp5 --25k --package CABGA256 --lpf board.lpf --json output.json --textcfg output.config
ecppack --compress output.config output.bit
```

## Development

Run the full test suite:

```
pytest tests/ -v
```

Run only the unit tests (no pyslang dependency):

```
pytest tests/test_ir.py tests/test_passes.py tests/test_techmap.py tests/test_json_backend.py tests/test_fsm.py tests/test_bram.py tests/test_dsp.py tests/test_carry.py tests/test_equiv.py -v
```

Run the regression tests (requires pyslang and RIME source):

```
NOSIS_PYSLANG_PATH=/path/to/slang/build/lib pytest tests/test_regression.py tests/test_frontend.py -v
```

Lint:

```
ruff check .
```

Generate API documentation:

```
pip install pdoc
pdoc nosis --output-directory docs/
```

The generated HTML covers every module, class, function, and dataclass with their docstrings and inline examples.

## License

MIT.
