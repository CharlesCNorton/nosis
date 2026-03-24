# Nosis

Correctness-first open-source FPGA synthesis targeting Lattice ECP5.

Nosis synthesizes SystemVerilog and Verilog into technology-mapped netlists for the ECP5 FPGA family. It prioritizes synthesis correctness over optimization — every output is verifiable against the input RTL through built-in equivalence checking.

## Status

Early development. The IR and CLI skeleton exist. Synthesis pipeline is not yet implemented.

## Architecture

```
  SystemVerilog / Verilog source
           |
     [1. Frontend]       — parse HDL, elaborate, produce IR
           |
     [2. Optimization]   — constant prop, dead code, Boolean simplification
           |
     [3. FSM handling]   — identify FSMs, preserve designer encoding
           |
     [4. Tech mapping]   — map IR primitives to ECP5 cells (LUT4, FF, BRAM, DSP)
           |
     [5. JSON output]    — nextpnr-compatible netlist
           |
     nextpnr-ecp5 → ecppack → bitstream
```

Nosis covers the synthesis step. Place-and-route (nextpnr) and bitstream packing (ecppack) handle the rest.

## Design Principles

1. **Correctness over optimization.** A correct but larger netlist beats an optimized but broken one. Every optimization pass preserves functional equivalence.

2. **Respect the RTL.** If the designer wrote a one-hot FSM, synthesize a one-hot FSM. Designer intent is preserved through synthesis — state encodings, explicit structure, and named signals survive into the netlist.

3. **Verify the output.** Built-in equivalence checking compares the output netlist against the input RTL via SAT solving. If the check fails, synthesis fails.

4. **ECP5 first.** Initial target is Lattice ECP5. Full primitive coverage:
   - **Logic:** TRELLIS_SLICE (2x LUT4 + 2x FF + carry), CCU2C (carry chain unit), PFUMX (LUT5 passthrough mux), L6MUX21 (LUT6 mux), TRELLIS_COMB, TRELLIS_FF, TRELLIS_DPR16X4 (distributed RAM)
   - **Block RAM:** DP16KD (true dual-port 16Kbit), PDPW16KD (pseudo dual-port wide), SP16KD (single-port), FIFO16KD (asynchronous FIFO)
   - **DSP:** MULT18X18D (18x18 signed/unsigned multiply), ALU54B (54-bit ALU with accumulator)
   - **PLL/Clock:** EHXPLLL (primary PLL), EHXPLLJ (JTAG-configurable PLL), CLKDIVF (clock divider), DCSC (dynamic clock stop), DQSCE (DQS clock enable), ECLKSYNCB (edge clock sync), ECLKBRIDGECS (edge clock bridge), OSCG (internal oscillator, 2.4-133 MHz), DCCA/DCC (dedicated clock buffers), PCSCLKDIV (PCS clock divider)
   - **I/O buffers:** TRELLIS_IO, BB/IB/OB/OBZ (bidirectional, input, output, tristate), BBPU/BBPD/IBPU/IBPD (with pull-up/pull-down), LVDS pairs
   - **I/O registers:** IFS1P3BX/IFS1P3DX (input FF), OFS1P3BX/OFS1P3DX (output FF)
   - **DDR I/O:** IDDRX1F/IDDRX2F (input DDR 1:1, 1:2), IDDR71B (input DDR 1:7), ODDRX1F/ODDRX2F (output DDR 1:1, 1:2), ODDR71B (output DDR 1:7), OSHX2A (output serializer), ISHX2A (input deserializer), TSHX2DQA/TSHX2DQSA (tristate DDR)
   - **I/O delay:** DELAYF (input programmable delay), DELAYG (output programmable delay), DQSBUFM (DQS buffer manager for DDR memory)
   - **System:** USRMCLK (user SPI flash clock), JTAGG (JTAG interface), GSR (global set/reset), SGSR (slice global set/reset), PUR (power-up reset), DTR (die temperature readout), SEDGA (soft error detection), START (startup sequence control), TSALL (tristate all outputs), EXTREFB (external reference clock buffer)
   - **SerDes:** DCUA (dual-channel universal transceiver, 5G variants)
   - **Configuration:** BCINRD, sysCONFIG interface primitives

   Additional FPGA families are planned.

5. **Pure Python first pass.** Correctness is easier to verify in Python than C++. Performance optimization comes after the pipeline is proven correct.

## Install

```
pip install -e ".[dev]"
```

## Usage

```
nosis input.sv --top top --target ecp5 -o output.json
```

The output JSON is a nextpnr-compatible netlist.

## Development

```
pytest tests/ -v
ruff check .
```

## License

MIT.
