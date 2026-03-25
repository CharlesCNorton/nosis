# Nosis

Correctness-first open-source FPGA synthesis targeting Lattice ECP5.

Nosis synthesizes SystemVerilog and Verilog into technology-mapped netlists for the ECP5 FPGA family. It produces smaller netlists than yosys on real RISC-V SoC designs while maintaining provable functional equivalence at every optimization step.

On a 13-file PicoRV32 SoC (PicoRV32 + UART + SPI flash + SD + SDRAM + CRC32), nosis produces 3,202 LUT slices after optimization. Yosys `synth_ecp5` on the same design produces approximately 4,700. Both target ECP5-25F.

## Architecture

```
  SystemVerilog / Verilog source
           |
     [1. Frontend]         pyslang IEEE 1800-2017 parse, elaborate, type-check
           |
     [2. IR Lowering]      behavioral HDL to technology-independent netlist
           |
     [3. Optimization]     12 passes in 6 iterative rounds + post-optimization
           |
     [4. Inference]        FSM, BRAM, DSP, carry chain annotation
           |
     [5. Tech Mapping]     IR primitives to ECP5 cells with dual-LUT packing
           |
     [6. Slice Packing]    chain merge, dedup, constant simplify, dead bit strip
           |
     [7. JSON Backend]     nextpnr-compatible netlist
           |
     [8. Verification]     equivalence checking, formal BMC, RTL simulation
           |
     nextpnr-ecp5 --> ecppack --> bitstream
```

Nosis covers stages 1 through 8. Place-and-route (nextpnr) and bitstream packing (ecppack) are external.

## Synthesis Results

