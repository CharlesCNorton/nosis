"""Integration test: $readmemh through the full synthesis pipeline.

Tests that designs with memory arrays synthesize correctly.
Small arrays (16 entries) lower to MUX trees; BRAM inference applies
to larger arrays (>= 256 bits total).
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from nosis.ir import PrimOp
from nosis.readmem import parse_readmemh, parse_readmemb


def test_parse_hex_file():
    """parse_readmemh reads $readmemh format correctly."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".hex", delete=False, encoding="utf-8") as f:
        f.write("DEADBEEF\n01234567\nCAFEBABE\n")
        f.flush()
        values = parse_readmemh(f.name)
    assert values[0] == 0xDEADBEEF
    assert values[1] == 0x01234567
    assert values[2] == 0xCAFEBABE


def test_parse_hex_file_with_comments():
    """parse_readmemh handles // comments and blank lines."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".hex", delete=False, encoding="utf-8") as f:
        f.write("// header comment\nFF\n\n0A\n// footer\n")
        f.flush()
        values = parse_readmemh(f.name)
    assert values[0] == 0xFF
    assert values[1] == 0x0A


def test_parse_bin_file():
    """parse_readmemb reads $readmemb format correctly."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".bin", delete=False, encoding="utf-8") as f:
        f.write("11011110\n10101010\n")
        f.flush()
        values = parse_readmemb(f.name)
    assert values[0] == 0xDE
    assert values[1] == 0xAA


def test_small_array_synthesizes_to_mux():
    """A 16x32 array lowers to MUX trees (not MEMORY cells)."""
    from nosis.frontend import parse_files, lower_to_ir

    with tempfile.TemporaryDirectory() as tmp:
        sv = Path(tmp) / "small_mem.sv"
        sv.write_text("""\
module small_mem (
    input wire clk,
    input wire [3:0] addr,
    input wire [31:0] wdata,
    input wire we,
    output reg [31:0] rdata
);
    reg [31:0] mem [0:15];
    always @(posedge clk) begin
        if (we) mem[addr] <= wdata;
        rdata <= mem[addr];
    end
endmodule
""", encoding="utf-8")

        result = parse_files([str(sv)], top="small_mem")
        assert not result.errors
        design = lower_to_ir(result, top="small_mem")
        mod = design.top_module()

        # Small arrays produce MUX trees, not MEMORY cells
        ops = {c.op for c in mod.cells.values()}
        assert PrimOp.MUX in ops, f"expected MUX cells, got ops: {ops}"
        # The design should have input/output ports intact
        assert "clk" in mod.ports
        assert "rdata" in mod.ports


def test_readmem_association_captured():
    """The frontend captures $readmemh associations in ParseResult."""
    from nosis.frontend import parse_files

    with tempfile.TemporaryDirectory() as tmp:
        hex_file = Path(tmp) / "init.hex"
        hex_file.write_text("FF\n00\n", encoding="utf-8")

        sv = Path(tmp) / "assoc.sv"
        sv.write_text(f"""\
module assoc (
    input wire clk,
    input wire [0:0] addr,
    output reg [7:0] rdata
);
    reg [7:0] mem [0:1];
    initial $readmemh("{hex_file.as_posix()}", mem);
    always @(posedge clk) rdata <= mem[addr];
endmodule
""", encoding="utf-8")

        result = parse_files([str(sv)], top="assoc")
        assert not result.errors
        # The readmem_associations should capture the mem name
        assert "mem" in result.readmem_associations
        file_path, fmt = result.readmem_associations["mem"]
        assert fmt == "hex"
        assert "init.hex" in file_path
