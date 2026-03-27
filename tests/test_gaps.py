"""Tests for identified gaps in nosis coverage.

Covers: multi-file synthesis (item 37), regression cell count locks (item 39),
signed comparison gate-level (item 42), ALU54B MAC detection (item 43),
DP16KD tiled inference (item 44).
"""

from __future__ import annotations

import pytest

from nosis.ir import Module, PrimOp
from nosis.eval import eval_const_op
from nosis.bram import infer_brams, _count_brams_needed, _fits_dp16kd
from nosis.dsp import detect_mac


# ---------------------------------------------------------------------------
# Item 37: multi-file synthesis smoke test
# ---------------------------------------------------------------------------

def _try_import_thaw_sources():
    """Check if RIME thaw sources are available (nosis + rime both installed)."""
    try:
        from pathlib import Path
        rime_root = Path(__file__).parent.parent.parent / "rime"
        if not rime_root.exists():
            # Try sibling directory
            rime_root = Path(__file__).parent.parent.parent.parent / "rime"
        thaw_top = rime_root / "firmware" / "images" / "thaw" / "top.sv"
        return thaw_top.exists(), rime_root
    except Exception:
        return False, None


@pytest.mark.skipif(not _try_import_thaw_sources()[0], reason="RIME thaw sources not available")
def test_multi_file_thaw_parse():
    """Parse a multi-file RIME design (thaw) through the frontend."""
    from nosis.frontend import parse_files
    from pathlib import Path
    _, rime_root = _try_import_thaw_sources()
    core = rime_root / "firmware" / "core"
    thaw = rime_root / "firmware" / "images" / "thaw"
    sources = [
        str(thaw / "top.sv"),
        str(thaw / "thaw_service.sv"),
        str(core / "uart" / "uart_tx.sv"),
        str(core / "uart" / "uart_rx.sv"),
        str(core / "service" / "flash_spi_master.sv"),
        str(core / "service" / "sd_spi_master.sv"),
        str(core / "service" / "sdram_controller.sv"),
        str(core / "service" / "sdram_bridge.sv"),
    ]
    existing = [s for s in sources if Path(s).exists()]
    if len(existing) < 4:
        pytest.skip("not enough thaw sources available")
    result = parse_files(existing, top="top")
    assert len(result.top_instances) >= 1
    assert not result.errors


# ---------------------------------------------------------------------------
# Item 39: regression cell count locks
# ---------------------------------------------------------------------------

def test_uart_tx_cell_count_regression():
    """uart_tx LUT4 count must not regress above known ceiling."""
    from nosis.frontend import parse_files, lower_to_ir
    from nosis.passes import run_default_passes
    from nosis.techmap import map_to_ecp5
    from nosis.slicepack import pack_slices
    from nosis.carry import infer_carry_chains
    from nosis.fsm import extract_fsms, annotate_fsm_cells
    from nosis.lutpack import pack_luts_ir
    from pathlib import Path

    uart_tx = Path(__file__).parent / "designs" / "uart_tx.sv"
    if not uart_tx.exists():
        pytest.skip("uart_tx.sv not available")
    result = parse_files([str(uart_tx)], top="uart_tx")
    design = lower_to_ir(result, top="uart_tx")
    mod = design.top_module()
    run_default_passes(mod)
    infer_carry_chains(mod)
    fsms = extract_fsms(mod)
    annotate_fsm_cells(mod, fsms)
    pack_luts_ir(mod)
    design.eliminate_dead_modules()
    netlist = map_to_ecp5(design)
    pack_slices(netlist)
    stats = netlist.stats()
    # Known ceiling from benchmarks: 51 LUT4, 46 FF, 64 CCU2C
    assert stats.get("LUT4", 0) <= 60, f"LUT4 regression: {stats.get('LUT4', 0)} > 60"
    assert stats.get("TRELLIS_FF", 0) <= 55, f"FF regression: {stats.get('TRELLIS_FF', 0)} > 55"


# ---------------------------------------------------------------------------
# Item 42: signed comparison correctness
# ---------------------------------------------------------------------------

def test_signed_lt_evaluator():
    """Signed LT produces correct results for negative values."""
    # -1 < 0 should be True (signed)
    r = eval_const_op(PrimOp.LT, {"A": 0xFF, "B": 0x00}, {"signed": True}, 8)
    assert r == 1, f"signed(-1) < signed(0) should be True, got {r}"

    # 0 < -1 should be False (signed)
    r = eval_const_op(PrimOp.LT, {"A": 0x00, "B": 0xFF}, {"signed": True}, 8)
    assert r == 0

    # -128 < 127 should be True (signed)
    r = eval_const_op(PrimOp.LT, {"A": 0x80, "B": 0x7F}, {"signed": True}, 8)
    assert r == 1

    # Unsigned: 0xFF > 0x00
    r = eval_const_op(PrimOp.LT, {"A": 0xFF, "B": 0x00}, {}, 8)
    assert r == 0


