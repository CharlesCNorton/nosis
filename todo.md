# Nosis — Cure List

1. Add test that optimization never increases cell count on any design
2. Lock exact cell counts in regression tests instead of only lower bounds
3. Fix `eval_cell` CONCAT copy — use a per-call scratch dict instead of `dict(cell.params)` on every invocation
4. Strip `$display`/`$monitor`/`$finish` during lowering with explicit synthesis warning
5. Reject `real` and floating-point types with explicit error message during lowering
6. Add latch inference warning for incomplete case/if in `always_comb`
7. Add `assign` with delay stripping (remove `#N` from continuous assignments)
8. Add `defparam` support
9. Add `(* synthesis off/on *)` pragma handling
10. Handle `generate for` at IR level if slang doesn't fully unroll
11. Add `casez`/`casex` wildcard bit handling in equivalence checker
12. Propagate `$readmemh` initialization data to DP16KD INITVAL parameters
13. Add tri-state buffer inference from `inout` ports
14. Add memory port inference from read/write patterns in procedural blocks
15. Test DPR16X4 emission on real designs that produce DPR-tagged MEMORY cells
16. Add parameterized port widths to ECP5 primitive stubs in `ecp5_prims.sv`
17. Create explicit wire cells for hierarchy port connections instead of driver pointer assignment
18. Refactor `_SubLowerer` out of nested class into a proper module-level class
19. Split `frontend.py` into parse, lower, and hierarchy modules
20. Add formal equivalence check between pre-optimization and post-optimization IR
21. Add sequential equivalence checking — unroll FFs for K cycles in SAT
22. Add SAT-based BMC with unrolled state instead of simulation approximation
23. Expand SAT encoding to cover ADD, SUB, and multi-bit operations
24. Wire automatic test vector generation into the validation harness
25. Implement post-synthesis simulation comparison — compare RTL and post-synth Verilog outputs automatically
26. Add nextpnr JSON consumption and placement test — verify the output places and routes
27. Assert yosys comparison ratio bounds in regression tests
28. Add clock gating inference
29. Add clock tree power estimation separate from FF dynamic power
30. Replace assumed 12.5% toggle rate with per-net activity estimation from simulation
31. Replace congestion heuristic with physical routing metric using fanout + wire-length model
32. Add routing delay to timing analysis critical path using wire-length estimation
33. Add `specify` block parsing for timing arc extraction
34. Add SDC timing arcs into static timing analysis
35. Add incremental tech mapping — re-map only changed cells using IR delta
36. Relax register retiming single-fanout constraint — allow retiming through multi-fanout with duplication
37. Add multi-level Boolean factoring beyond single-level AND distribution
38. Relax PFUMX packing — match LUT4 pairs that share any 3 of 4 inputs, not just exact A0/B0
39. Add LUT4 sharing between independent signals occupying the same slice
40. Wire PFUMX and L6MUX21 packing into the CLI pipeline after tech mapping
41. Reduce SoC LUT count from 45K toward 7K through items 37-40
42. Add multi-dimensional array support
43. Add packed struct support
44. Add interface support
45. Add `library` and `config` construct handling
46. Forward reference tolerance in frontend instead of requiring source fixes
47. Generate HTML API documentation via pdoc and commit to repo
48. Add inline docstring examples to all remaining modules
49. Increase hypothesis max_examples to 5000+ for critical property tests
50. Lock regression test exact cell counts after LUT optimization stabilizes