Validated against real hardware designs from the [RIME](https://github.com/CharlesCNorton/rime) project (Resident IcePi Management Environment for Lattice ECP5). All numbers are post-optimization, post-slice-packing.

| Design | Source | LUT slices | FFs | CCU2C | Notes |
|--------|--------|-----------|-----|-------|-------|
| uart_tx | 1 file, 76 lines | 9 | 46 | 32 | 4-state FSM, baud rate counter |
| uart_rx | 1 file, 79 lines | 16 | 47 | 32 | Mid-bit sampling, baud counter |
| sdram_bridge | 1 file, 130 lines | 14 | 220 | 14 | 128-bit burst aggregator |
| sdram_controller | 1 file, 350 lines | 64 | 180 | 18 | W9825G6KH 32 MB SDR controller |
| rime_pcpi_crc32 | 1 file, 50 lines | 0 | 32 | 0 | PicoRV32 PCPI CRC32 coprocessor |
| PicoRV32 SoC | 13 files | 3,202 | 891 | ~600 | RV32IMC + UART + SPI + SD + SDRAM |

The SoC design includes PicoRV32 (RV32IMC), UART TX/RX with FIFOs, SPI flash engine, SD SPI engine, 32 MB SDRAM controller and bridge, CRC32 coprocessor, hardware watchdog, IRQ controller, boot ROM, and GPIO. All output ports are driven. Zero undriven nets after optimization.

Unoptimized (lowering + tech mapping only, no passes):

| Design | LUT slices | FFs | CCU2C |
|--------|-----------|-----|-------|
| uart_tx | 117 | 46 | 128 |
| uart_rx | 149 | 47 | 128 |
| rime_v (RV32IMC CPU) | 2,659 | 1,727 | 275 |
| Thaw (flash service image) | 8,521 | 6,143 | 1,044 |
| PicoRV32 SoC | 30,562 | 16,825 | 4,094 |

These locked counts serve as regression baselines. Any pipeline change that alters them fails CI.

## Synthesis Pipeline

### Stage 1: Frontend (`nosis/frontend.py`)

Parses SystemVerilog and Verilog through [slang](https://github.com/MikePopoloski/slang), a complete IEEE 1800-2017 compiler. Slang handles preprocessing, parsing, elaboration, parameter resolution, type checking, and constant evaluation.

The frontend:

- Accepts `.sv` and `.v` source files with `-D` defines and `-I` include paths
- Resolves parameters and generate blocks at elaboration
- Type-checks all expressions with full width inference
- Reports errors with exact source locations
- Provides ECP5 vendor primitive stubs (`ecp5_prims.sv`) for USRMCLK, EHXPLLL, OSCG, DTR, SEDGA, MULT18X18D, DP16KD, CCU2C, BB, and 30+ other Lattice-specific cells
- Strips simulation-only constructs (`$display`, `$finish`, `$readmemh`, etc.) with warnings
- Detects latch inference from incomplete `if/case` in `always_comb`
- Handles `defparam` with deprecation warnings
- Respects `(* synthesis off *)` pragmas

### Stage 2: IR Lowering (`nosis/frontend.py`, `nosis/ir.py`)

Walks the elaborated AST and converts it into the Nosis intermediate representation: a flat, technology-independent netlist of 30 primitive operations connected by typed nets.

| Category | Operations |
|----------|------------|
| Combinational logic | AND, OR, XOR, NOT, MUX, PMUX, REDUCE_AND, REDUCE_OR, REDUCE_XOR |
| Arithmetic | ADD, SUB, MUL, DIV, MOD, SHL, SHR, SSHR |
| Comparison | EQ, NE, LT, LE, GT, GE |
| Bit manipulation | CONCAT, SLICE, REPEAT, ZEXT, SEXT |
| Sequential | FF, LATCH |
| Memory | MEMORY |
| Constants / Ports | CONST, INPUT, OUTPUT |

The lowering handles:

- **`always_ff`:** Clock edge extraction, non-blocking assignment collection, FF creation with Q-redirect (all consumers of the target net are redirected to the FF Q output so DCE does not orphan live logic), synchronous and asynchronous reset inference from `if (rst)` patterns
- **`always_comb` / `always @(*)`:** Pure combinational wiring with latch inference detection
- **`if/else`:** MUX trees with constant selector folding at lowering time
- **`case` / `casez` / `casex`:** Parallel MUX chains with EQ comparisons per case label
- **Concatenation LHS decomposition:** `{a, b, c, d} <= val` splits into per-element SLICE assignments
- **Continuous assignments:** `assign x = expr` wires the driver directly
- **Hierarchy flattening:** Sub-module instances are recursively lowered with prefixed net/cell names. Port connections are wired by direction. Vendor primitives (USRMCLK, EHXPLLL, etc.) are skipped as black boxes.
- **Multi-dimensional arrays:** Flattened to 1D MEMORY cells
- **Packed structs/unions:** Treated as bitvectors (slang flattens them)
- **Generate blocks:** Already unrolled by slang; members walked normally
- **Interface instances:** Members extracted as regular nets
- **Replication (`{N{expr}}`):** REPEAT cells with count parameter

### Stage 3: Optimization (`nosis/passes.py` and supporting modules)

Twelve optimization passes run in up to six iterative rounds, followed by four post-optimization stages. The pipeline runs to fixed point: iteration stops when no round reduces the cell count.

**Iterative passes (per round):**

1. **Constant folding** (`constant_fold`): Replace cells with all-constant inputs by their evaluated result. Runs to fixed point within each round. Handles all combinational PrimOps including MUX with constant selector.

2. **Identity simplification** (`identity_simplify`): `a & 0xFF = a`, `a | 0 = a`, `a ^ 0 = a`, `a + 0 = a`, `a * 1 = a`, `a << 0 = a`, `MUX(0,a,b) = a`, `MUX(1,a,b) = b`, `NOT(NOT(a)) = a`. Redirects both cell consumers and module port references.

3. **Boolean optimization** (`boolopt.py`): AND/OR distribution `(a & b) | (a & c) = a & (b | c)`, idempotent `a & a = a`, `a ^ a = 0`, complement `a & ~a = 0`. Technology-aware variant respects LUT4 input budget.

4. **Constant FF removal** (`remove_const_ffs`): FFs with constant D input are replaced by the constant value.

5. **Common subexpression elimination** (`cse.py`): Hash-based deduplication of cells with identical `(op, input_nets, params)` signatures.

6. **Functional identity elimination** (`_eliminate_functional_identities`): Exhaustive truth table evaluation for cells with up to 4 inputs and 1-bit output. If the output equals any single input for all combinations, the cell is an identity and is bypassed.

7. **HIT equivalence merging** (`_merge_hit_equivalent`): Two cells with the same input net set that compute the same Boolean function (identical truth table) are merged regardless of structural differences. Derived from the Higher Inductive Type principle: a function is defined by its action on inputs, not its syntactic form.

8. **Don't-care input elimination** (`_eliminate_dont_care_inputs`): If toggling an input never changes the output (truth table is symmetric under that input), the input is dropped. Fewer inputs produce fewer LUT4 cells downstream.

9. **MUX chain merging** (`merge_mux_chains`): Deduplicates EQ cells sharing the same `(selector, constant)` pair across different case targets. Eliminates MUX cells where both branches are identical.

10. **MUX-to-AND simplification** (`_simplify_mux_with_zero`): `MUX(sel, A, 0) = AND(NOT(sel), A)` and `MUX(sel, 0, B) = AND(sel, B)`. Reduces LUT input count from 3 to 2, improving dual-LUT packing.

11. **Narrow constant MUX** (`_narrow_const_mux`): Placeholder for partial-match MUX width reduction (currently delegated to ECP5-level constant LUT simplification).

12. **Dead code elimination** (`dead_code_eliminate`): Backward reachability from outputs and FF D inputs. Unreachable cells and nets are removed.

**Post-optimization stages:**

13. **Backward don't-care propagation** (`dontcare.py`): Identifies FFs whose outputs are always AND-masked, meaning their value outside the mask's active window is irrelevant. Derived from the duality principle of stable categories.

14. **Reachable-state equivalence merging** (`reqmerge.py`): Simulates the design for 200 clock cycles with random inputs, tracking per-net value signatures. Nets that carry identical values across all reachable states are merged. Safety guards exclude nets in the output-reachable cone and nets feeding FF D inputs (sequential feedback). Derived from quotient types in HoTT.

15. **SAT-based constant proof** (`satconst.py`): For nets observed as constant during simulation, constructs the combinational logic cone and exhaustively evaluates all input combinations (up to 16 cone inputs). If the net's value is invariant, it is provably constant and replaced with a CONST cell. Cones containing FF boundaries are excluded.

16. **Cut-based LUT remapping** (`cutmap.py`): Enumerates 4-input cuts for single-bit combinational cells. When a multi-cell cone fits in 4 inputs, the composed truth table is computed and intermediate cells are absorbed.

### Stage 4: Inference

Four annotation-only passes that tag cells for specialized technology mapping. No structural changes.

- **FSM extraction** (`fsm.py`): Identifies state machine feedback loops through MUX/EQ trees. Classifies encoding (sequential, one-hot, Gray, binary). Does not re-encode.
- **BRAM inference** (`bram.py`): Tags MEMORY cells for DP16KD (16Kx1 through 512x36) or TRELLIS_DPR16X4 (distributed RAM, 16x4). Detects write mode (read-before-write vs write-through) and output register absorption.
- **DSP inference** (`dsp.py`): Tags MUL cells for MULT18X18D (up to 18x18) or decomposed 4x MULT18X18D (up to 36x36). Detects multiply-accumulate patterns for ALU54B mapping.
- **Carry chain inference** (`carry.py`): Tags ADD/SUB cells for CCU2C carry chains (2 bits per cell, `ceil(N/2)` cells for N-bit arithmetic).

### Stage 5: Technology Mapping (`nosis/techmap.py`)

Converts the IR into ECP5-specific cells.

- **LUT mapping:** Combinational cells map to TRELLIS_SLICE with computed LUT4 INIT values. Multi-bit operations are dual-packed: two adjacent bits share one TRELLIS_SLICE using both LUT0 and LUT1 slots, halving slice count for bitwise operations.
- **FF mapping:** One TRELLIS_FF per bit. Clock, data, reset, enable connections. Parameters: GSR, CEMUX, CLKMUX, LSRMUX, REGSET, SRMODE.
- **Arithmetic:** ADD/SUB map to CCU2C carry chains with XOR/XNOR base INIT and carry propagation.
- **Multiply:** Tagged MUL cells emit MULT18X18D or ALU54B (for MAC patterns).
- **Memory:** Tagged MEMORY cells emit DP16KD (block RAM) or TRELLIS_DPR16X4 (distributed RAM) with full address/data/control wiring.
- **PMUX:** Narrow cases (1-bit output, up to 4 cases) compute a single LUT4 truth table. Wider cases build balanced MUX trees with log2 depth.
- **Wiring:** CONCAT, SLICE, ZEXT, SEXT, REPEAT reassign bit indices without physical cells.
- **Inout:** BB (bidirectional buffer) cells for inout ports.

### Stage 6: Slice Packing (`nosis/slicepack.py`)

Post-mapping optimization on the ECP5 netlist.

1. **Constant LUT simplification:** Reduces truth tables when inputs are tied to constants. All-0 or all-1 results eliminate the LUT entirely.
2. **LUT deduplication:** Eliminates TRELLIS_SLICE cells with identical INIT and input bits, redirecting all references to the survivor.
3. **Buffer absorption:** Single-input LUT4 cells (buffers) are bypassed by wiring the consumer directly to the source signal.
4. **Dead LUT bit elimination:** Strips unused LUT1 functions from dual-LUT slices, derived from the cofiber construction in stable categories (the difference between the full and simplified design is zero for dead bits).
5. **Priority-cut chain merging:** When LUT A's output feeds LUT B's input and their combined variable inputs fit in 4, LUT B absorbs LUT A's function via composed truth table. Runs iteratively up to 5 rounds. Handles both single-fanout and limited multi-fanout (up to 3 consumers with up to 2 variable inputs).
6. **Dual-LUT packing:** Pairs independent single-LUT TRELLIS_SLICE cells into dual-LUT slices (LUT0 + LUT1), reducing total slice count by up to 50%.

### Stage 7: JSON Backend (`nosis/json_backend.py`)

Serializes the ECP5 netlist to the nextpnr JSON format. Handles parameter encoding (hex INIT values to 16-bit binary strings, other hex to 32-bit binary, string parameters passed through), connection bit encoding (integers for signals, strings `"0"`/`"1"`/`"x"` for constants), port direction classification by name convention, and `hide_name` annotation for internal nets.

### Stage 8: Verification

- **Equivalence checking** (`equiv.py`): Exhaustive simulation for designs up to 16 input bits (provably complete). SAT-based via PySAT Glucose3 for larger designs (CNF encoding of all PrimOps including full-adder chains for multi-bit ADD/SUB). Random simulation fallback with 10,000 vectors.
- **Formal BMC** (`formal.py`): Bounded model checking via simulation and exhaustive evaluation. Assertion checking, reachability analysis, optimization equivalence verification, sequential equivalence with FF state carry-forward.
- **RTL simulation** (`validate.py`): Generates deterministic Verilog testbenches, compiles through iverilog, compares cycle-by-cycle outputs between RTL and post-synthesis netlist.
- **Post-synthesis Verilog** (`postsynth.py`): Generates behavioral Verilog from the ECP5 netlist with simulation models for TRELLIS_SLICE, TRELLIS_FF, CCU2C, DP16KD, and MULT18X18D.

## HoTT-Inspired Optimization

Five optimization techniques are derived from concepts in Homotopy Type Theory:

1. **Quotient types** (reachable-state equivalence merging): The net space is quotiented by a simulation-derived equivalence relation. Nets carrying identical values across all reachable states are merged.

2. **Higher Inductive Types** (truth table equivalence): Two cells with identical truth tables are equivalent regardless of structural differences. A function is defined by its action on inputs, not its syntactic form.

3. **Encode-decode method** (don't-care input elimination): Build the map from N-input to (N-1)-input function by dropping a candidate input. If the map is an equivalence (same truth table), the input is don't-care.

4. **Cofiber / zero object** (dead LUT bit elimination): The cofiber of the map from simplified to full design is zero for dead LUT bits. Stripping them preserves all information.

5. **Duality principle** (backward don't-care propagation): Forward constant propagation has a dual on the backward observation cone. Nets masked by downstream AND gates are don't-care in certain states.

## Analysis Passes

Beyond synthesis, nosis provides analysis passes on the IR and mapped netlist:

| Pass | Module | Function |
|------|--------|----------|
| Static timing | `timing.py` | Forward-propagation delay model (ECP5 -6 speed grade), critical path traceback, per-pin LUT4 delay (A=0.33ns, D=0.42ns) |
| Routing estimation | `wirelength.py` | Wire-length model from cell count and fanout via Rent's rule, dedicated clock/carry routing, combined logic+routing delay |
| Area calculation | `resources.py` | Exact slice packing (2 LUT4 + 2 FF + 1 CCU2C per slice), BRAM/DSP tile counts, packing efficiency, binding resource identification |
| Power estimation | `power.py` | Static leakage + dynamic switching per cell type (1.1V, -6 grade), clock tree power, simulation-based toggle rate measurement |
| Congestion analysis | `congestion.py` | Fanout histogram, high-fanout net identification, Rent's-rule routing metric |
| Clock domain analysis | `clocks.py` | FF grouping by clock net, clock domain crossing detection with source/dest FF identification, 2-FF synchronizer insertion |
| Design warnings | `warnings.py` | Undriven nets, floating outputs, multi-clock detection, high-fanout threshold violations |
| Logic cone extraction | `cone.py` | Combinational fan-in isolation for targeted equivalence checking |
| Netlist diff | `diff.py` | Cell count deltas, port changes, structural comparison between two synthesis runs |
| Incremental synthesis | `incremental.py` | IR snapshots, cell-level hashing, delta computation for partial re-mapping |
| Constraint parsing | `constraints.py`, `sdc.py` | LPF pin/IO/frequency constraints, SDC clock/delay/false-path/multicycle constraints, specify block timing arcs |
| Test vector generation | `testvec.py` | Deterministic test vectors from port signatures for equivalence and validation |

## ECP5 Primitive Coverage

Nosis targets Lattice ECP5. Supported primitives:

- **Logic:** TRELLIS_SLICE (2x LUT4 + 2x FF + carry), CCU2C (carry chain), TRELLIS_FF, TRELLIS_DPR16X4 (distributed RAM)
- **Block RAM:** DP16KD (true dual-port 16Kbit, all six width configurations)
- **DSP:** MULT18X18D (18x18 signed/unsigned), ALU54B (54-bit ALU with accumulate)
- **PLL/Clock:** EHXPLLL, EHXPLLJ, CLKDIVF, DCCA, DCC, DCSC, DQSCE, ECLKSYNCB, ECLKBRIDGECS, OSCG, PCSCLKDIV, EXTREFB
- **I/O:** BB, IB, OB, OBZ, BBPU, BBPD, IBPU, IBPD
- **DDR I/O:** IDDRX1F, IDDRX2F, IDDR71B, ODDRX1F, ODDRX2F, ODDR71B, OSHX2A, ISHX2A, TSHX2DQA, TSHX2DQSA
- **I/O delay:** DELAYF, DELAYG, DQSBUFM
- **I/O registers:** IFS1P3BX, IFS1P3DX, OFS1P3BX, OFS1P3DX
- **System:** USRMCLK, GSR, SGSR, PUR, JTAGG, DTR, SEDGA, OSCG, START, TSALL, BCINRD
- **SerDes:** DCUA (dual-channel 5G transceiver)

All 30+ vendor primitives have stub declarations in `ecp5_prims.sv` for slang elaboration and black-box entries in the hierarchy module for synthesis pass-through.

## Repository

43 source modules, 47 test modules, 609 tests, 21,000 lines of Python.

| Module | Role |
|--------|------|
| `nosis/ir.py` | IR: `Design`, `Module`, `Cell`, `Net`, 30 `PrimOp` variants, Verilog emission |
| `nosis/frontend.py` | pyslang frontend: parse, elaborate, lower to IR, hierarchy flattening |
| `nosis/passes.py` | 12 optimization passes + pipeline orchestration |
| `nosis/boolopt.py` | Boolean algebra: AND/OR distribution, complement, idempotent |
| `nosis/cse.py` | Hash-based common subexpression elimination |
| `nosis/cutmap.py` | Cut-based LUT4 remapping with composed truth tables |
| `nosis/dontcare.py` | Backward don't-care propagation |
| `nosis/reqmerge.py` | Reachable-state equivalence merging (HoTT quotient types) |
| `nosis/satconst.py` | SAT-based constant proof via exhaustive cone evaluation |
| `nosis/eval.py` | Single source of truth for PrimOp evaluation semantics |
| `nosis/equiv.py` | Equivalence checking: exhaustive, SAT, random simulation |
| `nosis/formal.py` | Bounded model checking and sequential equivalence |
| `nosis/techmap.py` | ECP5 technology mapping with dual-LUT packing |
| `nosis/slicepack.py` | Post-mapping: chain merge, dedup, buffer absorb, dual-LUT pack |
| `nosis/json_backend.py` | nextpnr-compatible JSON serialization |
| `nosis/fsm.py` | FSM extraction and encoding classification |
| `nosis/bram.py` | BRAM inference: DP16KD and DPR16X4 |
| `nosis/dsp.py` | DSP inference: MULT18X18D and ALU54B MAC detection |
| `nosis/carry.py` | Carry chain inference: CCU2C |
| `nosis/lutpack.py` | IR-level cascaded operation merging |
| `nosis/timing.py` | Static timing analysis with per-pin LUT4 delay model |
| `nosis/wirelength.py` | Routing delay estimation via Rent's rule |
| `nosis/resources.py` | Area calculation and device utilization reporting |
| `nosis/power.py` | Static + dynamic power estimation with toggle rate measurement |
| `nosis/congestion.py` | Fanout analysis and routing pressure estimation |
| `nosis/clocks.py` | Clock domain analysis and CDC detection |
| `nosis/cone.py` | Combinational fan-in cone extraction |
| `nosis/blackbox.py` | Black box registry with full ECP5 vendor primitive declarations |
| `nosis/hierarchy.py` | Sub-module instance support and vendor primitive skip list |
| `nosis/constraints.py` | LPF constraint parsing |
| `nosis/sdc.py` | SDC constraint and specify block parsing |
| `nosis/validate.py` | RTL simulation harness with testbench generation |
| `nosis/postsynth.py` | Post-synthesis Verilog generation with ECP5 cell models |
| `nosis/incremental.py` | IR snapshots and incremental re-mapping |
| `nosis/diff.py` | Netlist structural comparison |
| `nosis/warnings.py` | Design warning detection |
| `nosis/readmem.py` | `$readmemh`/`$readmemb` file parsing for BRAM initialization |
| `nosis/retiming.py` | Register retiming (forward/backward) and high-fanout duplication |
| `nosis/cli.py` | Command-line interface with `--stats`, `--benchmark`, `--ecppack` |
| `nosis/ecp5_prims.sv` | ECP5 vendor primitive stubs for slang elaboration |

## Design Principles

1. **Correctness over optimization.** Every optimization pass preserves functional equivalence. The output is verifiable against the input through built-in equivalence checking.

2. **Respect the RTL.** Designer intent is preserved. State encodings, explicit structure, and named signals survive into the netlist unchanged.

3. **Single evaluation semantics.** Every PrimOp has exactly one evaluation function in `eval.py`. Constant folding, equivalence checking, simulation, and truth table computation all use the same code path.

4. **Provable where possible.** SAT-based constant proof, exhaustive truth table verification for small cones, formal BMC for assertions. Simulation-based methods are clearly labeled as probabilistic.

5. **Pure Python.** Correctness is easier to verify in Python than C++. The entire pipeline is 21,000 lines with no C extensions beyond the pyslang dependency.

## Dependencies

**Runtime:**
- Python 3.11+
- pyslang (`pip install pyslang` for versions 7.x-10.x, or built from source)

**Downstream (for place-and-route):**
- nextpnr-ecp5 (OSS CAD Suite)
- ecppack (OSS CAD Suite)

**Validation (optional):**
- iverilog and vvp (Icarus Verilog)
- PySAT (`pip install python-sat`) for SAT-based equivalence checking

**Development:**
- pytest, hypothesis, ruff

## Install

```
pip install -e ".[dev]"
```

If pyslang is built from source rather than pip-installed:

```
export NOSIS_PYSLANG_PATH=/path/to/slang/build/lib
```

## Usage

Synthesize a design:

```
nosis input.sv --top top --target ecp5 -o output.json
```

Multi-file with defines and includes:

```
nosis top.sv uart_rx.sv uart_tx.sv flash_spi.sv --top top -DWIDTH=32 -I./includes -o output.json
```

Statistics and timing:

```
nosis input.sv --top top --stats --verbose
```

Machine-readable benchmark output:

```
nosis input.sv --top top --benchmark
```

Feed to nextpnr and ecppack:

```
nextpnr-ecp5 --25k --package CABGA256 --lpf board.lpf --json output.json --textcfg output.config
ecppack --compress output.config output.bit
```

End-to-end with integrated ecppack:

```
nosis input.sv --top top -o output.json --ecppack output.bit
```

## Development

Full test suite (609 tests):

```
pytest tests/ -v
```

Unit tests only (no pyslang dependency):

```
pytest tests/test_ir.py tests/test_eval.py tests/test_passes.py tests/test_techmap.py tests/test_json_backend.py -v
```

Regression tests (requires pyslang and RIME source):

```
pytest tests/test_regression.py tests/test_frontend.py tests/test_mux_merge.py tests/test_connectivity.py -v
```

Lint:

```
ruff check .
```

## License

MIT.
