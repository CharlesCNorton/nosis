# Nosis — Open Items

1. End-to-end hardware demonstration on IcePi Zero (nextpnr + ecppack path verified, physical flash pending)
2. Timing-driven optimization: use critical path analysis to prioritize optimization of timing-critical logic
3. Register retiming: forward/backward FF movement is implemented but not in the default pipeline
4. CDC synchronizer insertion: detection works, insertion implemented, not in default pipeline
5. Full SAT-based equivalence for multi-bit MUX/PMUX/MEMORY operations
6. pyslang 10 API support (currently requires pyslang <10)
7. BRAM initialization from $readmemh/$readmemb in the synthesis pipeline
8. Multiport RAM: DP16KD dual-port read+write mapping
9. ALU54B accumulator feedback wiring for true MAC patterns
10. Deeper Boolean minimization beyond two-level AND/OR distribution
