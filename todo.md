# Nosis — Cure List

1. Integrate dual-LUT4 packing into `_map_lut` so the mapper emits TRELLIS_SLICE with both LUT0 and LUT1 populated during mapping instead of as a post-pass
2. Fix `identity_simplify` driver chain breakage — bypass must redirect all consumers of the output net to the source net before clearing the cell
3. Add a fresh-copy lowering path so the CLI tech-maps an unoptimized IR copy while still reporting optimized IR statistics separately
4. Flatten nested hierarchy recursively in `_lower_sub_instance` instead of the single-level no-op fallback
5. Extend `_collect_nb_assignments` to emit PMUX cells for case statements instead of linear MUX chains
6. Detect active-low reset patterns (`if (!rst_n)`) in the true branch of procedural blocks alongside the existing active-high detection
7. Propagate constant MUX selectors during lowering — fold `MUX(const, a, b)` immediately instead of deferring to `identity_simplify`
8. Synchronize the vendor-skip list in `_lower_sub_instance` with the full 48-name `blackbox.py` registry
9. Add multi-bit constant propagation through CONCAT/SLICE chains in `constant_fold`
10. Convert PMUX tech mapping from a linear priority chain to a balanced MUX tree (log2(N) depth instead of N)
11. Encode multi-bit ADD, SUB, shift, and comparison operations in the SAT equivalence encoder via per-bit unrolling
12. Remove redundant clauses from the SAT LE/GE encodings
13. Absorb adjacent logic into CCU2C carry chain LUT INIT values instead of hardcoding XOR/XNOR
14. Add 3-input LUT packing in `lutpack.py` — compose three cascaded 2-input operations into a single LUT4
15. Add technology-aware Boolean optimization that evaluates LUT4 truth table capacity before deciding to merge or split
16. Drive yosys comparison ratio below 2x on uart_tx, rime_v, and the full SoC — the current bound is 20x; close the gap through items 1, 5, 10, 13-15
17. Detect read-before-write vs write-before-read ordering in BRAM inference and set DP16KD WRITEMODE accordingly
18. Infer BRAM output registers when an FF is directly connected to the DP16KD read data port — set REGMODE to REG
19. Detect multiply-accumulate patterns and map to ALU54B instead of MULT18X18D + ADD
20. Verify that retimed FFs share the same clock net as the surrounding logic before moving
21. Add register balancing across pipeline stages — retime backward as well as forward to equalize path delays
22. Insert synchronizer cell pairs at detected clock domain crossings
23. Apply SDC `set_false_path` constraints to exclude paths from static timing analysis
24. Model per-pin delay differences within LUT4 cells (A input is faster than D input on ECP5)
25. Carry FF state between simulation vectors in `estimate_toggle_rates` for sequential designs
26. Calibrate the Rent's rule exponent in `estimate_routing_metric` against actual ECP5 place-and-route data
27. Account for dedicated clock routing, carry chain routing, and BRAM column placement in wirelength estimation
28. Add area optimization feedback loop — iterate between optimization passes and area measurement until convergence
29. Add behavioral simulation models for DP16KD and MULT18X18D in `postsynth.py`
30. Replace the CCU2C comment placeholder in post-synth Verilog with a functional instantiation
31. Wire actual post-synthesis simulation comparison into the validation harness instead of comparing RTL output against itself
32. Handle bidirectional (inout) ports in the testbench generator
33. Detect non-standard reset port names in the testbench generator using driver-cone analysis instead of name matching
34. Classify JSON port directions from the IR port metadata instead of the name-based heuristic
35. Set `hide_name` correctly for all internal nets in the JSON backend
36. Add multi-clock static timing analysis — compute critical paths per clock domain
37. Parse `set_max_delay` and `set_multicycle_path` command bodies in the SDC parser
38. Handle multi-line statements and escaped quotes in the LPF parser
39. Fix the specify block parser to handle the `=>` path operator without splitting on `=`
40. Parse min:typ:max delay triples in specify blocks
41. Implement the cell-level mapping cache in `incremental_remap` so small deltas skip full re-mapping
42. Add a technology-independent area metric for architecture-neutral optimization decisions
43. Handle signed negative literals in `_svint_to_int`
44. Lower `for` loops inside `always_comb` blocks by unrolling at the IR level
45. Lower `function` and `task` bodies that slang does not fully inline
46. Propagate `casez`/`casex` don't-care bits through the IR as mask metadata on EQ cells
47. Extend multi-dimensional array flattening to handle 3+ dimensions
48. Track packed struct field-level access for partial-width read optimization
49. Wire interface modport directions into the port connection logic
50. Add `generate if` handling at the IR level as a fallback for conditional elaboration
51. Add dead module elimination — remove unused modules from multi-module Design objects
52. Add stubs for FIFO16KD, PDPW16KD, SP16KD, ALU54B, TRELLIS_COMB, and DCUA to `ecp5_prims.sv`
53. Add Verilog text output for the IR (not just JSON and post-synth behavioral)
54. Support multiple top modules and partial elaboration in the CLI
55. Add on-disk incremental compilation cache keyed by source file hashes
56. Parallelize independent optimization passes across multiple threads
57. Wire snapshot/delta infrastructure into the CLI for automatic incremental re-synthesis
58. Bundle standalone test designs with the repo so the test suite does not require RIME source files
59. Add regression tests, connectivity tests, and property tests to the CI workflow
60. Add pyslang build and frontend tests to CI — build slang from source in the workflow, run test_frontend.py, test_regression.py, and test_connectivity.py against real HDL
61. Add a test for the CLI entry point (`nosis.cli:main`) covering parse, check, dump-ir, and full-pipeline modes
62. Add benchmarking infrastructure to track synthesis time and cell count regressions per commit
63. Run all 10 documented analysis passes by default in the CLI instead of only 6
64. Add floorplanning region constraint support in the LPF parser
65. Validate IO standards against ECP5 bank voltage rules after LPF parsing
66. Add PLL configuration inference from target frequency constraints
67. Add bitstream-level output via ecppack integration so the pipeline can produce a .bit file directly
68. End-to-end hardware demonstration — synthesize uart_tx through Nosis, place-and-route with nextpnr, pack with ecppack, load onto IcePi Zero, and verify functional UART output on the wire
69. Add `$clog2` width inference awareness so computed widths survive into the IR as named constants
70. Add area feedback to the optimization pipeline — re-run Boolean optimization when area increases
71. Add IO timing validation — check that constrained input/output delays are achievable given the critical path
72. Add formal sequential equivalence via SAT with unrolled transition relation (not simulation approximation)
73. Add a `--benchmark` flag to the CLI that emits machine-readable JSON with cell counts, timing, and wall-clock time per stage