def test_signed_ge_evaluator():
    """Signed GE produces correct results."""
    r = eval_const_op(PrimOp.GE, {"A": 0x00, "B": 0xFF}, {"signed": True}, 8)
    assert r == 1  # 0 >= -1

    r = eval_const_op(PrimOp.GE, {"A": 0xFF, "B": 0x00}, {"signed": True}, 8)
    assert r == 0  # -1 >= 0 is False


def test_signed_div_truncates_toward_zero():
    """Signed DIV truncates toward zero (SystemVerilog semantics)."""
    # -7 / 2 = -3 (truncate toward zero, not -4)
    r = eval_const_op(PrimOp.DIV, {"A": 0xF9, "B": 0x02}, {"signed": True}, 8)
    # -7 in 8-bit two's complement is 0xF9
    # -7 / 2 = -3, which in 8-bit is 0xFD
    assert r == 0xFD, f"signed(-7)/2 should be -3 (0xFD), got 0x{r:02X}"


# ---------------------------------------------------------------------------
# Item 43: ALU54B MAC detection
# ---------------------------------------------------------------------------

def test_mac_detection():
    """detect_mac identifies multiply-accumulate feedback loops."""
    mod = Module(name="mac_test")
    # Build: acc_ff -> add -> acc_next -> ff(clk) -> acc_ff
    #                   ^
    #                   |
    #                mul -> mul_out
    clk = mod.add_net("clk", 1)
    a_in = mod.add_net("a_in", 16)
    b_in = mod.add_net("b_in", 16)
    acc_q = mod.add_net("acc_q", 32)
    mul_out = mod.add_net("mul_out", 32)
    add_out = mod.add_net("add_out", 32)

    inp_clk = mod.add_cell("inp_clk", PrimOp.INPUT, port_name="clk")
    mod.connect(inp_clk, "Y", clk, direction="output")
    inp_a = mod.add_cell("inp_a", PrimOp.INPUT, port_name="a_in")
    mod.connect(inp_a, "Y", a_in, direction="output")
    inp_b = mod.add_cell("inp_b", PrimOp.INPUT, port_name="b_in")
    mod.connect(inp_b, "Y", b_in, direction="output")

    mul = mod.add_cell("mul0", PrimOp.MUL)
    mod.connect(mul, "A", a_in)
    mod.connect(mul, "B", b_in)
    mod.connect(mul, "Y", mul_out, direction="output")

    add = mod.add_cell("add0", PrimOp.ADD)
    mod.connect(add, "A", acc_q)
    mod.connect(add, "B", mul_out)
    mod.connect(add, "Y", add_out, direction="output")

    ff = mod.add_cell("ff0", PrimOp.FF)
    mod.connect(ff, "CLK", clk)
    mod.connect(ff, "D", add_out)
    mod.connect(ff, "Q", acc_q, direction="output")

    mod.ports["clk"] = clk
    mod.ports["a_in"] = a_in
    mod.ports["b_in"] = b_in

    detected = detect_mac(mod)
    assert detected >= 1, "MAC pattern should be detected"
    assert mul.params.get("dsp_mac") is True


# ---------------------------------------------------------------------------
# Item 44: DP16KD tiled inference
# ---------------------------------------------------------------------------

def test_dp16kd_single_fits():
    """Single DP16KD fits a 512x32 array."""
    assert _fits_dp16kd(512, 32) is not None

def test_dp16kd_single_too_large():
    """2048x64 does not fit a single DP16KD."""
    assert _fits_dp16kd(2048, 64) is None

def test_dp16kd_tiled_count():
    """Tiled inference counts BRAMs correctly for arrays exceeding one DP16KD."""
    # 4096x32: needs 4096/512 = 8 deep, so 8 BRAMs
    count = _count_brams_needed(4096, 32)
    assert count > 1
    assert count <= 56  # ECP5-25F limit

def test_bram_tiled_inference_tag():
    """Large array gets tagged as DP16KD_TILED."""
    mod = Module(name="big_mem")
    cell = mod.add_cell("mem0", PrimOp.MEMORY, depth=4096, width=32)
    out = mod.add_net("rdata", 32)
    mod.connect(cell, "RDATA", out, direction="output")
    tagged = infer_brams(mod)
    assert tagged == 1
    assert cell.params.get("bram_config") == "DP16KD_TILED"
    assert cell.params.get("bram_count", 0) > 1
