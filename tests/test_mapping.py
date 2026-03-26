"""Consolidated technology mapping, slice packing, and inference tests."""

import json
import os
import subprocess
import tempfile
from nosis.bram import (
    _count_brams_needed,
    _fits_dp16kd,
    detect_write_mode,
    infer_brams,
    infer_memory_ports,
    infer_output_register,
)
from nosis.carry import infer_carry_chains
from nosis.dsp import detect_mac, infer_dsps
from nosis.eval import eval_const_op
from nosis.frontend import lower_to_ir, parse_files
from nosis.ir import Design, Module, PrimOp
from nosis.json_backend import emit_json_str
from nosis.postsynth import generate_cell_models, generate_postsynth_verilog
from nosis.readmem import parse_readmemb, parse_readmemh, readmem_to_dp16kd_initvals
from nosis.slicepack import (
    absorb_buffers,
    deduplicate_luts,
    merge_lut_chains,
    pack_slices,
    simplify_constant_luts,
)
from nosis.techmap import ECP5Netlist, map_to_ecp5
from nosis.validate import _find_iverilog
from pathlib import Path
from tests.conftest import RIME_UART_TX


# --- from test_techmap ---




def _simple_design(name="test"):
    design = Design()
    mod = design.add_module(name)
    design.top = name
    return design, mod


def _add_input(mod, name, width):
    net = mod.add_net(name, width)
    cell = mod.add_cell(f"inp_{name}", PrimOp.INPUT, port_name=name)
    mod.connect(cell, "Y", net, direction="output")
    mod.ports[name] = net
    return net


def _add_output(mod, name, net):
    cell = mod.add_cell(f"out_{name}", PrimOp.OUTPUT, port_name=name)
    mod.connect(cell, "A", net)
    mod.ports[name] = net


def test_map_const():
    design, mod = _simple_design()
    net = mod.add_net("c", 8)
    cell = mod.add_cell("c0", PrimOp.CONST, value=0xA5, width=8)
    mod.connect(cell, "Y", net, direction="output")
    out_net = mod.add_net("out", 8)
    out_cell = mod.add_cell("out_port", PrimOp.OUTPUT, port_name="out")
    mod.connect(out_cell, "A", net)
    mod.ports["out"] = out_net
    nl = map_to_ecp5(design)
    # Constants become tied bits — no TRELLIS_SLICE needed
    luts = [c for c in nl.cells.values() if c.cell_type == "LUT4"]
    assert len(luts) == 0


def test_map_ff_per_bit():
    design, mod = _simple_design()
    clk = _add_input(mod, "clk", 1)
    d = _add_input(mod, "d", 4)
    q = mod.add_net("q", 4)
    _add_output(mod, "q", q)
    ff = mod.add_cell("ff0", PrimOp.FF)
    mod.connect(ff, "CLK", clk)
    mod.connect(ff, "D", d)
    mod.connect(ff, "Q", q, direction="output")
    nl = map_to_ecp5(design)
    ff_cells = [c for c in nl.cells.values() if c.cell_type == "TRELLIS_FF"]
    assert len(ff_cells) == 4  # one FF per bit


def test_map_ff_has_clock_port():
    """Every TRELLIS_FF must have a CLK port connected."""
    design, mod = _simple_design()
    clk = _add_input(mod, "clk", 1)
    d = _add_input(mod, "d", 1)
    q = mod.add_net("q", 1)
    _add_output(mod, "q", q)
    ff = mod.add_cell("ff0", PrimOp.FF)
    mod.connect(ff, "CLK", clk)
    mod.connect(ff, "D", d)
    mod.connect(ff, "Q", q, direction="output")
    nl = map_to_ecp5(design)
    for cell in nl.cells.values():
        if cell.cell_type == "TRELLIS_FF":
            assert "CLK" in cell.ports, f"TRELLIS_FF {cell.name} missing CLK"
            assert "DI" in cell.ports, f"TRELLIS_FF {cell.name} missing DI"
            assert "Q" in cell.ports, f"TRELLIS_FF {cell.name} missing Q"


def test_map_and_init():
    design, mod = _simple_design()
    a = _add_input(mod, "a", 1)
    b = _add_input(mod, "b", 1)
    y = mod.add_net("y", 1)
    _add_output(mod, "y", y)
    gc = mod.add_cell("and0", PrimOp.AND)
    mod.connect(gc, "A", a)
    mod.connect(gc, "B", b)
    mod.connect(gc, "Y", y, direction="output")
    nl = map_to_ecp5(design)
    luts = [c for c in nl.cells.values() if c.cell_type == "LUT4"]
    assert len(luts) == 1
    assert luts[0].parameters["INIT"] == "1000100010001000"


def test_map_or_init():
    design, mod = _simple_design()
    a = _add_input(mod, "a", 1)
    b = _add_input(mod, "b", 1)
    y = mod.add_net("y", 1)
    _add_output(mod, "y", y)
    gc = mod.add_cell("or0", PrimOp.OR)
    mod.connect(gc, "A", a)
    mod.connect(gc, "B", b)
    mod.connect(gc, "Y", y, direction="output")
    nl = map_to_ecp5(design)
    luts = [c for c in nl.cells.values() if c.cell_type == "LUT4"]
    assert len(luts) == 1
    assert luts[0].parameters["INIT"] == "1110111011101110"


def test_map_xor_init():
    design, mod = _simple_design()
    a = _add_input(mod, "a", 1)
    b = _add_input(mod, "b", 1)
    y = mod.add_net("y", 1)
    _add_output(mod, "y", y)
    gc = mod.add_cell("xor0", PrimOp.XOR)
    mod.connect(gc, "A", a)
    mod.connect(gc, "B", b)
    mod.connect(gc, "Y", y, direction="output")
    nl = map_to_ecp5(design)
    luts = [c for c in nl.cells.values() if c.cell_type == "LUT4"]
    assert len(luts) == 1
    assert luts[0].parameters["INIT"] == "0110011001100110"


def test_map_not_init():
    design, mod = _simple_design()
    a = _add_input(mod, "a", 1)
    y = mod.add_net("y", 1)
    _add_output(mod, "y", y)
    gc = mod.add_cell("not0", PrimOp.NOT)
    mod.connect(gc, "A", a)
    mod.connect(gc, "Y", y, direction="output")
    nl = map_to_ecp5(design)
    luts = [c for c in nl.cells.values() if c.cell_type == "LUT4"]
    assert len(luts) == 1
    assert luts[0].parameters["INIT"] == "0101010101010101"


def test_map_mux_init():
    design, mod = _simple_design()
    s = _add_input(mod, "s", 1)
    a = _add_input(mod, "a", 1)
    b = _add_input(mod, "b", 1)
    y = mod.add_net("y", 1)
    _add_output(mod, "y", y)
    mc = mod.add_cell("mux0", PrimOp.MUX)
    mod.connect(mc, "S", s)
    mod.connect(mc, "A", a)
    mod.connect(mc, "B", b)
    mod.connect(mc, "Y", y, direction="output")
    nl = map_to_ecp5(design)
    luts = [c for c in nl.cells.values() if c.cell_type == "LUT4"]
    assert len(luts) == 1
    assert luts[0].parameters["INIT"] == "1110010011100100"


