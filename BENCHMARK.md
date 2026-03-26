# Nosis Benchmark Results

All designs from the [RIME](https://github.com/CharlesCNorton/rime) project (Resident IcePi Management Environment). Target: Lattice ECP5-25F CABGA256, speed grade 6. Post-optimization, post-slice-packing. Measured 2026-03-26.

## Individual Modules

| Design | Files | LUT4 | FF | CCU2C | BRAM | Slices | Logic Fmax | Synth |
|--------|-------|------|----|-------|------|--------|------------|-------|
| uart_tx | 1 | 51 | 46 | 64 | 0 | 64 | 354.6 MHz | <0.1s |
| uart_rx | 1 | 113 | 46 | 64 | 0 | 64 | 352.3 MHz | <0.1s |
| sdram_bridge | 1 | 34 | 332 | 14 | 0 | 166 | 340.9 MHz | 0.1s |
| sdram_controller | 1 | 225 | 174 | 34 | 0 | 113 | 93.7 MHz | 0.3s |
| rime_pcpi_crc32 | 1 | 33 | 34 | 0 | 0 | 17 | 1046.2 MHz | <0.1s |

## CPU Cores (standalone, no SoC)

| Core | ISA | LUT4 | FF | CCU2C | Slices | Logic Fmax | Synth |
|------|-----|------|----|-------|--------|------------|-------|
| RIME-V | RV32IMC+Zbb+Zicond+CRC32 | 6,009 | 1,715 | 227 | 3,005 | 51.1 MHz | 2.5s |
| PicoRV32 | RV32IMC | 4,064 | 1,462 | 79 | 2,032 | 61.9 MHz | 3.3s |

Standalone, PicoRV32 is smaller. In a SoC, RIME-V's Harvard instruction port and tighter state machine reverse the area relationship (see below).

## RIME Images (multi-file hierarchy)

| Image | Files | LUT4 | FF | CCU2C | BRAM | Slices | ECP5 Util | Logic Fmax | Synth |
|-------|-------|------|----|-------|------|--------|-----------|------------|-------|
| Thaw | 7 | 4,656 | 3,785 | 590 | 0 | 2,328 | 19% | 69.6 MHz | 10.7s |
| Frost | 10 | 5,212 | 5,925 | 938 | 4 | 2,963 | 24% | 44.2 MHz | 36.0s |
| Slush | 10 | 10,052 | 20,592 | 770 | 0 | 10,296 | 84% | 44.5 MHz | 50.3s |
| Ember | 4 | 1,365 | 1,847 | 600 | 2 | 924 | 7% | 61.2 MHz | 104.9s |

## SoC Comparison (RIME-V vs PicoRV32, identical shell)

Identical test conditions: same minimal SoC (16 KB BRAM + UART), same FPGA, same synthesizer (nosis), same PnR (nextpnr-ecp5 speed grade 6).

### Area

| Metric | RIME-V SoC | PicoRV32 SoC | RIME-V advantage |
|--------|-----------|-------------|-----------------|
| LUT4 | 4,001 | 9,957 | **2.49x smaller** |
| TRELLIS_FF | 2,176 | 5,708 | **2.62x smaller** |
| CCU2C | 483 | 1,214 | **2.51x smaller** |
| TRELLIS_COMB (post-PnR) | 5,091 | 12,670 | **2.49x smaller** |
| Slices | 2,001 | 4,979 | **2.49x smaller** |
| ECP5-25F utilization | 20% | 52% | |

### Timing (post-route, nextpnr)

| Metric | RIME-V SoC | PicoRV32 SoC |
|--------|-----------|-------------|
| Fmax (sys_clk) | **41.40 MHz** | 30.64 MHz |
| Logic-only Fmax | 82.9 MHz | 56.8 MHz |
| Synthesis time | **3.7s** | 32.7s |

### Throughput

| Metric | RIME-V SoC | PicoRV32 SoC |
|--------|-----------|-------------|
| Fmax | 41.40 MHz | 30.64 MHz |
| Average CPI (estimated) | 6.0 | 6.1 |
| MIPS | **6.96** | 5.02 |
| Throughput ratio | **1.39x** | 1.00x |

### Architecture

| Feature | RIME-V | PicoRV32 |
|---------|--------|----------|
| ISA | RV32IMC + Zbb + Zicond + CRC32 | RV32IMC |
| Memory | Harvard (separate imem) | Von Neumann (shared bus) |
| Multiply | Iterative (~10 cycles) | Iterative (~32 cycles) |
| Divide | 1 bit/cycle (32 cycles) | 1 bit/cycle (32 cycles) |
| Compressed ISA | +1 cycle (expand stage) | Inline (+0 cycles) |
| Custom instructions | PCPI CRC32 | PCPI interface |

### Optimization Effectiveness

| Design | IR cells (pre) | IR cells (post) | Reduction |
|--------|---------------|----------------|-----------|
| uart_tx | 96 | 60 | 37.5% |
| uart_rx | 104 | 60 | 42.3% |
| sdram_bridge | 130 | 101 | 22.3% |
| sdram_controller | 357 | 264 | 26.1% |
| rime_v | 1,696 | 924 | 45.5% |
| picorv32 | 4,061 | 1,206 | 70.3% |
| thaw | 5,101 | 2,785 | 45.4% |
| frost | 7,799 | 3,879 | 50.3% |
| slush | 7,744 | 3,672 | 52.6% |
| ember | 39,222 | 926 | 97.6% |
| rime_v_soc | 3,661 | 1,038 | 71.6% |
| picorv32_soc | 9,271 | 3,300 | 64.4% |

## Hardware Verification

The RIME-V SoC bitstream (synthesized by nosis, placed by nextpnr, packed by ecppack) was flashed to an IcePi Zero board via the RIME UART service protocol and verified on silicon:

- **VERSION**: phase=5 version=1 (service mode, correct)
- **PING**: ACK (0xAC) received
- **JEDEC**: 0xEF 0x40 0x18 (Winbond W25Q128, correct flash chip)

The PicoRV32 CPU in the same SoC design was the previous default. yosys produced functional hardware from PicoRV32 but non-functional hardware from RIME-V. nosis produces functional hardware from both.

## Methodology

- Synthesizer: nosis 0.1.0 (pure Python, pyslang 7.0.33 frontend)
- Place and route: nextpnr-ecp5 (OSS CAD Suite)
- Bitstream: ecppack (OSS CAD Suite)
- Target: Lattice ECP5U-25F, CABGA256 package, speed grade 6
- Board: IcePi Zero
- Logic Fmax: nosis static timing analysis (cell-level, no routing delay)
- Post-route Fmax: nextpnr timing report
- CPI estimates: architectural analysis of RTL state machines
- All numbers are reproducible from the RIME and nosis source trees
