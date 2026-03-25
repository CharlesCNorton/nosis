"""Nosis post-synthesis simulation — generate Verilog from ECP5 netlist.

Converts the mapped ECP5Netlist back to behavioral Verilog for
simulation comparison against the original RTL. Each ECP5 cell type
is modeled with a behavioral Verilog module.

The generated Verilog can be compiled with iverilog alongside the
ECP5 cell simulation models and compared against the original RTL
simulation output.
"""

from __future__ import annotations

from nosis.techmap import ECP5Netlist

__all__ = [
    "generate_cell_models",
    "generate_postsynth_verilog",
]


# Behavioral Verilog models for ECP5 cells
_CELL_MODELS = """
// ECP5 behavioral simulation models for nosis post-synthesis verification

module LUT4_SIM #(
    parameter [15:0] INIT = 16'h0000
) (
    input A, B, C, D,
    output Z
);
    wire [3:0] idx = {D, C, B, A};
    assign Z = INIT[idx];
endmodule

module TRELLIS_FF_SIM #(
    parameter GSR = "DISABLED",
    parameter CEMUX = "CE",
    parameter CLKMUX = "CLK",
    parameter LSRMUX = "LSR",
    parameter REGSET = "RESET",
    parameter SRMODE = "LSR_OVER_CE",
    parameter INIT = 0
) (
    input CLK, DI, LSR, CE,
    output reg Q
);
    initial Q = INIT;
    always @(posedge CLK) begin
        if (LSR && LSRMUX == "LSR")
            Q <= (REGSET == "SET") ? 1'b1 : 1'b0;
        else if (CE)
            Q <= DI;
    end
endmodule

module CCU2C_SIM #(
    parameter INIT0 = 16'h0000,
    parameter INIT1 = 16'h0000,
    parameter INJECT1_0 = "NO",
    parameter INJECT1_1 = "NO"
) (
    input CIN, A0, B0, C0, D0, A1, B1, C1, D1,
    output S0, S1, COUT
);
    wire lut0 = INIT0[{D0, C0, B0, A0}];
    wire lut1 = INIT1[{D1, C1, B1, A1}];
    wire carry0 = (A0 & B0) | (CIN & (A0 ^ B0));
    wire carry1 = (A1 & B1) | (carry0 & (A1 ^ B1));
    assign S0 = lut0 ^ CIN;
    assign S1 = lut1 ^ carry0;
    assign COUT = carry1;
endmodule

module DP16KD_SIM #(
    parameter DATA_WIDTH_A = 18,
    parameter DATA_WIDTH_B = 18,
    parameter REGMODE_A    = "NOREG",
    parameter REGMODE_B    = "NOREG",
    parameter WRITEMODE_A  = "NORMAL",
    parameter WRITEMODE_B  = "NORMAL"
) (
    input [13:0] ADA, ADB,
    input [17:0] DIA, DIB,
    output reg [17:0] DOA, DOB,
    input CLKA, CLKB,
    input WEA, WEB,
    input CEA, CEB,
    input OCEA, OCEB,
    input RSTA, RSTB,
    input CSA0, CSA1, CSA2,
    input CSB0, CSB1, CSB2
);
    reg [17:0] mem [0:1023];
    integer i;
    initial begin
        for (i = 0; i < 1024; i = i + 1)
            mem[i] = 18'b0;
        DOA = 0;
        DOB = 0;
    end
    always @(posedge CLKA) begin
        if (CEA) begin
            if (WEA)
                mem[ADA[13:4]] <= DIA;
            DOA <= mem[ADA[13:4]];
        end
        if (RSTA) DOA <= 0;
    end
    always @(posedge CLKB) begin
        if (CEB) begin
            if (WEB)
                mem[ADB[13:4]] <= DIB;
            DOB <= mem[ADB[13:4]];
        end
        if (RSTB) DOB <= 0;
    end
endmodule

module MULT18X18D_SIM #(
    parameter REG_INPUTA_CLK = "NONE",
    parameter REG_INPUTB_CLK = "NONE",
    parameter REG_OUTPUT_CLK = "NONE"
) (
    input signed [17:0] A, B,
    input CLK0, CLK1, CLK2, CLK3,
    input CE0, CE1, CE2, CE3,
    input RST0, RST1, RST2, RST3,
    input SIGNEDA, SIGNEDB,
    output [35:0] P
);
    wire signed [35:0] product = A * B;
    assign P = product;
endmodule
"""


def generate_cell_models() -> str:
    """Return behavioral Verilog models for ECP5 cell types."""
    return _CELL_MODELS