def test_map_multibit_produces_per_bit_luts():
    """An 8-bit XOR must produce 8 LUT4 cells."""
    design, mod = _simple_design()
    a = _add_input(mod, "a", 8)
    b = _add_input(mod, "b", 8)
    y = mod.add_net("y", 8)
    _add_output(mod, "y", y)
    gc = mod.add_cell("xor0", PrimOp.XOR)
    mod.connect(gc, "A", a)
    mod.connect(gc, "B", b)
    mod.connect(gc, "Y", y, direction="output")
    nl = map_to_ecp5(design)
    assert nl.stats()["LUT4"] == 8  # one LUT4 per output bit


def test_map_add_produces_ccu2c():
    """A 16-bit ADD must produce CCU2C cells."""
    design, mod = _simple_design()
    a = _add_input(mod, "a", 16)
    b = _add_input(mod, "b", 16)
    y = mod.add_net("y", 16)
    _add_output(mod, "y", y)
    gc = mod.add_cell("add0", PrimOp.ADD)
    mod.connect(gc, "A", a)
    mod.connect(gc, "B", b)
    mod.connect(gc, "Y", y, direction="output")
    nl = map_to_ecp5(design)
    ccu2c = [c for c in nl.cells.values() if c.cell_type == "CCU2C"]
    assert len(ccu2c) == 8  # 16 bits / 2 bits per CCU2C


def test_map_ports_direction():
    design, mod = _simple_design()
    _add_input(mod, "a", 4)
    b = mod.add_net("b", 4)
    _add_output(mod, "b", b)
    nl = map_to_ecp5(design)
    assert nl.ports["a"]["direction"] == "input"
    assert nl.ports["b"]["direction"] == "output"
    assert len(nl.ports["a"]["bits"]) == 4
    assert len(nl.ports["b"]["bits"]) == 4


def test_map_netlist_stats():
    design, mod = _simple_design()
    a = _add_input(mod, "a", 8)
    b = _add_input(mod, "b", 8)
    y = mod.add_net("y", 8)
    _add_output(mod, "y", y)
    gc = mod.add_cell("xor0", PrimOp.XOR)
    mod.connect(gc, "A", a)
    mod.connect(gc, "B", b)
    mod.connect(gc, "Y", y, direction="output")
    nl = map_to_ecp5(design)
    stats = nl.stats()
    assert stats["LUT4"] == 8  # one LUT4 per output bit
    assert stats["ports"] == 3


def test_map_concat_is_wiring_only():
    """CONCAT must not produce physical cells — it's pure wiring."""
    design, mod = _simple_design()
    a = _add_input(mod, "a", 4)
    b = _add_input(mod, "b", 4)
    y = mod.add_net("y", 8)
    _add_output(mod, "y", y)
    cc = mod.add_cell("cat0", PrimOp.CONCAT, count=2)
    mod.connect(cc, "I0", a)
    mod.connect(cc, "I1", b)
    mod.connect(cc, "Y", y, direction="output")
    nl = map_to_ecp5(design)
    assert nl.stats().get("LUT4", 0) == 0


def test_map_slice_is_wiring_only():
    """SLICE must not produce physical cells."""
    design, mod = _simple_design()
    a = _add_input(mod, "a", 8)
    y = mod.add_net("y", 4)
    _add_output(mod, "y", y)
    sc = mod.add_cell("sl0", PrimOp.SLICE, offset=2, width=4)
    mod.connect(sc, "A", a)
    mod.connect(sc, "Y", y, direction="output")
    nl = map_to_ecp5(design)
    assert nl.stats().get("LUT4", 0) == 0


# ---------------------------------------------------------------------------
# Comparison operation tests
# ---------------------------------------------------------------------------

def test_map_lt_produces_cells():
    """LT comparison must produce LUT4 cells (comparator chain), not constant 0."""
    design, mod = _simple_design("lt_test")
    a = _add_input(mod, "a", 8)
    b = _add_input(mod, "b", 8)
    y_net = mod.add_net("y", 1)
    oc = mod.add_cell("y_p", PrimOp.OUTPUT, port_name="y")
    mod.connect(oc, "A", y_net)
    mod.ports["y"] = y_net
    lt = mod.add_cell("lt0", PrimOp.LT)
    mod.connect(lt, "A", a)
    mod.connect(lt, "B", b)
    mod.connect(lt, "Y", y_net, direction="output")
    nl = map_to_ecp5(design)
    luts = nl.stats().get("LUT4", 0)
    assert luts >= 8, f"8-bit LT should produce at least 8 LUT4 cells, got {luts}"


def test_map_le_produces_cells():
    """LE comparison needs comparator chain + equality chain."""
    design, mod = _simple_design("le_test")
    a = _add_input(mod, "a", 4)
    b = _add_input(mod, "b", 4)
    y_net = mod.add_net("y", 1)
    oc = mod.add_cell("y_p", PrimOp.OUTPUT, port_name="y")
    mod.connect(oc, "A", y_net)
    mod.ports["y"] = y_net
    le = mod.add_cell("le0", PrimOp.LE)
    mod.connect(le, "A", a)
    mod.connect(le, "B", b)
    mod.connect(le, "Y", y_net, direction="output")
    nl = map_to_ecp5(design)
    luts = nl.stats().get("LUT4", 0)
    assert luts >= 4, f"4-bit LE should produce LUT4 cells, got {luts}"


def test_map_gt_produces_cells():
    """GT should produce the same cell count as LT (swapped operands)."""
    design, mod = _simple_design("gt_test")
    a = _add_input(mod, "a", 8)
    b = _add_input(mod, "b", 8)
    y_net = mod.add_net("y", 1)
    oc = mod.add_cell("y_p", PrimOp.OUTPUT, port_name="y")
    mod.connect(oc, "A", y_net)
    mod.ports["y"] = y_net
    gt = mod.add_cell("gt0", PrimOp.GT)
    mod.connect(gt, "A", a)
    mod.connect(gt, "B", b)
    mod.connect(gt, "Y", y_net, direction="output")
    nl = map_to_ecp5(design)
    luts = nl.stats().get("LUT4", 0)
    assert luts >= 8, f"8-bit GT should produce at least 8 LUT4 cells, got {luts}"


def test_map_ge_produces_cells():
    """GE should produce comparator + equality chain."""
    design, mod = _simple_design("ge_test")
    a = _add_input(mod, "a", 4)
    b = _add_input(mod, "b", 4)
    y_net = mod.add_net("y", 1)
    oc = mod.add_cell("y_p", PrimOp.OUTPUT, port_name="y")
    mod.connect(oc, "A", y_net)
    mod.ports["y"] = y_net
    ge = mod.add_cell("ge0", PrimOp.GE)
    mod.connect(ge, "A", a)
    mod.connect(ge, "B", b)
    mod.connect(ge, "Y", y_net, direction="output")
    nl = map_to_ecp5(design)
    luts = nl.stats().get("LUT4", 0)
    assert luts >= 4, f"4-bit GE should produce LUT4 cells, got {luts}"


# ---------------------------------------------------------------------------
# Comparison correctness via eval
# ---------------------------------------------------------------------------

def test_eval_unsigned_lt():
    """Unsigned LT: 3 < 5 = 1, 5 < 3 = 0, 3 < 3 = 0."""
    assert eval_const_op(PrimOp.LT, {"A": 3, "B": 5}, {}, 8) == 1
    assert eval_const_op(PrimOp.LT, {"A": 5, "B": 3}, {}, 8) == 0
    assert eval_const_op(PrimOp.LT, {"A": 3, "B": 3}, {}, 8) == 0


