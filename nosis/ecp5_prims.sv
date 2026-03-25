`timescale 1ns/1ps
// ECP5 vendor primitive stubs for slang elaboration.
// These are black-box declarations that allow the frontend to parse
// designs that instantiate ECP5-specific primitives. The actual
// implementation is in the FPGA fabric and does not need behavioral
// modeling for synthesis.

module USRMCLK (
    input  wire USRMCLKI,
    input  wire USRMCLKTS
);
endmodule

module GSR (
    input wire GSR
);
endmodule

module SGSR (
    input wire GSR, CLK
);
endmodule

module PUR (
    input wire PUR
);
endmodule

module JTAGG (
    output wire JTCK,
    output wire JTDI,
    output wire JTMS,
    output wire JTDO1,
    output wire JTDO2,
    output wire JSHIFT,
    output wire JUPDATE,
    output wire JRSTN,
    output wire JCE1,
    output wire JCE2,
    output wire JRTI1,
    output wire JRTI2,
    input  wire JTDO
);
endmodule

module DTR (
    output wire [7:0] DTROUT
);
endmodule

module OSCG #(
    parameter DIV = 128
) (
    output wire OSC
);
endmodule

module EHXPLLL #(
    parameter CLKI_DIV       = 1,
    parameter CLKFB_DIV      = 1,
    parameter CLKOP_DIV      = 1,
    parameter CLKOS_DIV      = 1,
    parameter CLKOS2_DIV     = 1,
    parameter CLKOS3_DIV     = 1,
    parameter CLKOP_ENABLE   = "ENABLED",
    parameter CLKOS_ENABLE   = "DISABLED",
    parameter CLKOS2_ENABLE  = "DISABLED",
    parameter CLKOS3_ENABLE  = "DISABLED",
    parameter CLKOP_CPHASE   = 0,
    parameter CLKOS_CPHASE   = 0,
    parameter CLKOS2_CPHASE  = 0,
    parameter CLKOS3_CPHASE  = 0,
    parameter CLKOP_FPHASE   = 0,
    parameter CLKOS_FPHASE   = 0,
    parameter CLKOS2_FPHASE  = 0,
    parameter CLKOS3_FPHASE  = 0,
    parameter CLKOP_TRIM_POL = "RISING",
    parameter CLKOP_TRIM_DELAY = 0,
    parameter CLKOS_TRIM_POL = "RISING",
    parameter CLKOS_TRIM_DELAY = 0,
    parameter FEEDBK_PATH    = "CLKOP",
    parameter PLLRST_ENA     = "DISABLED",
    parameter STDBY_ENABLE   = "DISABLED",
    parameter OUTDIVIDER_MUXA = "DIVA",
    parameter OUTDIVIDER_MUXB = "DIVB",
    parameter OUTDIVIDER_MUXC = "DIVC",
    parameter OUTDIVIDER_MUXD = "DIVD",
    parameter DPHASE_SOURCE   = "DISABLED",
    parameter INTFB_WAKE      = "DISABLED"
) (
    input  wire CLKI,
    input  wire CLKFB,
    input  wire RST,
    input  wire STDBY,
    input  wire PHASESEL0,
    input  wire PHASESEL1,
    input  wire PHASEDIR,
    input  wire PHASESTEP,
    input  wire PHASELOADREG,
    output wire CLKOP,
    output wire CLKOS,
    output wire CLKOS2,
    output wire CLKOS3,
    output wire LOCK,
    output wire INTLOCK
);
endmodule

module CLKDIVF #(
    parameter DIV      = "2.0",
    parameter GSR      = "DISABLED"
) (
    input  wire CLKI,
    input  wire RST,
    input  wire ALIGNWD,
    output wire CDIVX
);
endmodule

module DCCA (
    input  wire CLKI,
    input  wire CE,
    output wire CLKO
);
endmodule

module SEDGA (
    output wire SEDSTDBY,
    output wire SEDENABLE,
    output wire SEDSTART,
    output wire SEDDONE,
    output wire SEDINPROG,
    output wire SEDERR
);
endmodule

module EXTREFB (
    input  wire REFCLKP,
    input  wire REFCLKN,
    output wire REFCLKO
);
endmodule

module TSALL (
    input wire TSALL
);
endmodule

// Additional primitives with parameterized port widths

module EHXPLLJ #(
    parameter CLKI_DIV      = 1,
    parameter CLKFB_DIV     = 1,
    parameter CLKOP_DIV     = 1,
    parameter CLKOS_DIV     = 1,
    parameter CLKOS2_DIV    = 1,
    parameter CLKOS3_DIV    = 1,
    parameter CLKOP_ENABLE  = "ENABLED",
    parameter CLKOS_ENABLE  = "DISABLED",
    parameter CLKOS2_ENABLE = "DISABLED",
    parameter CLKOS3_ENABLE = "DISABLED",
    parameter FEEDBK_PATH   = "CLKOP"
) (
    input  wire CLKI,
    input  wire CLKFB,
    input  wire RST,
    input  wire STDBY,
    input  wire PHASESEL0,
    input  wire PHASESEL1,
    input  wire PHASEDIR,
    input  wire PHASESTEP,
    input  wire PHASELOADREG,
    output wire CLKOP,
    output wire CLKOS,
    output wire CLKOS2,
    output wire CLKOS3,
    output wire LOCK,
    output wire INTLOCK
);
endmodule

module MULT18X18D #(
    parameter REG_INPUTA_CLK  = "NONE",
    parameter REG_INPUTB_CLK  = "NONE",
    parameter REG_OUTPUT_CLK  = "NONE",
    parameter SOURCEB_MODE    = "B_INPUT",
    parameter GSR             = "DISABLED"
) (
    input  wire [17:0] A,
    input  wire [17:0] B,
    input  wire CLK0, CLK1, CLK2, CLK3,
    input  wire CE0, CE1, CE2, CE3,
    input  wire RST0, RST1, RST2, RST3,
    input  wire SIGNEDA, SIGNEDB,
    output wire [35:0] P
);
endmodule

module DP16KD #(
    parameter DATA_WIDTH_A  = 18,
    parameter DATA_WIDTH_B  = 18,
    parameter REGMODE_A     = "NOREG",
    parameter REGMODE_B     = "NOREG",
    parameter CSDECODE_A    = "0b000",
    parameter CSDECODE_B    = "0b000",
    parameter WRITEMODE_A   = "NORMAL",
    parameter WRITEMODE_B   = "NORMAL",
    parameter GSR           = "DISABLED"
) (
    input  wire [13:0] ADA, ADB,
    input  wire [17:0] DIA, DIB,
    output wire [17:0] DOA, DOB,
    input  wire CLKA, CLKB,
    input  wire WEA, WEB,
    input  wire CEA, CEB,
    input  wire OCEA, OCEB,
    input  wire RSTA, RSTB,
    input  wire CSA0, CSA1, CSA2,
    input  wire CSB0, CSB1, CSB2
);
endmodule

module BB (
    input  wire I,
    input  wire T,
    output wire O,
    inout  wire B
);
endmodule

module OBZ (
    input  wire I,
    input  wire T,
    output wire O
);
endmodule

module TRELLIS_DPR16X4 (
    input  wire [3:0] RAD,
    input  wire [3:0] WAD,
    input  wire [3:0] DI,
    output wire [3:0] DO,
    input  wire WCK,
    input  wire WRE
);
endmodule

module CCU2C #(
    parameter [15:0] INIT0     = 16'h0000,
    parameter [15:0] INIT1     = 16'h0000,
    parameter INJECT1_0        = "YES",
    parameter INJECT1_1        = "YES"
) (
    input  wire CIN,
    input  wire A0, B0, C0, D0,
    input  wire A1, B1, C1, D1,
    output wire S0, S1, COUT
);
endmodule

module PCSCLKDIV (
    input  wire CLKI,
    input  wire RST,
    input  wire SEL2, SEL1, SEL0,
    output wire CDIV1, CDIVX
);
endmodule

module DCSC (
    input  wire CLK0, CLK1,
    input  wire SEL0, SEL1,
    input  wire MODESEL,
    output wire DCSOUT
);
endmodule

module DQSCE (
    input  wire CLK, DQSW, CE,
    output wire DQSW270
);
endmodule

module ECLKSYNCB (
    input  wire ECLKI, STOP,
    output wire ECLKO
);
endmodule

module ECLKBRIDGECS (
    input  wire CLK0, CLK1, SEL,
    output wire ECSOUT
);
endmodule
