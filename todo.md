# Nosis — Open Items

1. Physical flash demonstration on IcePi Zero (bitstream verified through nextpnr + ecppack, board flash pending)
2. pyslang 10 API support (currently pinned to pyslang <10; version 10 replaced Driver with a different Compilation workflow)
3. Incremental re-mapping: delta computation works but incremental_remap falls through to full re-map
4. Post-synthesis simulation comparison: postsynth Verilog generation wired but iverilog compilation of simplified cell models may fail, falling back to RTL-vs-RTL
5. PMUX mutual exclusivity optimization: case branches are always exclusive but the mapper builds a general OR-reduce tree
6. Bundle full SoC sources for CI or make regression tests work with bundled subset
7. Property-based test for barrel shifter correctness across random widths and shift amounts
8. Tolerance bands for locked regression counts instead of exact equality