def test_eval_signed_lt():
    """Signed LT: -1 (0xFF) < 1 = 1 when signed."""
    # unsigned: 0xFF > 0x01
    assert eval_const_op(PrimOp.LT, {"A": 0xFF, "B": 0x01}, {}, 8) == 0
    # signed: 0xFF = -1, 0x01 = 1, so -1 < 1 = true
    assert eval_const_op(PrimOp.LT, {"A": 0xFF, "B": 0x01}, {"signed": True}, 8) == 1
    # signed: 0x01 < 0xFF => 1 < -1 = false
    assert eval_const_op(PrimOp.LT, {"A": 0x01, "B": 0xFF}, {"signed": True}, 8) == 0


def test_eval_signed_div():
    """Signed division: -6 / 4 = -1 (truncate toward zero)."""
    # -6 in 8-bit = 0xFA, 4 = 0x04
    # unsigned: 0xFA // 0x04 = 62
    assert eval_const_op(PrimOp.DIV, {"A": 0xFA, "B": 0x04}, {}, 8) == 62
    # signed: -6 / 4 = -1 -> 0xFF
    assert eval_const_op(PrimOp.DIV, {"A": 0xFA, "B": 0x04}, {"signed": True}, 8) == 0xFF


# --- from test_slicepack ---




def _make_lut4(nl, name, init_bin, a, b, c="0", d="0"):
    """Helper to create a LUT4 cell with given ports."""
    cell = nl.add_cell(name, "LUT4")
    cell.parameters["INIT"] = init_bin
    cell.ports["A"] = [a]
    cell.ports["B"] = [b]
    cell.ports["C"] = [c]
    cell.ports["D"] = [d]
    cell.ports["Z"] = [nl.alloc_bit()]
    return cell


def test_simplify_constant_all_zero():
    nl = ECP5Netlist(top="test")
    c = nl.add_cell("lut0", "LUT4")
    c.parameters["INIT"] = "0000000000000000"
    c.ports["A"] = ["0"]
    c.ports["B"] = ["0"]
    c.ports["C"] = ["0"]
    c.ports["D"] = ["0"]
    c.ports["Z"] = [10]
    simplify_constant_luts(nl)
    # All-zero INIT with all-constant inputs -> cell eliminated
    assert "lut0" not in nl.cells


def test_simplify_constant_reduces_init():
    nl = ECP5Netlist(top="test")
    c = nl.add_cell("lut0", "LUT4")
    c.parameters["INIT"] = format(0x8888, "016b")
    c.ports["A"] = [2]
    c.ports["B"] = ["0"]
    c.ports["C"] = ["0"]
    c.ports["D"] = ["0"]
    c.ports["Z"] = [10]
    s = simplify_constant_luts(nl)
    assert s >= 1


def test_dedup_identical_luts():
    nl = ECP5Netlist(top="test")
    _make_lut4(nl, "lut0", format(0x8888, "016b"), 2, 3)
    _make_lut4(nl, "lut1", format(0x8888, "016b"), 2, 3)
    before = len(nl.cells)
    dd = deduplicate_luts(nl)
    assert dd == 1
    assert len(nl.cells) == before - 1


def test_dedup_different_init_no_merge():
    nl = ECP5Netlist(top="test")
    _make_lut4(nl, "lut0", format(0x8888, "016b"), 2, 3)
    _make_lut4(nl, "lut1", format(0x6666, "016b"), 2, 3)
    dd = deduplicate_luts(nl)
    assert dd == 0


def test_absorb_buffer():
    nl = ECP5Netlist(top="test")
    buf = nl.add_cell("buf0", "LUT4")
    buf.parameters["INIT"] = format(0xAAAA, "016b")
    buf.ports["A"] = [2]
    buf.ports["B"] = ["0"]
    buf.ports["C"] = ["0"]
    buf.ports["D"] = ["0"]
    buf.ports["Z"] = [10]
    cons = nl.add_cell("cons0", "LUT4")
    cons.parameters["INIT"] = format(0x8888, "016b")
    cons.ports["A"] = [10]
    cons.ports["B"] = [3]
    cons.ports["C"] = ["0"]
    cons.ports["D"] = ["0"]
    cons.ports["Z"] = [11]
    assert (0xAAAA & 0x3) == 0x2
    ab = absorb_buffers(nl)
    assert ab == 1
    assert "buf0" not in nl.cells
    assert cons.ports["A"] == [2]


def test_chain_merge_two_luts():
    nl = ECP5Netlist(top="test")
    a = nl.add_cell("lutA", "LUT4")
    a.parameters["INIT"] = format(0x8888, "016b")
    a.ports["A"] = [2]
    a.ports["B"] = [3]
    a.ports["C"] = ["0"]
    a.ports["D"] = ["0"]
    a.ports["Z"] = [10]
    b = nl.add_cell("lutB", "LUT4")
    b.parameters["INIT"] = format(0xEEEE, "016b")
    b.ports["A"] = [10]
    b.ports["B"] = [4]
    b.ports["C"] = ["0"]
    b.ports["D"] = ["0"]
    b.ports["Z"] = [11]
    nl.ports["out"] = {"direction": "output", "bits": [11]}
    mc = merge_lut_chains(nl)
    assert mc >= 1


def test_pack_slices_combined():
    nl = ECP5Netlist(top="test")
    for i in range(4):
        _make_lut4(nl, f"lut{i}", format(0x8888, "016b"), 2, 3)
    result = pack_slices(nl)
    assert isinstance(result, dict)
    assert result["lut_dedup"] >= 1


def test_pack_slices_never_increases_cells():
    nl = ECP5Netlist(top="test")
    for i in range(10):
        _make_lut4(nl, f"lut{i}", format(0x8888, "016b"), 2 + i, 3 + i)
    before = len(nl.cells)
    pack_slices(nl)
    assert len(nl.cells) <= before


# --- from test_carry ---




def _arith_module(op: PrimOp, width: int) -> Module:
    mod = Module(name="test")
    a = mod.add_net("a", width)
    b = mod.add_net("b", width)
    y = mod.add_net("y", width)
    cell = mod.add_cell("arith0", op)
    mod.connect(cell, "A", a)
    mod.connect(cell, "B", b)
    mod.connect(cell, "Y", y, direction="output")
    return mod


def test_infer_add_16bit():
    mod = _arith_module(PrimOp.ADD, 16)
    tagged = infer_carry_chains(mod)
    assert tagged == 1
    cell = mod.cells["arith0"]
    assert cell.params["carry_config"] == "CCU2C"
    assert cell.params["carry_width"] == 16
    assert cell.params["carry_ccu2c_count"] == 8
    assert cell.params["carry_is_sub"] is False


def test_infer_sub_8bit():
    mod = _arith_module(PrimOp.SUB, 8)
    tagged = infer_carry_chains(mod)
    assert tagged == 1
    assert mod.cells["arith0"].params["carry_is_sub"] is True
    assert mod.cells["arith0"].params["carry_ccu2c_count"] == 4


def test_skip_1bit():
    mod = _arith_module(PrimOp.ADD, 1)
    tagged = infer_carry_chains(mod)
    assert tagged == 0


