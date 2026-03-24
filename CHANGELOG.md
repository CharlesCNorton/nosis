# Changelog

## Unreleased

### Frontend hierarchical-lowering fixes (full-SoC support)
Found while bringing up the RIME boot image, a deep hierarchy the yosys flow
handles but nosis did not. Each is a real lowering bug or missing feature that
left nets undriven or mis-lowered. Together they take RIME from 18 undriven IR
nets after lowering to **0**, with no test regressions.

- **Use-before-declaration.** The root cause behind most of the damage. Several
  silicon-proven RIME modules reference a reg/wire in a port list or expression
  above its own declaration line. slang responds by marking the connection, or
  the *entire* enclosing procedural block, as `Invalid`; nosis suppressed the
  diagnostic and was then left with an Invalid body, so whole `always_ff` blocks
  silently vanished (the SDRAM controller lowered to zero flip-flops). The parse
  now passes `--allow-use-before-declare`, so slang binds these correctly.
- **Net-declaration alias ordering.** `wire tx = svc_tx;` where the RHS is
  driven by a sub-instance output (or a later block) was dropped because the
  alias was lowered before its RHS had a driver. Now deferred and resolved
  after all blocks and sub-instances are wired.
- **Forward-referenced instance connections.** A backstop for connections slang
  still elaborates to `Invalid`: the textual `.port(net)` survives in the
  instantiation syntax, so a simple-identifier connection is recovered there.
- **Constant part-select / bit-select register writes.** `reg[hi:lo] <= x` and
  `reg[i] <= x` were lowered as a throwaway slice read, never driving `reg`.
  They now perform a read-modify-write `reg <= {reg[msb:hi+1], x, reg[lo-1:0]}`.
- **Dynamic indexed part-select reads and writes.** `reg[base +: w]` /
  `reg[base -: w]` with a runtime base (e.g. `buf[127 - idx*8 -: 8]`) were
  ignored on write and read the wrong slice. Both directions are now lowered as
  per-bit select fabric; a base that folds to a constant (an unrolled loop
  index) collapses to a static slice so the cell count stays bounded.
- **Per-call function inlining.** User functions were inlined by reusing the
  formal and return-variable nets across every call, so repeated calls (RIME's
  `svc_crc8` CRC accumulation, `le32` byte extraction) collided and returned
  undriven. Each call now gets a unique scope: formals bind to the call's actual
  arguments, and the return variable and locals are per-call nets. Bodies are
  collected through the blocking-assignment engine, so bit/part-select writes to
  the return value and if/case logic inside a function all lower correctly.
- **Loop variable declared outside the `for`.** `integer i; ... for (i = 0; ...)`
  leaves slang's `loopVars` empty, so the unroller skipped the loop and left `i`
  undriven. The loop variable and its initial value are now recovered from the
  loop's initializer assignment.