def generate_postsynth_verilog(netlist: ECP5Netlist) -> str:
    """Generate a behavioral Verilog module from an ECP5 netlist.

    The output can be compiled with iverilog for post-synthesis
    simulation comparison.
    """
    lines: list[str] = []
    lines.append("`timescale 1ns/1ps")
    lines.append(f"module {netlist.top}_postsynth (")

    # Ports
    port_lines: list[str] = []
    for name, info in sorted(netlist.ports.items()):
        direction = info["direction"]
        bits = info["bits"]
        if len(bits) > 1:
            port_lines.append(f"  {direction} [{len(bits)-1}:0] {name}")
        else:
            port_lines.append(f"  {direction} {name}")
    lines.append(",\n".join(port_lines))
    lines.append(");")
    lines.append("")

    # Wire declarations for internal nets
    max_bit = netlist._bit_counter
    lines.append(f"  wire [{max_bit-1}:0] _net;")
    lines.append("")

    # Port assignments
    for name, info in sorted(netlist.ports.items()):
        bits = info["bits"]
        for i, bit in enumerate(bits):
            if isinstance(bit, int) and bit >= 2:
                if info["direction"] == "input":
                    if len(bits) > 1:
                        lines.append(f"  assign _net[{bit}] = {name}[{i}];")
                    else:
                        lines.append(f"  assign _net[{bit}] = {name};")
                elif info["direction"] == "output":
                    if len(bits) > 1:
                        lines.append(f"  assign {name}[{i}] = _net[{bit}];")
                    else:
                        lines.append(f"  assign {name} = _net[{bit}];")
    lines.append("")

    # Constant assignments
    lines.append("  assign _net[0] = 1'b0;  // constant 0")
    lines.append("  assign _net[1] = 1'b1;  // constant 1")
    lines.append("")

    # Cell instantiations
    for name, cell in sorted(netlist.cells.items()):
        safe_name = name.replace("$", "_").replace(".", "_")
        if cell.cell_type == "LUT4":
            init_bin = cell.parameters.get("INIT", "0000000000000000")
            # Convert binary string to hex for Verilog parameter
            try:
                init_int = int(init_bin, 2)
            except ValueError:
                init_int = 0
            a = _bit_ref(cell.ports.get("A", ["0"]))
            b = _bit_ref(cell.ports.get("B", ["0"]))
            c = _bit_ref(cell.ports.get("C", ["0"]))
            d = _bit_ref(cell.ports.get("D", ["0"]))
            z = _bit_ref(cell.ports.get("Z", ["0"]))
            lines.append(f"  LUT4_SIM #(.INIT(16'h{init_int:04X})) {safe_name} (")
            lines.append(f"    .A({a}), .B({b}), .C({c}), .D({d}), .Z({z}));")
        elif cell.cell_type == "TRELLIS_FF":
            clk = _bit_ref(cell.ports.get("CLK", ["0"]))
            di = _bit_ref(cell.ports.get("DI", ["0"]))
            lsr = _bit_ref(cell.ports.get("LSR", ["0"]))
            ce = _bit_ref(cell.ports.get("CE", ["1"]))
            q = _bit_ref(cell.ports.get("Q", ["0"]))
            init_val = cell.attributes.get("init_value", "0")
            lines.append(f"  TRELLIS_FF_SIM #(.INIT({init_val})) {safe_name} (")
            lines.append(f"    .CLK({clk}), .DI({di}), .LSR({lsr}), .CE({ce}), .Q({q}));")
        elif cell.cell_type == "CCU2C":
            init0 = cell.parameters.get("INIT0", "0000000000000000")
            init1 = cell.parameters.get("INIT1", "0000000000000000")
            try:
                init0_int = int(init0, 2)
            except ValueError:
                init0_int = 0
            try:
                init1_int = int(init1, 2)
            except ValueError:
                init1_int = 0
            cin = _bit_ref(cell.ports.get("CIN", ["0"]))
            a0 = _bit_ref(cell.ports.get("A0", ["0"]))
            b0 = _bit_ref(cell.ports.get("B0", ["0"]))
            c0 = _bit_ref(cell.ports.get("C0", ["0"]))
            d0 = _bit_ref(cell.ports.get("D0", ["0"]))
            a1 = _bit_ref(cell.ports.get("A1", ["0"]))
            b1 = _bit_ref(cell.ports.get("B1", ["0"]))
            c1 = _bit_ref(cell.ports.get("C1", ["0"]))
            d1 = _bit_ref(cell.ports.get("D1", ["0"]))
            s0 = _bit_ref(cell.ports.get("S0", ["0"]))
            s1 = _bit_ref(cell.ports.get("S1", ["0"]))
            cout = _bit_ref(cell.ports.get("COUT", ["0"]))
            lines.append(f"  CCU2C_SIM #(.INIT0(16'h{init0_int:04X}), .INIT1(16'h{init1_int:04X})) {safe_name} (")
            lines.append(f"    .CIN({cin}), .A0({a0}), .B0({b0}), .C0({c0}), .D0({d0}),")
            lines.append(f"    .A1({a1}), .B1({b1}), .C1({c1}), .D1({d1}),")
            lines.append(f"    .S0({s0}), .S1({s1}), .COUT({cout}));")

    lines.append("")
    lines.append("endmodule")

    return "\n".join(lines)


def _bit_ref(bits: list) -> str:
    """Convert a bit list to a Verilog net reference."""
    if not bits:
        return "1'b0"
    bit = bits[0]
    if isinstance(bit, int):
        if bit == 0:
            return "1'b0"
        if bit == 1:
            return "1'b1"
        return f"_net[{bit}]"
    if bit == "0":
        return "1'b0"
    if bit == "1":
        return "1'b1"
    return f"_net[{bit}]"