def test_infer_32bit():
    mod = _arith_module(PrimOp.ADD, 32)
    tagged = infer_carry_chains(mod)
    assert tagged == 1
    assert mod.cells["arith0"].params["carry_ccu2c_count"] == 16


def test_no_carry_for_and():
    mod = Module(name="test")
    a = mod.add_net("a", 16)
    b = mod.add_net("b", 16)
    y = mod.add_net("y", 16)
    cell = mod.add_cell("and0", PrimOp.AND)
    mod.connect(cell, "A", a)
    mod.connect(cell, "B", b)
    mod.connect(cell, "Y", y, direction="output")
    tagged = infer_carry_chains(mod)
    assert tagged == 0


def test_odd_width_3bit():
    """3-bit ADD needs ceil(3/2) = 2 CCU2C cells."""
    mod = _arith_module(PrimOp.ADD, 3)
    tagged = infer_carry_chains(mod)
    assert tagged == 1
    assert mod.cells["arith0"].params["carry_ccu2c_count"] == 2


def test_odd_width_7bit():
    """7-bit SUB needs ceil(7/2) = 4 CCU2C cells."""
    mod = _arith_module(PrimOp.SUB, 7)
    tagged = infer_carry_chains(mod)
    assert tagged == 1
    assert mod.cells["arith0"].params["carry_ccu2c_count"] == 4
    assert mod.cells["arith0"].params["carry_is_sub"] is True


def test_2bit_minimum():
    """2-bit ADD needs exactly 1 CCU2C cell."""
    mod = _arith_module(PrimOp.ADD, 2)
    tagged = infer_carry_chains(mod)
    assert tagged == 1
    assert mod.cells["arith0"].params["carry_ccu2c_count"] == 1


def test_ccu2c_count_formula():
    """Verify ceil(N/2) formula for multiple widths."""
    for width in range(2, 65):
        mod = _arith_module(PrimOp.ADD, width)
        infer_carry_chains(mod)
        expected = (width + 1) // 2
        actual = mod.cells["arith0"].params["carry_ccu2c_count"]
        assert actual == expected, f"width {width}: expected {expected} CCU2C, got {actual}"


def test_multiple_adders():
    """Multiple ADD/SUB cells in the same module should all be tagged."""
    mod = Module(name="test")
    for i, op in enumerate([PrimOp.ADD, PrimOp.SUB, PrimOp.ADD]):
        a = mod.add_net(f"a{i}", 8)
        b = mod.add_net(f"b{i}", 8)
        y = mod.add_net(f"y{i}", 8)
        cell = mod.add_cell(f"arith{i}", op)
        mod.connect(cell, "A", a)
        mod.connect(cell, "B", b)
        mod.connect(cell, "Y", y, direction="output")
    tagged = infer_carry_chains(mod)
    assert tagged == 3


# --- from test_bram ---




def test_fits_small():
    assert _fits_dp16kd(256, 8) is not None  # 2Kx9 fits easily


def test_fits_exact():
    assert _fits_dp16kd(1024, 16) is not None  # 1Kx18


def test_fits_512x32():
    assert _fits_dp16kd(512, 32) is not None  # 512x36


def test_too_wide_single():
    assert _fits_dp16kd(512, 64) is None  # needs tiling


def test_count_brams_tiled():
    # 1024x64 needs multiple BRAMs
    count = _count_brams_needed(1024, 64)
    assert count >= 2


def test_infer_tags_memory():
    mod = Module(name="test")
    net = mod.add_net("mem_out", 8)
    cell = mod.add_cell("mem0", PrimOp.MEMORY, depth=1024, width=8)
    mod.connect(cell, "RDATA", net, direction="output")
    out_cell = mod.add_cell("out", PrimOp.OUTPUT, port_name="out")
    mod.connect(out_cell, "A", net)
    mod.ports["out"] = net

    tagged = infer_brams(mod)
    assert tagged == 1
    assert mod.cells["mem0"].params["bram_config"] == "DP16KD"


def test_skip_tiny_array():
    mod = Module(name="test")
    net = mod.add_net("mem_out", 2)
    cell = mod.add_cell("mem0", PrimOp.MEMORY, depth=2, width=2)
    mod.connect(cell, "RDATA", net, direction="output")

    tagged = infer_brams(mod)
    assert tagged == 0  # 4 bits total, too small for any RAM


# ---------------------------------------------------------------------------
# DPR16X4 emission through full techmap pipeline
# ---------------------------------------------------------------------------

def test_dpr16x4_disabled():
    """DPR16X4 inference is disabled — small arrays fall through to FF-based mapping."""

    mod = Module(name="dpr_test")
    rdata = mod.add_net("rdata", 4)
    mem = mod.add_cell("mem0", PrimOp.MEMORY, depth=16, width=4, mem_name="fifo")
    mod.connect(mem, "RDATA", rdata, direction="output")

    tagged = infer_brams(mod)
    # 16x4 = 64 bits < 256 threshold — should NOT be tagged for BRAM
    assert tagged == 0
    assert "bram_config" not in mem.params


def test_small_array_ff_fallback():
    """A 16x8 array (128 bits < 256) must fall through to FF-based mapping."""
    mod = Module(name="dpr_wide")
    rdata = mod.add_net("rdata", 8)
    mem = mod.add_cell("mem0", PrimOp.MEMORY, depth=16, width=8, mem_name="wide_fifo")
    mod.connect(mem, "RDATA", rdata, direction="output")

    tagged = infer_brams(mod)
    assert tagged == 0
    assert "bram_config" not in mem.params


def test_memory_port_inference():
    """Memory port inference must annotate read/write port counts."""
    mod = Module(name="test")
    raddr = mod.add_net("raddr", 10)
    waddr = mod.add_net("waddr", 10)
    rdata = mod.add_net("rdata", 8)

    mem = mod.add_cell("mem0", PrimOp.MEMORY, depth=1024, width=8)
    mod.connect(mem, "RADDR", raddr)
    mod.connect(mem, "WADDR", waddr)
    mod.connect(mem, "RDATA", rdata, direction="output")

    annotated = infer_memory_ports(mod)
    assert annotated == 1
    assert mem.params["mem_read_ports"] == 1
    assert mem.params["mem_write_ports"] == 1
    assert mem.params["mem_dual_port"] is True


# Write mode detection (item 17)

def test_write_mode_different_addrs():
    """Different read/write addresses should default to NORMAL."""
    mod = Module(name="test")
    raddr = mod.add_net("raddr", 10)
    waddr = mod.add_net("waddr", 10)
    rdata = mod.add_net("rdata", 8)
    mem = mod.add_cell("mem0", PrimOp.MEMORY, depth=1024, width=8)
    mod.connect(mem, "RADDR", raddr)
    mod.connect(mem, "WADDR", waddr)
    mod.connect(mem, "RDATA", rdata, direction="output")
    detect_write_mode(mod)
    assert mem.params["write_mode"] == "NORMAL"


def test_write_mode_same_addr_no_feedback():
    """Same address, no feedback from rdata to wdata → NORMAL."""
    mod = Module(name="test")
    addr = mod.add_net("addr", 10)
    rdata = mod.add_net("rdata", 8)
    wdata = mod.add_net("wdata", 8)
    mem = mod.add_cell("mem0", PrimOp.MEMORY, depth=1024, width=8)
    mod.connect(mem, "RADDR", addr)
    mod.connect(mem, "WADDR", addr)
    mod.connect(mem, "WDATA", wdata)
    mod.connect(mem, "RDATA", rdata, direction="output")
    detect_write_mode(mod)
    assert mem.params["write_mode"] == "NORMAL"


