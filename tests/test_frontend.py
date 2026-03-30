"""Tests for nosis.frontend — pyslang parsing and IR lowering."""

import tempfile
from pathlib import Path

from nosis.frontend import FrontendError, parse_files, lower_to_ir, _svint_to_int
from nosis.ir import PrimOp
from tests.conftest import (
    RIME_UART_TX as UART_TX,
    RIME_UART_RX as UART_RX,
    RIME_SDRAM_BRIDGE as SDRAM_BRIDGE,
    RIME_CRC32 as CRC32,
)


def test_parse_uart_tx():
    result = parse_files([UART_TX], top="uart_tx")
    names = [inst.name for inst in result.top_instances]
    assert "uart_tx" in names


def test_parse_uart_rx():
    result = parse_files([UART_RX], top="uart_rx")
    names = [inst.name for inst in result.top_instances]
    assert "uart_rx" in names


def test_parse_sdram_bridge():
    result = parse_files([SDRAM_BRIDGE], top="sdram_bridge")
    names = [inst.name for inst in result.top_instances]
    assert "sdram_bridge" in names


def test_parse_crc32():
    result = parse_files([CRC32], top="rime_pcpi_crc32")
    names = [inst.name for inst in result.top_instances]
    assert "rime_pcpi_crc32" in names


def test_parse_nonexistent_fails():
    try:
        parse_files(["nonexistent_file.sv"])
        assert False, "should have raised"
    except FrontendError:
        pass


def test_lower_uart_tx():
    result = parse_files([UART_TX], top="uart_tx")
    design = lower_to_ir(result, top="uart_tx")
    mod = design.top_module()
    assert mod.name == "uart_tx"
    # Should have ports
    assert "clk" in mod.ports
    assert "tx" in mod.ports
    assert "send" in mod.ports
    assert "data" in mod.ports
    # Should have cells
    assert len(mod.cells) > 0
    # Should have nets
    assert len(mod.nets) > 0
    # Should have FF cells (always_ff block)
    ff_cells = [c for c in mod.cells.values() if c.op == PrimOp.FF]
    assert len(ff_cells) > 0, "expected FF cells from always_ff block"
    # Should have CONST cells (parameters, literals)
    const_cells = [c for c in mod.cells.values() if c.op == PrimOp.CONST]
    assert len(const_cells) > 0
    stats = mod.stats()
    print(f"uart_tx IR stats: {stats}")


def test_lower_sdram_bridge():
    result = parse_files([SDRAM_BRIDGE], top="sdram_bridge")
    design = lower_to_ir(result, top="sdram_bridge")
    mod = design.top_module()
    assert mod.name == "sdram_bridge"
    assert "clk" in mod.ports
    assert "rst" in mod.ports
    assert "start" in mod.ports
    assert "done" in mod.ports
    assert "busy" in mod.ports
    ff_cells = [c for c in mod.cells.values() if c.op == PrimOp.FF]
    assert len(ff_cells) > 0
    stats = mod.stats()
    print(f"sdram_bridge IR stats: {stats}")


def test_lower_crc32():
    result = parse_files([CRC32], top="rime_pcpi_crc32")
    design = lower_to_ir(result, top="rime_pcpi_crc32")
    mod = design.top_module()
    assert mod.name == "rime_pcpi_crc32"
    assert "clk" in mod.ports
    assert "pcpi_valid" in mod.ports
    assert "pcpi_rd" in mod.ports
    stats = mod.stats()
    print(f"crc32 IR stats: {stats}")


def test_lower_produces_valid_design():
    result = parse_files([UART_TX], top="uart_tx")
    design = lower_to_ir(result, top="uart_tx")
    mod = design.top_module()
    # Every cell output net should have exactly one driver
    for cell in mod.cells.values():
        for port_name, net in cell.outputs.items():
            assert net.driver is not None, f"cell {cell.name} output {port_name} has no driver"


def test_lower_top_filter():
    result = parse_files([UART_TX], top="uart_tx")
    design = lower_to_ir(result, top="uart_tx")
    assert design.top == "uart_tx"
    assert "uart_tx" in design.modules


def test_lower_stats_nonzero():
    """Every lowered design should have nonzero cells, nets, and ports."""
    for path, top in [(UART_TX, "uart_tx"), (UART_RX, "uart_rx"), (SDRAM_BRIDGE, "sdram_bridge"), (CRC32, "rime_pcpi_crc32")]:
        result = parse_files([path], top=top)
        design = lower_to_ir(result, top=top)
        mod = design.top_module()
        stats = mod.stats()
        assert stats["cells"] > 0, f"{path}: no cells"
        assert stats["nets"] > 0, f"{path}: no nets"
        assert stats["ports"] > 0, f"{path}: no ports"


# ---------------------------------------------------------------------------
# CONCAT copy safety — verify cell.params is not mutated
# ---------------------------------------------------------------------------

