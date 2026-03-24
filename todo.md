# Nosis — Cure List

1. Compute single LUT4 truth tables for narrow case statements (2-4 cases on 1-2 bit selectors) instead of emitting MUX chains
2. Simplify LUT4 cells with tied-constant inputs by reducing the truth table and freeing the unused input pin
3. Scan for adjacent logic absorbable into CCU2C INIT values instead of only reading the packed_lut_init param
4. Remove the absorbed FF from the netlist when BRAM output register inference tags a DP16KD for REGMODE=OUTREG
5. Emit ALU54B cells from the tech mapper when a MUL cell is tagged with dsp_mac=True instead of mapping to MULT18X18D + separate ADD
6. Call verify_retime_clocks before and after retime_forward and retime_backward, refuse to move FFs that would cross clock domains
7. Handle two-variable-input cells in backward retiming by duplicating the FF for each input path
8. Scope CDC synchronizer rewiring to the destination domain by tracing from the crossing net through the dest-domain logic cone instead of rewiring the first consumer found
9. Fix the fresh-copy lowering workaround by making identity_simplify preserve output net identity — redirect consumers AND keep the output net's driver chain intact so DCE does not orphan live logic
10. Replace driver pointer assignment at hierarchy port boundaries with explicit buffer cells that DCE and tech mapping can reason about
11. Encode multi-bit shifts and multi-bit comparisons in the SAT equivalence encoder via per-bit unrolling
12. Compute per-domain critical paths in analyze_timing_multi_clock by extracting sub-modules per clock domain instead of returning the global report
13. Integrate the per-pin LUT4 delay model into the STA arrival time computation — use the pin assignment from the tech mapper to select A/B/C/D delay
14. Model all 6 DP16KD configurations in the behavioral simulation model instead of the simplified 1K-entry version
15. Add input/output register and signed/unsigned mode to the MULT18X18D behavioral simulation model
16. Wire actual post-synthesis simulation comparison into the validation harness — generate post-synth Verilog, compile both RTL and post-synth with iverilog, compare cycle-by-cycle output
17. Generate testbench drive logic for bidirectional (inout) ports using the BB cell model
18. Detect reset ports by driver-cone analysis instead of name matching in the testbench generator
19. Classify ECP5 cell port directions from the techmap cell definition instead of the name-based heuristic in the JSON backend
20. Apply set_max_delay bounds in the STA critical path computation — clamp path delays to the constraint value
21. Store and apply set_multicycle_path multipliers — scale the clock period for affected paths
22. Fix LPF multi-line joining to handle lines ending without semicolons by looking ahead for continuation keywords instead of checking trailing punctuation
23. Add a min/max selector parameter to the specify parser so callers can choose which delay value from min:typ:max triples
24. Replace the name-prefix heuristic in CellMappingCache with an explicit mapping recorded during tech mapping
25. Make incremental_remap copy unchanged cells from the previous netlist using the cache instead of doing a full re-map and then invalidating
26. Wire OnDiskCache into the CLI behind a --cache-dir flag so incremental compilation persists across runs
27. Wire run_passes_parallel into run_default_passes for independent pass pairs (constant_fold + identity_simplify can run concurrently on disjoint cell sets)
28. Bundle standalone test designs (small .sv files exercising each PrimOp) with the repo so the test suite does not require RIME source files
29. Add regression, connectivity, and property tests to the CI workflow alongside the existing unit tests
30. Add a CI job that builds slang from source, installs pyslang, and runs test_frontend.py, test_regression.py, and test_connectivity.py
31. Extend multi-dimensional array flattening to handle 3+ dimensions by recursive elementType descent
32. Track packed struct field-level access by recording field offset and width metadata on SLICE cells generated from struct member references
33. Wire interface modport directions into port connection logic so input-only modport members are not driven from the sub-instance side
34. Set the module_ref param on cells created by _lower_sub_instance so Design.eliminate_dead_modules can trace module dependencies
35. Add emit_verilog coverage for MUL, DIV, MOD, SHL, SHR, SSHR, REDUCE_AND, REDUCE_OR, REDUCE_XOR, EQ, NE, LT, LE, GT, GE, CONCAT, SLICE, ZEXT, SEXT, REPEAT, PMUX, LATCH, and MEMORY
36. Accept multiple --top values in the CLI by changing the argument to action="append" and iterating over all specified tops
37. Search OSS CAD Suite paths for ecppack in the CLI --ecppack handler instead of calling the bare command name
38. Guard _svint_to_int signed two's complement against None width from unsized literals
39. Add a test for emit_verilog that verifies the output parses as valid Verilog for a module with FFs, MUXes, and arithmetic
40. Add a test for Design.eliminate_dead_modules with a multi-module design where one module is unreachable
41. Add a test for _svint_to_int with negative decimal, signed hex, and signed binary inputs
42. Add a test for OnDiskCache store/lookup/invalidate roundtrip
43. Add a test for run_passes_parallel returning correct per-pass cell counts
44. Add a test for the specify parser with min:typ:max delay triples verifying the typical value is selected
45. Implement LPF BLOCK/REGION constraint parsing for floorplanning instead of claiming it is handled by LOCATE
46. Validate parsed IOBUF IO_TYPE values against a table of ECP5-legal standards per bank voltage
47. Add PLL frequency-to-divider computation that calculates CLKI_DIV, CLKFB_DIV, and CLKOP_DIV from a target frequency and input clock
48. Preserve $clog2 result as a named CONST in the IR with the original expression as a cell attribute for downstream width inference
49. Wire area feedback into the optimization pipeline — re-run boolean_optimize when estimate_area_independent shows an increase, iterate until stable
50. Replace simulation-based check_sequential_equivalence with SAT-based BMC using K-cycle unrolled transition relation
51. Add per-stage timing (parse, lower, opt, infer, map, emit) to the --benchmark JSON output
52. Synthesize uart_tx through Nosis, place-and-route with nextpnr, pack with ecppack, load onto IcePi Zero, and verify functional UART output on the wire
53. Drive yosys comparison ratio below 2x on uart_tx and rime_v through items 1-3 and 5
54. Update ARCHITECTURE.md module inventory with current line counts and module descriptions
55. Update README analysis pass list to match the passes actually run by the CLI
