"""Tests for nosis.passes — constant folding and dead code elimination."""

from nosis.ir import Design, Module, PrimOp
from nosis.passes import constant_fold, dead_code_eliminate, run_default_passes


def _make_const(mod, name, value, width):
    net = mod.add_net(name, width)
    cell = mod.add_cell(f"c_{name}", PrimOp.CONST, value=value, width=width)
    mod.connect(cell, "Y", net, direction="output")
    return net


def test_const_fold_and():
    mod = Module(name="test")
    a = _make_const(mod, "a", 0xFF, 8)
    b = _make_const(mod, "b", 0x0F, 8)
    out = mod.add_net("out", 8)
    cell = mod.add_cell("and0", PrimOp.AND)
    mod.connect(cell, "A", a)
    mod.connect(cell, "B", b)
    mod.connect(cell, "Y", out, direction="output")

    folded = constant_fold(mod)
    assert folded == 1
    assert mod.cells["and0"].op == PrimOp.CONST
    assert mod.cells["and0"].params["value"] == 0x0F


def test_const_fold_add():
    mod = Module(name="test")
    a = _make_const(mod, "a", 100, 16)
    b = _make_const(mod, "b", 200, 16)
    out = mod.add_net("out", 16)
    cell = mod.add_cell("add0", PrimOp.ADD)
    mod.connect(cell, "A", a)
    mod.connect(cell, "B", b)
    mod.connect(cell, "Y", out, direction="output")

    folded = constant_fold(mod)
    assert folded == 1
    assert mod.cells["add0"].params["value"] == 300


def test_const_fold_mux_const_sel():
    mod = Module(name="test")
    sel = _make_const(mod, "sel", 1, 1)
    a = _make_const(mod, "a", 42, 8)
    b = _make_const(mod, "b", 99, 8)
    out = mod.add_net("out", 8)
    mux = mod.add_cell("mux0", PrimOp.MUX)
    mod.connect(mux, "S", sel)
    mod.connect(mux, "A", a)
    mod.connect(mux, "B", b)
    mod.connect(mux, "Y", out, direction="output")

    folded = constant_fold(mod)
    assert folded == 1
    assert mod.cells["mux0"].params["value"] == 99  # sel=1 -> B


def test_const_fold_cascaded():
    mod = Module(name="test")
    a = _make_const(mod, "a", 3, 8)
    b = _make_const(mod, "b", 5, 8)
    mid = mod.add_net("mid", 8)
    add = mod.add_cell("add0", PrimOp.ADD)
    mod.connect(add, "A", a)
    mod.connect(add, "B", b)
    mod.connect(add, "Y", mid, direction="output")

    c = _make_const(mod, "c", 10, 8)
    out = mod.add_net("out", 8)
    mul = mod.add_cell("mul0", PrimOp.MUL)
    mod.connect(mul, "A", mid)
    mod.connect(mul, "B", c)
    mod.connect(mul, "Y", out, direction="output")

    folded = constant_fold(mod)
    assert folded == 2  # both add and mul folded
    assert mod.cells["mul0"].params["value"] == 80  # (3+5)*10


def test_dce_removes_dead():
    mod = Module(name="test")
    # Live path: input -> output
    inp = mod.add_net("inp", 1)
    inp_cell = mod.add_cell("inp_port", PrimOp.INPUT, port_name="inp")
    mod.connect(inp_cell, "Y", inp, direction="output")
    mod.ports["inp"] = inp

    out = mod.add_net("out", 1)
    out_cell = mod.add_cell("out_port", PrimOp.OUTPUT, port_name="out")
    mod.connect(out_cell, "A", inp)
    mod.ports["out"] = out

    # Dead path: constant -> nowhere
    dead_net = mod.add_net("dead", 8)
    dead_cell = mod.add_cell("dead_const", PrimOp.CONST, value=0, width=8)
    mod.connect(dead_cell, "Y", dead_net, direction="output")

    removed = dead_code_eliminate(mod)
    assert removed >= 1
    assert "dead_const" not in mod.cells


def test_dce_keeps_ff():
    mod = Module(name="test")
    clk = mod.add_net("clk", 1)
    clk_cell = mod.add_cell("clk_port", PrimOp.INPUT, port_name="clk")
    mod.connect(clk_cell, "Y", clk, direction="output")
    mod.ports["clk"] = clk

    d = mod.add_net("d", 1)
    d_cell = mod.add_cell("d_port", PrimOp.INPUT, port_name="d")
    mod.connect(d_cell, "Y", d, direction="output")
    mod.ports["d"] = d

    q = mod.add_net("q", 1)
    q_cell = mod.add_cell("q_port", PrimOp.OUTPUT, port_name="q")
    mod.connect(q_cell, "A", q)
    mod.ports["q"] = q

    ff = mod.add_cell("ff0", PrimOp.FF)
    mod.connect(ff, "CLK", clk)
    mod.connect(ff, "D", d)
    mod.connect(ff, "Q", q, direction="output")

    removed = dead_code_eliminate(mod)
    assert "ff0" in mod.cells


def test_run_default_passes():
    mod = Module(name="test")
    a = _make_const(mod, "a", 7, 8)
    b = _make_const(mod, "b", 3, 8)
    out = mod.add_net("out", 8)
    out_cell = mod.add_cell("out_port", PrimOp.OUTPUT, port_name="out")
    mod.connect(out_cell, "A", out)
    mod.ports["out"] = out

    cell = mod.add_cell("add0", PrimOp.ADD)
    mod.connect(cell, "A", a)
    mod.connect(cell, "B", b)
    mod.connect(cell, "Y", out, direction="output")

    stats = run_default_passes(mod)
    assert stats["const_fold"] >= 1