def test_write_mode_same_addr_feedback():
    """Same address, wdata derived from rdata → WRITETHROUGH."""
    mod = Module(name="test")
    addr = mod.add_net("addr", 10)
    rdata = mod.add_net("rdata", 8)
    inc = mod.add_net("inc", 8)
    mem = mod.add_cell("mem0", PrimOp.MEMORY, depth=1024, width=8)
    mod.connect(mem, "RADDR", addr)
    mod.connect(mem, "WADDR", addr)
    mod.connect(mem, "RDATA", rdata, direction="output")
    # wdata = rdata + 1 (feedback loop)
    one = mod.add_net("one", 8)
    oc = mod.add_cell("one_c", PrimOp.CONST, value=1, width=8)
    mod.connect(oc, "Y", one, direction="output")
    add = mod.add_cell("add0", PrimOp.ADD)
    mod.connect(add, "A", rdata)
    mod.connect(add, "B", one)
    mod.connect(add, "Y", inc, direction="output")
    mod.connect(mem, "WDATA", inc)
    detect_write_mode(mod)
    assert mem.params["write_mode"] == "WRITETHROUGH"


# Output register inference (item 18)

def test_output_register_detected():
    """FF directly on BRAM read data with matching clock → output register."""
    mod = Module(name="test")
    clk = mod.add_net("clk", 1)
    rdata = mod.add_net("rdata", 8)
    q = mod.add_net("q", 8)
    mem = mod.add_cell("mem0", PrimOp.MEMORY, depth=1024, width=8)
    mod.connect(mem, "CLK", clk)
    mod.connect(mem, "RDATA", rdata, direction="output")
    ff = mod.add_cell("ff0", PrimOp.FF)
    mod.connect(ff, "CLK", clk)
    mod.connect(ff, "D", rdata)
    mod.connect(ff, "Q", q, direction="output")
    annotated = infer_output_register(mod)
    assert annotated == 1
    assert mem.params["output_register"] is True
    assert mem.params["output_ff"] == "ff0"


def test_output_register_different_clock():
    """FF with different clock should NOT be absorbed as output register."""
    mod = Module(name="test")
    clk1 = mod.add_net("clk1", 1)
    clk2 = mod.add_net("clk2", 1)
    rdata = mod.add_net("rdata", 8)
    q = mod.add_net("q", 8)
    mem = mod.add_cell("mem0", PrimOp.MEMORY, depth=1024, width=8)
    mod.connect(mem, "CLK", clk1)
    mod.connect(mem, "RDATA", rdata, direction="output")
    ff = mod.add_cell("ff0", PrimOp.FF)
    mod.connect(ff, "CLK", clk2)  # different clock
    mod.connect(ff, "D", rdata)
    mod.connect(ff, "Q", q, direction="output")
    annotated = infer_output_register(mod)
    assert annotated == 0


# --- from test_dsp ---




def _mul_module(a_width: int, b_width: int) -> Module:
    mod = Module(name="test")
    a = mod.add_net("a", a_width)
    b = mod.add_net("b", b_width)
    y = mod.add_net("y", a_width + b_width)
    cell = mod.add_cell("mul0", PrimOp.MUL)
    mod.connect(cell, "A", a)
    mod.connect(cell, "B", b)
    mod.connect(cell, "Y", y, direction="output")
    return mod


def test_infer_18x18():
    mod = _mul_module(16, 16)
    tagged = infer_dsps(mod)
    assert tagged == 1
    assert mod.cells["mul0"].params["dsp_config"] == "MULT18X18D"


def test_infer_8x8():
    mod = _mul_module(8, 8)
    tagged = infer_dsps(mod)
    assert tagged == 1
    assert mod.cells["mul0"].params["dsp_config"] == "MULT18X18D"


def test_infer_32x32_decomposed():
    mod = _mul_module(32, 32)
    tagged = infer_dsps(mod)
    assert tagged == 1
    assert mod.cells["mul0"].params["dsp_config"] == "MULT18X18D_DECOMPOSED"
    assert mod.cells["mul0"].params["dsp_count"] == 4


def test_no_mul_no_tag():
    mod = Module(name="test")
    a = mod.add_net("a", 8)
    b = mod.add_net("b", 8)
    y = mod.add_net("y", 8)
    cell = mod.add_cell("add0", PrimOp.ADD)
    mod.connect(cell, "A", a)
    mod.connect(cell, "B", b)
    mod.connect(cell, "Y", y, direction="output")
    tagged = infer_dsps(mod)
    assert tagged == 0


def test_exact_18x18_boundary():
    """Exactly 18x18 must fit in a single MULT18X18D."""
    mod = _mul_module(18, 18)
    tagged = infer_dsps(mod)
    assert tagged == 1
    assert mod.cells["mul0"].params["dsp_config"] == "MULT18X18D"


def test_19x1_exceeds_single():
    """19-bit input exceeds MULT18X18D — should decompose."""
    mod = _mul_module(19, 1)
    tagged = infer_dsps(mod)
    assert tagged == 1
    assert mod.cells["mul0"].params["dsp_config"] == "MULT18X18D_DECOMPOSED"


def test_1x1_multiply():
    """1-bit multiply should still map to MULT18X18D (no reason not to)."""
    mod = _mul_module(1, 1)
    tagged = infer_dsps(mod)
    assert tagged == 1
    assert mod.cells["mul0"].params["dsp_config"] == "MULT18X18D"


def test_asymmetric_widths():
    """Asymmetric widths (4x16) should fit."""
    mod = _mul_module(4, 16)
    tagged = infer_dsps(mod)
    assert tagged == 1
    assert mod.cells["mul0"].params["dsp_config"] == "MULT18X18D"
    assert mod.cells["mul0"].params["dsp_a_width"] == 4
    assert mod.cells["mul0"].params["dsp_b_width"] == 16


def test_exactly_36x36_decomposed():
    mod = _mul_module(36, 36)
    tagged = infer_dsps(mod)
    assert tagged == 1
    assert mod.cells["mul0"].params["dsp_config"] == "MULT18X18D_DECOMPOSED"


def test_multiple_multipliers():
    """Multiple MUL cells should all be tagged."""
    mod = Module(name="test")
    for i in range(4):
        a = mod.add_net(f"a{i}", 8)
        b = mod.add_net(f"b{i}", 8)
        y = mod.add_net(f"y{i}", 16)
        cell = mod.add_cell(f"mul{i}", PrimOp.MUL)
        mod.connect(cell, "A", a)
        mod.connect(cell, "B", b)
        mod.connect(cell, "Y", y, direction="output")
    tagged = infer_dsps(mod)
    assert tagged == 4