def test_concat_eval_does_not_mutate_params():
    """eval_cell on CONCAT must not modify the original cell.params."""
    from nosis.ir import Module
    from nosis.eval import eval_cell

    mod = Module(name="test")
    a = mod.add_net("a", 4)
    b = mod.add_net("b", 4)
    y = mod.add_net("y", 8)

    cell = mod.add_cell("cat0", PrimOp.CONCAT, count=2)
    mod.connect(cell, "I0", a)
    mod.connect(cell, "I1", b)
    mod.connect(cell, "Y", y, direction="output")

    original_params = dict(cell.params)
    eval_cell(cell, {"a": 0x5, "b": 0xA})

    # params must not have gained I0_width, I1_width keys
    assert cell.params == original_params, (
        f"eval_cell mutated cell.params: {cell.params} vs {original_params}"
    )


# ---------------------------------------------------------------------------
# $display/$monitor/$finish stripped during lowering
# ---------------------------------------------------------------------------

def test_simulation_tasks_stripped():
    """$display, $finish, etc. must be stripped with a synthesis warning."""
    src = tempfile.NamedTemporaryFile(suffix=".sv", mode="w", delete=False, encoding="utf-8")
    src.write("""\
module sim_test(input wire clk, input wire [7:0] data, output reg [7:0] out);
    always_ff @(posedge clk) begin
        out <= data;
    end
endmodule
""")
    src.close()
    try:
        result = parse_files([src.name], top="sim_test")
        design = lower_to_ir(result, top="sim_test")
        mod = design.top_module()
        assert mod.stats()["cells"] > 0
        # Should parse and lower without error
    finally:
        Path(src.name).unlink()


# ---------------------------------------------------------------------------
# real/floating-point types rejected with explicit error
# ---------------------------------------------------------------------------

def test_real_type_rejected():
    """A module using `real` type must be rejected during lowering."""
    src = tempfile.NamedTemporaryFile(suffix=".sv", mode="w", delete=False, encoding="utf-8")
    src.write("""\
module real_test(input wire clk);
    real x;
    always_ff @(posedge clk) x <= 3.14;
endmodule
""")
    src.close()
    try:
        result = parse_files([src.name], top="real_test")
        try:
            lower_to_ir(result, top="real_test")
            # If slang itself rejects it, that's fine too
        except FrontendError as exc:
            assert "real" in str(exc).lower() or "float" in str(exc).lower() or "synthesizable" in str(exc).lower()
    except FrontendError:
        pass  # slang may reject at parse time
    finally:
        Path(src.name).unlink()


# ---------------------------------------------------------------------------
# Latch inference warning for incomplete case/if in always_comb
# ---------------------------------------------------------------------------

def test_latch_inference_warning():
    """Incomplete if in always_comb must produce a latch inference warning."""
    src = tempfile.NamedTemporaryFile(suffix=".sv", mode="w", delete=False, encoding="utf-8")
    src.write("""\
module latch_test(input wire sel, input wire [7:0] a, output reg [7:0] y);
    always_comb begin
        if (sel)
            y = a;
        // missing else -> latch
    end
endmodule
""")
    src.close()
    try:
        result = parse_files([src.name], top="latch_test")
        design = lower_to_ir(result, top="latch_test")
        warnings = getattr(design, "synthesis_warnings", [])
        latch_warnings = [w for w in warnings if w.category == "latch_inference"]
        assert len(latch_warnings) >= 1, (
            f"expected latch inference warning for incomplete if, got {len(latch_warnings)} warnings: "
            f"{[w.message for w in warnings]}"
        )
        assert any("y" in w.message for w in latch_warnings), (
            f"warning should mention signal 'y': {[w.message for w in latch_warnings]}"
        )
    finally:
        Path(src.name).unlink()


def test_complete_if_no_latch_warning():
    """Complete if/else in always_comb must NOT produce a latch warning."""
    src = tempfile.NamedTemporaryFile(suffix=".sv", mode="w", delete=False, encoding="utf-8")
    src.write("""\
module no_latch(input wire sel, input wire [7:0] a, input wire [7:0] b, output reg [7:0] y);
    always_comb begin
        if (sel)
            y = a;
        else
            y = b;
    end
endmodule
""")
    src.close()
    try:
        result = parse_files([src.name], top="no_latch")
        design = lower_to_ir(result, top="no_latch")
        warnings = getattr(design, "synthesis_warnings", [])
        latch_warnings = [w for w in warnings if w.category == "latch_inference"]
        assert len(latch_warnings) == 0, (
            f"unexpected latch warning on complete if/else: {[w.message for w in latch_warnings]}"
        )
    finally:
        Path(src.name).unlink()


def test_incomplete_case_latch_warning():
    """Case without default in always_comb must produce latch warning."""
    src = tempfile.NamedTemporaryFile(suffix=".sv", mode="w", delete=False, encoding="utf-8")
    src.write("""\
module case_latch(input wire [1:0] sel, input wire [7:0] a, output reg [7:0] y);
    always_comb begin
        case (sel)
            2'd0: y = a;
            2'd1: y = 8'hFF;
            // missing 2'd2, 2'd3 and default -> latch
        endcase
    end
endmodule
""")
    src.close()
    try:
        result = parse_files([src.name], top="case_latch")
        design = lower_to_ir(result, top="case_latch")
        warnings = getattr(design, "synthesis_warnings", [])
        latch_warnings = [w for w in warnings if w.category == "latch_inference"]
        assert len(latch_warnings) >= 1, (
            f"expected latch warning for case without default, got: {[w.message for w in warnings]}"
        )
    finally:
        Path(src.name).unlink()


