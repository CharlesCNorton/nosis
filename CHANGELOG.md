# Changelog

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
- 609 tests: unit, integration, property-based (Hypothesis), regression, structural
- Exhaustive truth table verification for small cones
- SAT-based equivalence checking (AND/OR/XOR/NOT/MUX/EQ/NE/ADD/SUB, wiring ops)
- Post-synthesis Verilog generation with behavioral cell models
- RTL-vs-post-synthesis simulation comparison via iverilog

### Hardware
- End-to-end verified: nosis -> nextpnr -> ecppack -> IcePi Zero flash install
- uart_tx: 379 MHz Fmax on ECP5-25F (16 LUT4, 46 FF, 32 CCU2C)

### CLI
- `--stats`, `--benchmark`, `--json-stats` output modes
- `--ecppack` runs nextpnr + ecppack with `--device`, `--package`, `--lpf`
- `--check`, `--dump-ir`, `--emit-verilog`, `--snapshot`, `--delta`