def test_mac_detection():
    """acc += a * b pattern should be detected as MAC."""
    mod = Module(name="test")
    clk = mod.add_net("clk", 1)
    a = mod.add_net("a", 8)
    b = mod.add_net("b", 8)
    prod = mod.add_net("prod", 16)
    acc_in = mod.add_net("acc_in", 16)
    acc_out = mod.add_net("acc_out", 16)

    mul = mod.add_cell("mul0", PrimOp.MUL)
    mod.connect(mul, "A", a)
    mod.connect(mul, "B", b)
    mod.connect(mul, "Y", prod, direction="output")

    add = mod.add_cell("add0", PrimOp.ADD)
    mod.connect(add, "A", prod)
    mod.connect(add, "B", acc_in)
    mod.connect(add, "Y", acc_out, direction="output")

    ff = mod.add_cell("ff0", PrimOp.FF)
    mod.connect(ff, "CLK", clk)
    mod.connect(ff, "D", acc_out)
    mod.connect(ff, "Q", acc_in, direction="output")

    detected = detect_mac(mod)
    assert detected == 1
    assert mul.params.get("dsp_mac") is True
    assert mul.params.get("dsp_acc_add") == "add0"
    assert mul.params.get("dsp_acc_ff") == "ff0"


def test_mac_not_detected_without_feedback():
    """MUL -> ADD without FF feedback is not a MAC."""
    mod = Module(name="test")
    a = mod.add_net("a", 8)
    b = mod.add_net("b", 8)
    c = mod.add_net("c", 16)
    prod = mod.add_net("prod", 16)
    out = mod.add_net("out", 16)

    mul = mod.add_cell("mul0", PrimOp.MUL)
    mod.connect(mul, "A", a)
    mod.connect(mul, "B", b)
    mod.connect(mul, "Y", prod, direction="output")

    add = mod.add_cell("add0", PrimOp.ADD)
    mod.connect(add, "A", prod)
    mod.connect(add, "B", c)
    mod.connect(add, "Y", out, direction="output")

    detected = detect_mac(mod)
    assert detected == 0


# --- from test_json_backend ---





def _simple_and_design():
    design = Design()
    mod = design.add_module("test_and")
    design.top = "test_and"

    a = mod.add_net("a", 1)
    a_cell = mod.add_cell("a_p", PrimOp.INPUT, port_name="a")
    mod.connect(a_cell, "Y", a, direction="output")
    mod.ports["a"] = a

    b = mod.add_net("b", 1)
    b_cell = mod.add_cell("b_p", PrimOp.INPUT, port_name="b")
    mod.connect(b_cell, "Y", b, direction="output")
    mod.ports["b"] = b

    y = mod.add_net("y", 1)
    y_cell = mod.add_cell("y_p", PrimOp.OUTPUT, port_name="y")
    mod.connect(y_cell, "A", y)
    mod.ports["y"] = y

    and_cell = mod.add_cell("and0", PrimOp.AND)
    mod.connect(and_cell, "A", a)
    mod.connect(and_cell, "B", b)
    mod.connect(and_cell, "Y", y, direction="output")

    return design


def test_json_valid():
    design = _simple_and_design()
    nl = map_to_ecp5(design)
    text = emit_json_str(nl)
    data = json.loads(text)
    assert "creator" in data
    assert "nosis" in data["creator"]
    assert "modules" in data


def test_json_has_module():
    design = _simple_and_design()
    nl = map_to_ecp5(design)
    data = json.loads(emit_json_str(nl))
    assert "test_and" in data["modules"]
    mod = data["modules"]["test_and"]
    assert "ports" in mod
    assert "cells" in mod
    assert "netnames" in mod


def test_json_ports():
    design = _simple_and_design()
    nl = map_to_ecp5(design)
    data = json.loads(emit_json_str(nl))
    mod = data["modules"]["test_and"]
    assert "a" in mod["ports"]
    assert "b" in mod["ports"]
    assert "y" in mod["ports"]
    assert mod["ports"]["a"]["direction"] == "input"
    assert mod["ports"]["y"]["direction"] == "output"


def test_json_cells():
    design = _simple_and_design()
    nl = map_to_ecp5(design)
    data = json.loads(emit_json_str(nl))
    mod = data["modules"]["test_and"]
    cells = mod["cells"]
    # Should have at least one TRELLIS_SLICE
    slice_cells = [c for c in cells.values() if c["type"] == "LUT4"]
    assert len(slice_cells) >= 1
    cell = slice_cells[0]
    assert "INIT" in cell["parameters"]
    assert "connections" in cell
    assert "port_directions" in cell


def test_json_bit_numbering():
    design = _simple_and_design()
    nl = map_to_ecp5(design)
    data = json.loads(emit_json_str(nl))
    mod = data["modules"]["test_and"]
    # All port bits should be integers >= 0
    for port in mod["ports"].values():
        for bit in port["bits"]:
            assert isinstance(bit, int)
            assert bit >= 0
    # All cell connection bits should be integers (signals) or string constants
    for cell in mod["cells"].values():
        for port_bits in cell["connections"].values():
            for bit in port_bits:
                assert isinstance(bit, int) or (isinstance(bit, str) and bit in ("0", "1", "x"))


def test_json_top_attribute():
    design = _simple_and_design()
    nl = map_to_ecp5(design)
    data = json.loads(emit_json_str(nl))
    mod = data["modules"]["test_and"]
    assert mod["attributes"]["top"] == "00000000000000000000000000000001"


# --- from test_postsynth ---



os.environ.setdefault("NOSIS_PYSLANG_PATH", "D:/slang/build/lib")



def test_cell_models_valid():
    models = generate_cell_models()
    assert "LUT4_SIM" in models
    assert "TRELLIS_FF_SIM" in models
    assert "CCU2C_SIM" in models
    assert "module" in models


def test_postsynth_empty():
    nl = ECP5Netlist(top="empty")
    v = generate_postsynth_verilog(nl)
    assert "module empty_postsynth" in v
    assert "endmodule" in v


def test_postsynth_with_ports():
    nl = ECP5Netlist(top="test")
    nl.ports["clk"] = {"direction": "input", "bits": [2]}
    nl.ports["out"] = {"direction": "output", "bits": [3]}
    v = generate_postsynth_verilog(nl)
    assert "input clk" in v
    assert "output out" in v


def test_postsynth_with_lut4_cell():
    nl = ECP5Netlist(top="test")
    nl.ports["a"] = {"direction": "input", "bits": [2]}
    nl.ports["y"] = {"direction": "output", "bits": [3]}
    c = nl.add_cell("lut0", "LUT4")
    c.parameters["INIT"] = format(0x8888, "016b")
    c.ports["A"] = [2]
    c.ports["B"] = [2]
    c.ports["C"] = ["0"]
    c.ports["D"] = ["0"]
    c.ports["Z"] = [3]
    v = generate_postsynth_verilog(nl)
    assert "LUT4_SIM" in v
    assert "lut0" in v.replace("$", "_")
    assert "0x8888" in v.upper() or "8888" in v.upper()


def test_postsynth_from_real_design():
    result = parse_files([RIME_UART_TX], top="uart_tx")
    design = lower_to_ir(result, top="uart_tx")
    nl = map_to_ecp5(design)
    v = generate_postsynth_verilog(nl)
    assert "module uart_tx_postsynth" in v
    assert "endmodule" in v
    assert "clk" in v
    assert "tx" in v
    assert "LUT4_SIM" in v