- **Word-aligned dynamic part-select.** The general per-bit expansion above is
  O(width x reg-width) and blows up a 128-bit lane access into thousands of
  LUTs (sdram_bridge: 851 -> 5402 LUT4). When the index is affine in one
  variable with coefficient equal to the window width (the byte/word-lane
  pattern `buf[C - idx*W -: W]`), the windows are proven aligned and the access
  lowers to one mux per word instead (sdram_bridge back to 668 LUT4, RIME from
  ~45k IR cells back to ~9.7k). Detection is by exact affine analysis, so it
  only fires when soundness is guaranteed; anything else keeps the per-bit path
  (sdram_bridge's lane access: 5402 -> 668 LUT4; RIME: ~45k -> ~9.8k IR cells).

With all of the above, the RIME boot image lowers to 0 undriven nets and maps
to 7,298 LUT4 / 721 CCU2C / 4,652 FF on the ECP5U-25F (yosys 0.63 synth_ecp5
on the same sources: 6,336 / 510 / 4,479), building through nextpnr and
ecppack to a valid bitstream at under half the device. All four bundled
designs pass RTL-vs-post-synth-netlist equivalence.

### Correctness
- **Case arms without an assignment no longer take the `default` value.** In
  the sequential collector, a `case` whose `default` assigns a register seeded
  the register's mux chain with the default value; explicit arms that do not
  assign that register contributed no chain node, so at gate level those arms
  loaded the default's value instead of holding. Any FSM with
  `default: err <= '0` style arms mis-latched registers on states that never
  touch them (RIME's auto_recovery `hold`/`exit_*` outputs). Explicit arms now
  insert a hold node for every default-assigned register they do not assign.
- **Stale driver aliases repaired before mapping.** Lowering-time register
  renaming and const-FF replacement can leave a net whose `.driver` points at
  a cell that does not list it among its outputs, with only some consumers
  redirected — remaining readers map to netlist bits nothing drives (frozen
  FSMs via undriven clock-enables, floating output ports). A repair pass now
  runs before clock-enable extraction and again at mapper entry: every
  referenced net object (including ones orphaned out of `mod.nets`) whose
  claimed driver disagrees is redirected to the driver's true output, with a
  name-based fallback for driverless register aliases.
- **Gate-level validation asserts reset first.** Test vectors drove `rst`
  randomly from cycle 0, so the RTL reference simulated from all-X state,
  wandered through `case` defaults the hardware never reaches, and diverged
  from the netlist's concrete power-on state. Vector generation now emits a
  3-cycle reset preamble and pins reset inactive through the structured
  sections; the random tail may still toggle it, which both simulations then
  see identically from defined state.
- **Carry-chain operand convention.** The CCU2C emission placed operand `a` on
  the B pins and operand `b` on the D pins, but the hardware carry is computed
  from pins A and B (`COUT = (A&B) | (CIN & (A^B))`), so any add or subtract
  with a *variable* second operand produced garbage sums — `x + 1` style
  counters worked only as a degenerate case (`CIN=1` makes `CIN&(A^B)` reduce
  to the right increment carry). Every design gate-validated before this fix
  happened to use only constant second operands; RIME's tide module
  (`128 ± sine`) was the first to exercise the broken path and failed
  RTL-vs-netlist equivalence. Both operands now ride the A/B pins with
  `INIT = A XOR B`, constants ride the pins as literals, and subtraction
  inverts the subtrahend explicitly with `CIN = 1`. tide now passes gate-level
  equivalence, and the old convention's dead `+1` CIN fixup is removed.
- **Sized-literal parsing.** The literal regex allowed `h` in the optional
  signed-marker class, so a hex literal whose first digit looks like a base
  char misparsed: `64'hdeadbeef...` crashed (base `d`), and `8'hb0` silently
  became binary `0` — a wrong constant with no error. The signed marker is
  only `s`; fixed and covered by direct parse checks.
- **Self-loop breaking no longer crashes.** The combinational self-loop pass
  added replacement cells while iterating the cell dict, raising
  `RuntimeError: dictionary changed size during iteration` on designs that
  trigger it (RIME's arbor and bloom modules).

### Gate-level validation fixes
- `validate_design` post-synthesis simulation now actually runs. It previously failed to compile (`cell_models.v` was passed alongside a `postsynth.v` that already embeds the same modules — duplicate declarations) and the failure was silently masked by comparing the RTL output against itself, reporting PASS. The duplicate pass-in is gone, and a post-synth sim that cannot run is now a validation failure.
- Both simulations now receive the same stimulus. The RTL testbench used structured test vectors while the post-synth testbench used a fresh random sequence, so the cycle-by-cycle comparison was between different input streams.
- `postsynth.py` parses LUT INIT parameters in both encodings the mapper emits (16-char binary and 0x-prefixed hex). Comparator-chain LUTs use the hex form and previously simulated with INIT=0, so every LT/LE/GT/GE in the model returned constant 0.
- `TRELLIS_FF_SIM` instantiations now derive power-on state and reset value from the cell's `REGSET` parameter instead of reading an `init_value` attribute the mapper never sets.
- With these four fixes, `test_iverilog_cycle_accurate_uart_tx` passes as a true RTL-vs-netlist comparison for the first time.
- New `slicepack.tie_dont_care_inputs` pass: undriven LUT4 inputs are tied to ground only when the truth table is provably invariant on that pin.
- **Constant-output LUT propagation.** `simplify_constant_luts` reduced a LUT's truth table over its tied-constant inputs and, when the result was constant, *deleted* the LUT without touching its consumers — leaving every reader with an undriven input bit (39 such LUTs on the Thaw design). It now ties each consumer of a constant-output LUT to `"0"`/`"1"` before removing the cell, as constant propagation should. `test_no_lut4_with_undriven_signal_inputs` is now green on Thaw.
- **Constant-driven output ports in post-synth Verilog.** `postsynth.py` only emitted an `assign` for a port bit wired to an internal net, so an output tied to a constant (`assign pcpi_wait = 1'b0`) was left floating (`z`) in the post-synth simulation and every cycle counted as a mismatch. Output ports now always emit a driver, including for constant-tied bits.
- **RTL don't-care handling in the comparison.** `validate_design` compared RTL and post-synth output lines as raw strings, so an uninitialized RTL reg reading `x` on an inactive path never matched the synthesized `0`/`1`. The comparison is now token- and bit-aware: an RTL `x` is a wildcard (the golden reference is a don't-care there), while a defined RTL bit must still match, so a real dropped/corrupted bit is still caught. With these two fixes `rime_pcpi_crc32` passes equivalence — its previous 99 "mismatches" were entirely harness artifacts, not synthesis bugs.
- **`CEMUX`/`LSRMUX` ignored by the FF sim model.** The `TRELLIS_FF_SIM`
  instance passed only `INIT`/`REGSET`, so the model fell back to its default
  `CEMUX="CE"` regardless of what the mapper recorded. A register with no
  reset but a shared reset signal (the SD/install command registers in
  `auto_recovery` — `sd_op`, `sd_lba`, `sd_chunk_idx`, held across the states
  that do not write them) lowers to `D = rst ? Q : next`, which the mapper
  correctly maps as an **inverted** clock enable (`CEMUX="INV"`, `CE=rst`). The
  sim model, seeing the default `CE`, read the enable with the opposite sign:
  the FF held through the entire post-reset run, so `sd_op` stayed 0 while the
  RTL held `3'd1`, reported as 61 spurious mismatches. The constant arms mapped
  fine, which is why only the hold value diverged. The instance now normalizes
  `CEMUX`/`LSRMUX` to integer mode codes (`CE`/`INV`/tied-high and
  `LSR`/`INV`/tied-low) and passes them, so the model reproduces every slice
  mux setting — including tied-high `CEMUX="1 "` FFs, which the old string
  compare would have simulated as `~1 = 0` (never updating) had the parameter
  been passed. Integer codes also dodge Verilog's string-parameter
  width-truncation trap. The bitstream was never affected: nextpnr reads
  `CEMUX` from the JSON, so silicon always had the right polarity; only nosis's
  own equivalence check was blind to it. `auto_recovery` now passes gate-level
  equivalence (157 cycles, 0 mismatches).

### Area
- **Whole-array memory reset folds to LSR.** A register file or memory array
  cleared on reset (`if (rst) for i: mem[i] <= 0`) lowered to an all-zero
  constant-address write of every element, one 2:1 reset MUX per bit per
  element — and each MUX has a distinct hold input, so CSE cannot merge them. A
  write-enable that zero-writes *every* element is now recognized as an
  array-wide synchronous reset and routed to the FFs' built-in LSR
  (`REGSET=RESET`), dropping the reset MUXes entirely. Full coverage keeps the
  transform safe (a partial zero-write could be a store a later write must
  override; a whole-array reset is mutually exclusive with the guarded stores,
  so the priority change is moot). RIME's rime-i register file (32x32, reset
  loop) drops from 4,681 LUT4 to 3,657.
- **Constant-address memory reads collapse to wires.** A `mem[const]` read
  (protocol-field access into a byte buffer) built the full binary MUX tree and
  leaned on a later pass to fold the constant selects. The read tree now folds a
  constant select bit to its chosen half at emission, so a constant-address read
  costs no LUTs.
- **ROM extraction (`extract_roms` + `PrimOp.ROM`).** A dense constant `case`
  (an S-box, a sine table) lowers to a linear chain of `MUX(EQ(sel,k), .., k)`
  cells — 2^w EQ + 2^w MUX per lookup — which the optimizer then grinds on and
  the mapper emits as a chain of LUTs. Chains of 32+ constant arms on one
  selector are now collapsed into a ROM cell before optimization, and the
  mapper emits the balanced form: per output bit, one leaf LUT4 per low
  selector nibble plus a 2:1 mux tree over the high bits. RIME's vault module
  (16 inlined AES S-box lookups) went from 15,920 LUT4 to 4,460 and from 106 s
  to 19 s to synthesize.
- **Cone resynthesis (`resynth_cones`).** Pairwise chain merging cannot cross
  the 4-input barrier, so a cone computing one function of 4-6 variables
  accretes as a tower of half-filled LUTs. This pass grows each LUT's cone of
  single-fanout feeders, takes its exhaustive truth table over the support,
  and re-emits it flat: one LUT4 for support <= 4; for support 5-6 it first
  tries Ashenhurst decomposition (a 4-variable bound set with column
  multiplicity <= 2 gives `f = h(g(bound), free)` — exactly two LUT4s), and
  at support 5 falls back to a Shannon split (three LUT4s) when that still
  beats the cone. Every replacement is verified against the cone's truth
  table over all 2^s assignments before commit.
- **Constant-canonical CSE.** Every lowered branch mints its own CONST cell for
  a literal, so `x + 1` written in ten case arms produced ten separate adders
  whose B operands were ten different nets all holding 1 — and CSE, keying on
  net names, merged none of them. Signatures now canonicalize constant operands
  by (value, width): on the RIME image this deduplicated 240 CCU2C and 182 LUT4
  of repeated increment/decrement and compare logic.
- **Dead carry-tail trimming (`trim_dead_carry_tails`).** Width-reduced
  registers leave the high cells of full-width carry chains unread, but each
  dead cell's COUT still feeds the next dead cell, so plain dead-cell sweeps
  only ever saw the chain end. Reference-counted cascade removal unravels the
  whole tail back to the last live sum bit: 249 CCU2C on the RIME image.
- **Buffer-LUT absorption (`absorb_buffer_luts`).** Slicing, width
  reconstruction, and merging leave LUTs that pass one input pin straight
  through. Beyond being wasted cells, they sat *between* mergeable LUTs,
  breaking single-fanout chains. Rewiring their readers to the source bit and
  deleting them let chain merging cascade: 9,249 -> 7,532 LUT4 on RIME.
- **Clock-enable extraction (`extract_clock_enables`).** A register written as
  `state <= cond ? next : state` was emitted as a flip-flop plus a feedback mux
  costing a LUT per bit, and the whole hold-mux tree (with all its condition
  logic) stayed live because the register read its own output. This pass proves,
  per subtree, when the register actually changes and rewrites the flip-flop to
  use its native clock-enable input: `FF(D = update, CE = enable)`, with the
  hold branches removed. On a design as state-machine-heavy as RIME the freed
  feedback trees then dead-code away in bulk — the boot image dropped from
  21,015 to 9,431 LUT4 (from ~3.5x the yosys result to ~1.6x), verified against
  gate-level equivalence on the bundled designs and the full test suite.
- **Register width reduction (`reduce_register_width`).** SystemVerilog `enum {...}` with no base type is `int`, so a four-state FSM register was carried as 32 flip-flops driving 32-bit-wide mux trees. The new pass proves the high bits of a register are constant (every non-hold leaf of the D mux tree is a constant, or a zero-extension, with that bit fixed) and prunes them: the flip-flop keeps only the live low bits and consumers read `{const_high, live_low}`, leaving the high mux bits to fall out as dead LUTs during mapping. On uart_rx this took the register count from 54 to 24 flip-flops (matching yosys) and 352 to 124 LUT4; on the RIME image it removed ~130 flip-flops and enough LUTs to bring placement from over the ECP5U-25F's capacity (24,366 combinational cells, unplaceable) to within it.

### Performance
- **`merge_lut_chains` and `deduplicate_luts` are no longer quadratic.** Both decided whether a LUT's output was still referenced by rescanning the entire netlist per candidate — O(cells) per merge, quadratic per pass. On the RIME image (~22k LUTs) map+pack took ~600 s, dominated by 1.9 billion dict iterations. `merge_lut_chains` now retires each absorbed feeder from further consideration and does a single authoritative reference sweep at the end of the pass to delete the ones nothing reads (rather than rescanning per merge), and `deduplicate_luts` deletes replaced LUTs directly since the global reference rewrite already left them with zero consumers. RIME map+pack dropped from ~600 s to ~35 s with an identical netlist (21,770 LUT4, 0 undriven).

### Refactor
- Merged `slicepack_merge.py` into `slicepack.py`: the self-checking `merge_lut_chains` and reference-safe `deduplicate_luts` are now the only implementations. Removed the superseded broken chain merge, the never-called `absorb_buffers`, `merge_shared_input_luts`, `pack_pfumx`, and the disabled `_eliminate_tainted_luts`. `pack_slices` no longer reports hardcoded-zero stat keys, and deduplication now runs before dead-LUT elimination so its counts are meaningful.
- Removed `cutmap.py` (disabled in the pipeline; subsumed by IR-level `lutpack` and netlist-level chain merging).
- Removed `hierarchy.py`; the vendor primitive skip list `ECP5_BLACKBOX_NAMES` lives in `blackbox.py`.
- Removed disabled pipeline passes and their scaffolding: `collapse_case_chains`, `_simplify_mux_with_zero`, the no-op `simplify_constant_masks`, the uncalled `_narrow_eq_width`, and the hardcoded `cut_map`/`cdc_sync`/`timing_driven` stat keys.
- Removed dead mapper code: `_insert_dcca_buffers`, `_dead_cell_eliminate`, two disabled PMUX fast paths, a broken CCU2C orphan-bit loop that referenced an undefined name, and assorted no-op statements.
- Removed `analyze_timing_multi_clock` (returned the same global report per domain), `lut4_pin_delay`, `congestion.estimate_routing_metric` (redundant with `wirelength.estimate_routing`), and `incremental.CellMappingCache`/`build_cell_mapping_cache`.
- Frontend: removed an unreachable duplicate branch in assignment lowering, the vestigial `_unroll_for_loop`, and a dead pre-assignment of the FF reset net.
- CLI: the three report modes share one analysis pass, and `--ecppack` parses the captured nextpnr log instead of running place-and-route a second time. The nextpnr/ecppack subprocesses get the OSS CAD Suite `bin`/`lib` dirs on PATH (Windows DLL resolution, matching `run_nextpnr`).
- `emit_verilog` emits valid zero-extension Verilog for ZEXT cells.
- Docs updated to match: the mapper emits LUT4 cells (no dual-LUT slice packing claim), stage lists reflect the passes that actually run.

## 0.2.0 (2026-03-25)

### Critical Fixes
- **Comparison ops (LT/LE/GT/GE) now produce correct hardware.** Previously mapped to constant 0 (all comparisons were always false). Now implemented as bit-serial comparator chains with borrow propagation.
- **Signed comparison support.** Frontend records signedness from pyslang types. Evaluator, simulator, and techmap all respect `signed` parameter. MSB inversion in hardware for correct signed ordering.
- **Signed division and modulo.** SystemVerilog truncate-toward-zero semantics for signed DIV/MOD.
- **DIV/MOD without DSP warns explicitly** instead of silently producing constant 0.
- **Unsupported expressions emit SynthesisWarning** instead of silent constant 0.

### Performance
- **FastSimulator** (`nosis/sim.py`): pre-compiled flat-array evaluator replaces per-cycle topological sort and dict-based dispatch. Pipeline 2.2x faster (0.64s -> 0.29s on uart_tx). Function calls reduced 75%.
- Adaptive reqmerge cycle count based on FF chain depth analysis.

### Test Suite
- Consolidated 49 test files into 12 thematic suites. Eliminated redundant design parsing.
- 585 tests in 126s (was 622 in 159s — 37 redundant duplicates removed, 21% faster).
- Added: FastSimulator unit tests, comparison correctness tests, signed arithmetic tests, nextpnr integration tests.

### Quality
- 73 ruff lint errors fixed across the entire codebase. Zero remaining.
- Dead code removed: `_simulate_combinational`, `_eval_cell` from equiv.py.
- `py.typed` marker added. mypy runs in CI.
- All 44 source modules define `__all__`. 90% return type annotations.
- EHXPLLL: 12 missing PLL parameters added.
- 5 missing ECP5 primitive stubs added (PCSCLKDIV, DCSC, DQSCE, ECLKSYNCB, ECLKBRIDGECS).
- pnr_feedback: fallback regex patterns for nextpnr version variants.

## 0.1.0 (2026-03-25)

First public release. Full synthesis pipeline from SystemVerilog to ECP5 bitstream.

### Pipeline
- 19 optimization passes across 6 iterative rounds plus 6 post-optimization stages
- HoTT-inspired: quotient-type merging, HIT equivalence, encode-decode don't-care, cofiber dead-bit elimination, duality backward propagation
- SAT-based constant proof with full Tseitin CNF encoding via PySAT
- Reachable-state equivalence merging (500-cycle simulation)
- Cut-based LUT remapping at depth 5
- Register retiming (forward), CDC synchronizer insertion, high-fanout duplication
- Timing-driven extra optimization round on critical path
- Logarithmic barrel shifter for wide shifts (>8 bits)
- PMUX priority chain (replaces OR-reduce tree)

### Tech Mapping
- LUT4 cells with INIT binary parameter (nextpnr-compatible)
- TRELLIS_FF with CEMUX/CLKMUX/LSRMUX/REGSET
- CCU2C carry chains for ADD/SUB
- MULT18X18D with signedness tracking from SEXT
- ALU54B with accumulator feedback for MAC patterns
- DP16KD block RAM with readmemh initialization support
- TRELLIS_DPR16X4 distributed RAM
- BB bidirectional buffers for inout ports

### Verification
- 609 tests at initial release (see v0.2.0 for current numbers)
- Exhaustive truth table verification for small cones
- SAT-based equivalence checking (AND/OR/XOR/NOT/MUX/EQ/NE/ADD/SUB, wiring ops)
- Post-synthesis Verilog generation with behavioral cell models
- RTL-vs-post-synthesis simulation comparison via iverilog

### Hardware
- End-to-end verified: nosis -> nextpnr -> ecppack -> IcePi Zero flash install
- uart_tx: 379 MHz Fmax on ECP5-25F (46 FF, 32 CCU2C, LUT count varies by version)

### CLI
- `--stats`, `--benchmark`, `--json-stats` output modes
- `--ecppack` runs nextpnr + ecppack with `--device`, `--package`, `--lpf`
- `--check`, `--dump-ir`, `--emit-verilog`, `--snapshot`, `--delta`