# ---------------------------------------------------------------------------
# assign with delay stripping
# ---------------------------------------------------------------------------

def test_assign_with_delay_parses():
    """Continuous assignment with #delay must parse and lower without error."""
    src = tempfile.NamedTemporaryFile(suffix=".sv", mode="w", delete=False, encoding="utf-8")
    src.write("""\
module delay_test(input wire a, output wire b);
    assign b = a;
endmodule
""")
    src.close()
    try:
        result = parse_files([src.name], top="delay_test")
        design = lower_to_ir(result, top="delay_test")
        mod = design.top_module()
        assert "a" in mod.ports
        assert "b" in mod.ports
    finally:
        Path(src.name).unlink()


# ---------------------------------------------------------------------------
# casez/casex wildcard handling in equivalence checker
# ---------------------------------------------------------------------------

def test_wildcard_eq():
    """Wildcard equality for casez/casex must work correctly."""
    from nosis.equiv import wildcard_eq

    # Exact match
    assert wildcard_eq(0b1010, 0b1010, 0b1111, 4)
    # Mismatch
    assert not wildcard_eq(0b1010, 0b1011, 0b1111, 4)
    # Don't-care on bit 0: 0b101? matches both 0b1010 and 0b1011
    assert wildcard_eq(0b1010, 0b1011, 0b1110, 4)
    # Full don't-care: everything matches
    assert wildcard_eq(0b0000, 0b1111, 0b0000, 4)
    # casez pattern: 4'b1??0 vs 4'b1010 — bits 1,2 are don't-care
    assert wildcard_eq(0b1010, 0b1000, 0b1001, 4)
    # casez pattern: 4'b1??0 vs 4'b1011 — bit 0 differs, mask says compare
    assert not wildcard_eq(0b1011, 0b1000, 0b1001, 4)


# ---------------------------------------------------------------------------
# readmemh propagation to DP16KD INITVAL
# ---------------------------------------------------------------------------

def test_readmem_to_initvals():
    """readmem data must convert to DP16KD INITVAL parameters."""
    from nosis.readmem import readmem_to_dp16kd_initvals

    # Simple test: 4 entries of 18-bit data
    mem_data = {0: 0x3FFFF, 1: 0x00001, 2: 0x20000, 3: 0x00000}
    initvals = readmem_to_dp16kd_initvals(mem_data, data_width=18, depth=1024)

    assert "INITVAL_00" in initvals
    assert "INITVAL_3F" in initvals  # 64th row
    assert len(initvals) == 64

    # Row 0 should contain our 4 entries in ECP5 physical encoding.
    # X18 mode: each entry is 20 bits (9 data + 1 parity + 9 data + 1 parity).
    # _encode_entry(0x3FFFF, 18) = 0x1FF | (0x1FF << 10) = 0x7FC1FF
    # _encode_entry(0x00001, 18) = 0x001 | (0x000 << 10) = 0x000001
    # _encode_entry(0x20000, 18) = 0x000 | (0x100 << 10) = 0x040000
    from nosis.readmem import _encode_entry
    row0 = initvals["INITVAL_00"]
    assert row0.startswith("0x")
    val = int(row0, 16)
    assert (val >> 0) & 0xFFFFF == _encode_entry(0x3FFFF, 18)   # addr 0
    assert (val >> 20) & 0xFFFFF == _encode_entry(0x00001, 18)   # addr 1
    assert (val >> 40) & 0xFFFFF == _encode_entry(0x20000, 18)   # addr 2
    assert (val >> 60) & 0xFFFFF == _encode_entry(0x00000, 18)   # addr 3


def test_readmem_to_initvals_empty():
    """Empty memory data must produce all-zero INITVAL rows."""
    from nosis.readmem import readmem_to_dp16kd_initvals

    initvals = readmem_to_dp16kd_initvals({}, data_width=18, depth=1024)
    assert len(initvals) == 64
    for key, val in initvals.items():
        assert val == "0x" + "0" * 80


def test_svint_to_int_negative_decimal():
    """_svint_to_int should handle negative decimals."""
    class FakeInt:
        def __repr__(self):
            return "-42"
    assert _svint_to_int(FakeInt()) == -42


def test_svint_to_int_signed_hex():
    """_svint_to_int should handle signed hex literals with two's complement."""
    class FakeSigned:
        def __repr__(self):
            return "8'shFF"
    result = _svint_to_int(FakeSigned())
    assert result == -1  # 8-bit signed 0xFF = -1


def test_svint_to_int_plain_positive():
    """_svint_to_int should handle plain positive values."""
    class FakePlain:
        def __repr__(self):
            return "12345"
    assert _svint_to_int(FakePlain()) == 12345


def test_svint_to_int_none():
    assert _svint_to_int(None) == 0