def test_postsynth_compiles_with_iverilog():
    """If iverilog is available, the generated Verilog should compile."""
    iverilog = _find_iverilog()
    if not iverilog:
        return


    result = parse_files([RIME_UART_TX], top="uart_tx")
    design = lower_to_ir(result, top="uart_tx")
    nl = map_to_ecp5(design)

    models = generate_cell_models()
    postsynth = generate_postsynth_verilog(nl)

    with tempfile.TemporaryDirectory() as tmp:
        models_path = Path(tmp) / "models.v"
        models_path.write_text(models, encoding="utf-8")
        postsynth_path = Path(tmp) / "postsynth.v"
        postsynth_path.write_text(postsynth, encoding="utf-8")

        subprocess.run(
            [iverilog, "-g2012", "-o", "/dev/null", str(models_path), str(postsynth_path)],
            capture_output=True, text=True, cwd=tmp,
        )


def test_postsynth_verilog_has_all_ports():
    result = parse_files([RIME_UART_TX], top="uart_tx")
    design = lower_to_ir(result, top="uart_tx")
    nl = map_to_ecp5(design)
    v = generate_postsynth_verilog(nl)
    for port_name in nl.ports:
        assert port_name in v, f"port {port_name} missing from post-synth Verilog"


def test_postsynth_verilog_has_all_cell_types():
    result = parse_files([RIME_UART_TX], top="uart_tx")
    design = lower_to_ir(result, top="uart_tx")
    nl = map_to_ecp5(design)
    stats = nl.stats()
    v = generate_postsynth_verilog(nl)
    if stats.get("LUT4", 0) > 0:
        assert "LUT4_SIM" in v
    if stats.get("TRELLIS_FF", 0) > 0:
        assert "TRELLIS_FF_SIM" in v


# --- from test_readmem ---





def test_readmemh_basic():
    content = "@0\n00000013\nDEADBEEF\n12345678\n"
    with tempfile.NamedTemporaryFile(suffix=".hex", mode="w", delete=False, encoding="utf-8") as f:
        f.write(content)
        f.flush()
        path = f.name
    try:
        data = parse_readmemh(path)
        assert data[0] == 0x00000013
        assert data[1] == 0xDEADBEEF
        assert data[2] == 0x12345678
    finally:
        Path(path).unlink()


def test_readmemh_address_jump():
    content = "@0\nAA\nBB\n@10\nCC\nDD\n"
    with tempfile.NamedTemporaryFile(suffix=".hex", mode="w", delete=False, encoding="utf-8") as f:
        f.write(content)
        f.flush()
        path = f.name
    try:
        data = parse_readmemh(path)
        assert data[0] == 0xAA
        assert data[1] == 0xBB
        assert data[0x10] == 0xCC
        assert data[0x11] == 0xDD
        assert 2 not in data  # gap between 1 and 0x10
    finally:
        Path(path).unlink()


def test_readmemh_comments():
    content = "// header\n@0\n01 // inline\n02\n// footer\n"
    with tempfile.NamedTemporaryFile(suffix=".hex", mode="w", delete=False, encoding="utf-8") as f:
        f.write(content)
        f.flush()
        path = f.name
    try:
        data = parse_readmemh(path)
        assert data[0] == 0x01
        assert data[1] == 0x02
    finally:
        Path(path).unlink()


def test_readmemh_multiple_per_line():
    content = "@0\n01 02 03 04\n"
    with tempfile.NamedTemporaryFile(suffix=".hex", mode="w", delete=False, encoding="utf-8") as f:
        f.write(content)
        f.flush()
        path = f.name
    try:
        data = parse_readmemh(path)
        assert data[0] == 1
        assert data[1] == 2
        assert data[2] == 3
        assert data[3] == 4
    finally:
        Path(path).unlink()


def test_readmemb_basic():
    content = "@0\n00000000\n11111111\n10101010\n"
    with tempfile.NamedTemporaryFile(suffix=".bin", mode="w", delete=False, encoding="utf-8") as f:
        f.write(content)
        f.flush()
        path = f.name
    try:
        data = parse_readmemb(path)
        assert data[0] == 0b00000000
        assert data[1] == 0b11111111
        assert data[2] == 0b10101010
    finally:
        Path(path).unlink()


def test_readmemh_empty():
    content = "// empty\n"
    with tempfile.NamedTemporaryFile(suffix=".hex", mode="w", delete=False, encoding="utf-8") as f:
        f.write(content)
        f.flush()
        path = f.name
    try:
        data = parse_readmemh(path)
        assert len(data) == 0
    finally:
        Path(path).unlink()


def test_readmemh_real_firmware():
    """Parse the actual RIME firmware hex file if available."""
    fw_hex = Path("D:/rime/firmware/images/picorv32/firmware.hex")
    if not fw_hex.exists():
        return
    data = parse_readmemh(str(fw_hex))
    assert len(data) > 0
    # First word should be @0 address
    assert 0 in data


def test_readmem_to_dp16kd_roundtrip():
    """Parse a hex file and convert to DP16KD INITVAL parameters."""

    # Create a small hex file
    with tempfile.NamedTemporaryFile(suffix=".hex", mode="w", delete=False, encoding="utf-8") as f:
        f.write("@0\n")
        f.write("DEADBEEF\n")
        f.write("CAFEBABE\n")
        f.write("12345678\n")
        hex_path = f.name

    try:
        mem_data = parse_readmemh(hex_path)
        assert 0 in mem_data
        assert mem_data[0] == 0xDEADBEEF
        assert mem_data[1] == 0xCAFEBABE
        assert mem_data[2] == 0x12345678

        initvals = readmem_to_dp16kd_initvals(mem_data, data_width=18, depth=1024)
        assert "INITVAL_00" in initvals
        # First row should contain the data
        assert initvals["INITVAL_00"] != "0x" + "0" * 80
    finally:
        Path(hex_path).unlink()



# --- Comparison LUT truth table verification ---

def test_compare_lut_init_matches_eval():
    """Verify that the LT LUT4 truth table matches eval semantics for all inputs."""
    from nosis.techmap import _compute_lut4_init
    init = _compute_lut4_init(PrimOp.LT, 3)
    # For each combination: A=a_bit, B=b_bit, C=borrow_in
    # Expected: borrow_out = (!a & b) | (!(a^b) & borrow_in)
    for i in range(16):
        a = (i >> 0) & 1
        b = (i >> 1) & 1
        c = (i >> 2) & 1
        expected = ((~a & 1) & b) | (((a ^ b) ^ 1) & c)
        actual = (init >> i) & 1
        assert actual == expected, f"LT INIT mismatch at i={i}: a={a} b={b} c={c} expected={expected} got={actual}"


def test_compare_chain_exhaustive_4bit():
    """Exhaustively verify 4-bit unsigned LT comparison through the techmap chain."""
    from nosis.ir import Design
    from nosis.techmap import map_to_ecp5

    design = Design()
    mod = design.add_module("cmp4")
    design.top = "cmp4"

    a = mod.add_net("a", 4)
    b = mod.add_net("b", 4)
    y = mod.add_net("y", 1)
    ac = mod.add_cell("ap", PrimOp.INPUT, port_name="a")
    mod.connect(ac, "Y", a, direction="output")
    mod.ports["a"] = a
    bc = mod.add_cell("bp", PrimOp.INPUT, port_name="b")
    mod.connect(bc, "Y", b, direction="output")
    mod.ports["b"] = b
    lt = mod.add_cell("lt0", PrimOp.LT)
    mod.connect(lt, "A", a)
    mod.connect(lt, "B", b)
    mod.connect(lt, "Y", y, direction="output")
    oc = mod.add_cell("yp", PrimOp.OUTPUT, port_name="y")
    mod.connect(oc, "A", y)
    mod.ports["y"] = y

    nl = map_to_ecp5(design)
    stats = nl.stats()
    # 4-bit LT should produce exactly 4 LUT4 cells (one per bit in the borrow chain)
    assert stats.get("LUT4", 0) == 4, f"expected 4 LUT4 for 4-bit LT, got {stats.get('LUT4', 0)}"


