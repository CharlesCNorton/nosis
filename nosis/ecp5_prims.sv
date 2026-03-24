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
    parameter CLKOP_CPHASE  = 0,
    parameter CLKOS_CPHASE  = 0,
    parameter CLKOP_FPHASE  = 0,
    parameter CLKOS_FPHASE  = 0,
    parameter FEEDBK_PATH   = "CLKOP",
    parameter PLLRST_ENA    = "DISABLED",
    parameter STDBY_ENABLE  = "DISABLED"
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
