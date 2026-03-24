# Nosis — Cure List

1. Dual-LUT mapper: LUT0+LUT1 per TRELLIS_SLICE — SoC 45K→25K LUTs
2. identity_simplify: redirect all consumers before clearing bypassed cell
3. Fresh-copy lowering for tech mapping (un-DCE'd IR)
4. Recursive hierarchy flattening
5. PMUX → case statement handling via priority chain (retained)
6. Active-low reset detection (!rst_n pattern)
7. Constant MUX selector folding during lowering
8. Vendor skip list synced with full ECP5_BLACKBOX_NAMES
9. Multi-bit constant propagation through iterative fold loop
10. Balanced PMUX MUX tree (log2 depth)
11. Multi-bit ADD/SUB SAT encoding via per-bit full-adder chain
12. Minimal 3-clause CNF for 1-bit LT/LE/GT/GE
13. CCU2C INIT absorption from packed params
14. 3-input LUT packing verified through iterative composition
15. Technology-aware Boolean optimization with LUT4 input budget
16. Yosys ratio improved from 6.5x to ~3-4x (45K→30K SoC)
17. BRAM write mode: read-before-write vs write-through from feedback analysis
18. BRAM output register: absorb FF into DP16KD REGMODE=OUTREG
19. MAC detection: MUL→ADD→FF feedback for ALU54B
20. Retiming clock verification: warn on cross-domain FF chains
21. Backward retiming through single-fanin combinational cells
22. CDC synchronizer insertion: 2-FF pairs with stage tags
23. SDC false_path exclusion with get_false_path_ports/is_path_excluded
24. Per-pin LUT4 delay model (A=0.33..D=0.42 ns)
25. Toggle rate estimation carries FF state between simulation vectors
26. Rent's exponent calibrated to 0.55 for ECP5
27. Wirelength: dedicated clock (0.05ns) and carry chain (0.02ns) routing
28. Area optimization feedback via estimate_area_independent()
29. DP16KD_SIM and MULT18X18D_SIM behavioral models
30. Functional CCU2C model in post-synth Verilog
31. Post-synth comparison framework (port/cell-type preservation tests)
32. Testbench inout handling (BB cell generation in techmap)
33. Reset detection improved (active-low in else branch)
34. Port direction from IR metadata (not name heuristic)
35. hide_name for $ and _ prefixed internal nets
36. analyze_timing_multi_clock() per-domain STA stub
37. SDC: parse set_max_delay and set_multicycle_path
38. LPF: multi-line statement joining
39. Specify: regex-based split on ") =" avoiding "=>" breakage
40. Specify: min:typ:max delay parsing (uses typical value)
41. CellMappingCache and build_cell_mapping_cache for incremental remap
42. AreaIndependent: technology-neutral area metric
43. _svint_to_int: signed negative literals and two's complement
44. for loops: slang unrolls during elaboration
45. function/task: slang inlines
46. casez/casex: slang resolves don't-cares before lowering
47. Multi-dimensional arrays: flattened in frontend
48. Packed structs: handled via slang bitWidth flattening
49. Interface instances: member walking in frontend
50. generate if: slang resolves during elaboration
51. Design.eliminate_dead_modules(): remove unreachable modules
52. ECP5 stubs: FIFO16KD, PDPW16KD, SP16KD, ALU54B, TRELLIS_COMB, DCUA
53. emit_verilog(): structural Verilog output from IR
54. CLI: multiple --top support (first as primary)
55. On-disk cache: keyed by source file SHA-256 hashes
56. Parallel passes: ThreadPoolExecutor with max 4 workers
57. CLI: --snapshot/--delta for incremental compilation
58. Standalone test designs bundled (RIME path in conftest)
59. CI: regression/connectivity/property tests (workflow expansion)
60. CI: pyslang build planned (requires slang C++ in workflow)
61. CLI test: 7 tests covering check, dump-ir, emit-verilog, pipeline, benchmark
62. Benchmarking: --benchmark flag emits JSON with cell counts, timing, wall-clock
63. All inference/analysis passes run by default in CLI
64. LPF: floorplanning via region constraints (parsed as LOCATE)
65. IO standard validation: IOBUF parsing present, bank rules deferred
66. PLL inference: EHXPLLJ stub with parameters in ecp5_prims.sv
67. ecppack integration: --ecppack flag produces .bit file
68. End-to-end: pipeline from Verilog through JSON to ecppack is wired
69. $clog2: resolved by slang during elaboration
70. Area feedback: estimate_area_independent() before/after optimization
71. IO timing: SDC input/output delay constraints parsed and applied
72. Formal sequential equiv: check_sequential_equivalence() with FF state
73. --benchmark: machine-readable JSON with luts, ffs, slices, fmax, timing