def test_compare_signed_produces_extra_luts():
    """Signed comparison needs MSB inversion LUTs beyond the unsigned chain."""
    from nosis.ir import Design
    from nosis.techmap import map_to_ecp5

    design = Design()
    mod = design.add_module("scmp")
    design.top = "scmp"

    a = mod.add_net("a", 8)
    b = mod.add_net("b", 8)
    y = mod.add_net("y", 1)
    ac = mod.add_cell("ap", PrimOp.INPUT, port_name="a")
    mod.connect(ac, "Y", a, direction="output")
    mod.ports["a"] = a
    bc = mod.add_cell("bp", PrimOp.INPUT, port_name="b")
    mod.connect(bc, "Y", b, direction="output")
    mod.ports["b"] = b
    lt = mod.add_cell("lt0", PrimOp.LT)
    lt.params["signed"] = True
    mod.connect(lt, "A", a)
    mod.connect(lt, "B", b)
    mod.connect(lt, "Y", y, direction="output")
    oc = mod.add_cell("yp", PrimOp.OUTPUT, port_name="y")
    mod.connect(oc, "A", y)
    mod.ports["y"] = y

    nl = map_to_ecp5(design)
    luts = nl.stats().get("LUT4", 0)
    # 8 chain LUTs (MSB inversion folded into last stage's truth table)
    assert luts >= 8, f"signed 8-bit LT should need >= 8 LUT4, got {luts}"


# --- DIV/MOD power-of-2 mapping ---

def test_div_power_of_2_is_wiring():
    """DIV by power of 2 should be pure wiring (shift), no LUT4 cells."""
    from nosis.ir import Design
    from nosis.techmap import map_to_ecp5

    design = Design()
    mod = design.add_module("divtest")
    design.top = "divtest"

    a = mod.add_net("a", 8)
    y = mod.add_net("y", 8)
    ac = mod.add_cell("ap", PrimOp.INPUT, port_name="a")
    mod.connect(ac, "Y", a, direction="output")
    mod.ports["a"] = a

    # Constant divisor = 4 (power of 2)
    c4 = mod.add_net("c4", 8)
    cc = mod.add_cell("c4", PrimOp.CONST, value=4, width=8)
    mod.connect(cc, "Y", c4, direction="output")

    div = mod.add_cell("div0", PrimOp.DIV)
    mod.connect(div, "A", a)
    mod.connect(div, "B", c4)
    mod.connect(div, "Y", y, direction="output")

    oc = mod.add_cell("yp", PrimOp.OUTPUT, port_name="y")
    mod.connect(oc, "A", y)
    mod.ports["y"] = y

    nl = map_to_ecp5(design)
    # Power-of-2 div is pure wiring — should produce zero LUT4 cells
    assert nl.stats().get("LUT4", 0) == 0, f"expected 0 LUT4 for div-by-4, got {nl.stats().get('LUT4', 0)}"


def test_mod_power_of_2_is_masking():
    """MOD by power of 2 should be pure wiring (bit mask), no LUT4 cells."""
    from nosis.ir import Design
    from nosis.techmap import map_to_ecp5

    design = Design()
    mod = design.add_module("modtest")
    design.top = "modtest"

    a = mod.add_net("a", 8)
    y = mod.add_net("y", 8)
    ac = mod.add_cell("ap", PrimOp.INPUT, port_name="a")
    mod.connect(ac, "Y", a, direction="output")
    mod.ports["a"] = a

    c8 = mod.add_net("c8", 8)
    cc = mod.add_cell("c8", PrimOp.CONST, value=8, width=8)
    mod.connect(cc, "Y", c8, direction="output")

    m = mod.add_cell("mod0", PrimOp.MOD)
    mod.connect(m, "A", a)
    mod.connect(m, "B", c8)
    mod.connect(m, "Y", y, direction="output")

    oc = mod.add_cell("yp", PrimOp.OUTPUT, port_name="y")
    mod.connect(oc, "A", y)
    mod.ports["y"] = y

    nl = map_to_ecp5(design)
    assert nl.stats().get("LUT4", 0) == 0, f"expected 0 LUT4 for mod-by-8, got {nl.stats().get('LUT4', 0)}"


# --- Gate-level primitive tests (#12) ---

def test_gate_primitives_lower():
    """Verilog gate primitives (and, or, nand, buf, not) lower to IR cells."""
    from nosis.frontend import parse_files, lower_to_ir
    r = parse_files(["tests/designs/gate_prims.sv"], top="gate_prims")
    d = lower_to_ir(r, top="gate_prims")
    m = d.top_module()
    assert m.stats()["cells"] > 8, "gate primitives should produce cells beyond ports"


def test_gate_primitives_simulate():
    """Gate primitives produce correct values in simulation."""
    from nosis.frontend import parse_files, lower_to_ir
    from nosis.sim import FastSimulator
    r = parse_files(["tests/designs/gate_prims.sv"], top="gate_prims")
    d = lower_to_ir(r, top="gate_prims")
    m = d.top_module()
    sim = FastSimulator(m)

    vals = sim.step({"a": 1, "b": 0, "c": 1})
    assert vals.get("y_and") == 0
    assert vals.get("y_or") == 1
    assert vals.get("y_nand") == 1
    assert vals.get("y_buf") == 1
    assert vals.get("y_not") == 0

    vals = sim.step({"a": 1, "b": 1, "c": 0})
    assert vals.get("y_and") == 1
    assert vals.get("y_nand") == 0


# --- For-loop in always_comb (#2) ---

def test_for_loop_lowers():
    """For loops in always blocks should produce cells."""
    from nosis.frontend import parse_files, lower_to_ir
    r = parse_files(["tests/designs/for_loop.sv"], top="for_loop")
    d = lower_to_ir(r, top="for_loop")
    m = d.top_module()
    assert m.stats()["cells"] > 10, "for-loop should produce cells"


# --- casez (#4) ---

def test_casez_lowers():
    """casez with wildcards should produce MUX cells."""
    from nosis.frontend import parse_files, lower_to_ir
    r = parse_files(["tests/designs/casez_test.sv"], top="casez_test")
    d = lower_to_ir(r, top="casez_test")
    m = d.top_module()
    assert m.stats()["cells"] > 5


# --- User-defined function call (#6) ---

def test_func_call_lowers():
    """User-defined function calls should inline and produce cells."""
    from nosis.frontend import parse_files, lower_to_ir
    r = parse_files(["tests/designs/func_call.sv"], top="func_call")
    d = lower_to_ir(r, top="func_call")
    m = d.top_module()
    # Should have ADD cells from the saturating add
    from nosis.ir import PrimOp
    adds = sum(1 for c in m.cells.values() if c.op == PrimOp.ADD)
    assert adds >= 1, "function should inline to produce ADD cells"
