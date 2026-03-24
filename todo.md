# Nosis — Cure List

1. Fix `eval_cell` CONCAT width tracking side effect — compute width info without modifying `cell.params`
2. Fix zero-width net guard — trace root cause in array/hierarchy lowering instead of clamping
3. Fix equivalence checker port detection — build port set once instead of iterating all cells per port
4. Remove constant-driven FFs — FF with constant D input replaced by the constant
5. Strip `$display`, `$monitor`, `$finish` during lowering — simulation-only constructs
6. Reject `real` and floating-point types explicitly during lowering
7. Warn on latches inferred from incomplete case/if in `always_comb`
8. Add `always @(*)` sensitivity list inference alongside `always_comb`
9. Add tri-state buffer inference from `inout` ports
10. Add memory port inference from read/write patterns in procedural blocks
11. Propagate `$readmemh` initialization data to DP16KD INITVAL parameters
12. Add DPR16X4 cell emission in tech mapper for small arrays tagged by inference
13. Verify carry chain INIT values against ECP5 carry chain specification
14. Add LUT4 sharing between independent signals occupying the same slice
15. Add multi-bit LUT packing — extend cascaded merge beyond 1-bit operations
16. Add PFUMX (LUT5) packing — combine two LUT4 outputs through the passthrough mux
17. Add L6MUX21 (LUT6) packing — combine two LUT5 outputs
18. Add Boolean optimization — factor `(a & b) | (a & c)` to `a & (b | c)`
19. Relax register retiming single-fanout constraint — allow retiming through multi-fanout with duplication
20. Add clock gating inference
21. Add constant-driven FF removal as an optimization pass
22. Create explicit wire cells for hierarchy port connections instead of driver pointer assignment
23. Refactor `_SubLowerer` out of nested class into a proper module-level class
24. Add full IR serialization/deserialization (not just hashes)
25. Add incremental tech mapping — re-map only changed cells using IR delta
26. Track cell output nets in incremental snapshots
27. Add CNF encoding for MUX, ADD, SUB, and comparison ops in SAT equivalence
28. Add sequential equivalence checking — unroll FFs for K cycles in SAT
29. Add SAT-based BMC with unrolled state instead of simulation approximation
30. Add logic cone extraction for targeted equivalence checking of individual outputs
31. Add Verilog netlist back-annotation from ECP5 cell models for post-synthesis simulation
32. Integrate Project Trellis ECP5 cell simulation models
33. Implement post-synthesis simulation comparison in validation harness
34. Add nextpnr JSON consumption test — verify nextpnr-ecp5 accepts the output
35. Add nextpnr placement test — verify the output places and routes successfully
36. Add automatic test vector generation from design port constraints
37. Add multi-cycle sequential coverage in testbench generator
38. Add routing delay estimation from cell count and fanout (wire-length model)
39. Replace assumed 12.5% toggle rate with per-net activity estimation from simulation
40. Replace congestion heuristic with physical routing metric using fanout + wire-length model
41. Add clock tree power estimation separate from FF dynamic power
42. Add SDC constraint parsing alongside LPF
43. Add `specify` block parsing for timing arc extraction
44. Add `(* synthesis off *)` / `(* synthesis on *)` pragma handling
45. Add `defparam` support
46. Add `assign` with delay stripping (remove `#N` from continuous assignments)
47. Handle `generate for` at IR level if slang doesn't fully unroll
48. Add multi-dimensional array support
49. Add packed struct support
50. Add interface support
51. Add multi-clock FF detection and warning
52. Add parameterized port widths to ECP5 primitive stubs
53. Add `library` and `config` construct handling
54. Increase hypothesis max_examples to 1000+ for property-based tests
55. Make regression test paths fully relative with no absolute default
56. Add generated API documentation via pdoc
57. Add inline examples to module docstrings
58. Document analysis passes in README (timing, congestion, power, constraints)
59. Add netlist area comparison test against yosys output for the same design
60. Reduce SoC LUT count from 45K toward 7K through items 14-18
