"""Tests for nosis.passes — constant folding and dead code elimination."""

from nosis.ir import Design, Module, PrimOp
from nosis.passes import constant_fold, identity_simplify, dead_code_eliminate, run_default_passes


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
    assert stats.get("round_0", 0) >= 1


def test_identity_and_all_ones():
    """a & 0xFF (8-bit) -> a"""
    mod = Module(name="test")
    a = mod.add_net("a", 8)
    a_cell = mod.add_cell("a_p", PrimOp.INPUT, port_name="a")
    mod.connect(a_cell, "Y", a, direction="output")
    mod.ports["a"] = a

    ones = _make_const(mod, "ones", 0xFF, 8)
    out = mod.add_net("out", 8)
    and_cell = mod.add_cell("and0", PrimOp.AND)
    mod.connect(and_cell, "A", a)
    mod.connect(and_cell, "B", ones)
    mod.connect(and_cell, "Y", out, direction="output")

    simplified = identity_simplify(mod)
    assert simplified == 1


def test_identity_or_zero():
    """a | 0 -> a"""
    mod = Module(name="test")
    a = mod.add_net("a", 8)
    a_cell = mod.add_cell("a_p", PrimOp.INPUT, port_name="a")
    mod.connect(a_cell, "Y", a, direction="output")
    mod.ports["a"] = a

    zero = _make_const(mod, "zero", 0, 8)
    out = mod.add_net("out", 8)
    or_cell = mod.add_cell("or0", PrimOp.OR)
    mod.connect(or_cell, "A", a)
    mod.connect(or_cell, "B", zero)
    mod.connect(or_cell, "Y", out, direction="output")

    simplified = identity_simplify(mod)
    assert simplified == 1


def test_identity_xor_zero():
    """a ^ 0 -> a"""
    mod = Module(name="test")
    a = mod.add_net("a", 8)
    a_cell = mod.add_cell("a_p", PrimOp.INPUT, port_name="a")
    mod.connect(a_cell, "Y", a, direction="output")
    mod.ports["a"] = a

    zero = _make_const(mod, "zero", 0, 8)
    out = mod.add_net("out", 8)
    xor_cell = mod.add_cell("xor0", PrimOp.XOR)
    mod.connect(xor_cell, "A", a)
    mod.connect(xor_cell, "B", zero)
    mod.connect(xor_cell, "Y", out, direction="output")

    simplified = identity_simplify(mod)
    assert simplified == 1


def test_identity_mul_zero():
    """a * 0 -> 0"""
    mod = Module(name="test")
    a = mod.add_net("a", 8)
    a_cell = mod.add_cell("a_p", PrimOp.INPUT, port_name="a")
    mod.connect(a_cell, "Y", a, direction="output")
    mod.ports["a"] = a

    zero = _make_const(mod, "zero", 0, 8)
    out = mod.add_net("out", 8)
    mul_cell = mod.add_cell("mul0", PrimOp.MUL)
    mod.connect(mul_cell, "A", a)
    mod.connect(mul_cell, "B", zero)
    mod.connect(mul_cell, "Y", out, direction="output")

    simplified = identity_simplify(mod)
    assert simplified == 1
    assert mod.cells["mul0"].op == PrimOp.CONST
    assert mod.cells["mul0"].params["value"] == 0


def test_identity_add_zero():
    """a + 0 -> a"""
    mod = Module(name="test")
    a = mod.add_net("a", 8)
    a_cell = mod.add_cell("a_p", PrimOp.INPUT, port_name="a")
    mod.connect(a_cell, "Y", a, direction="output")
    mod.ports["a"] = a

    zero = _make_const(mod, "zero", 0, 8)
    out = mod.add_net("out", 8)
    add_cell = mod.add_cell("add0", PrimOp.ADD)
    mod.connect(add_cell, "A", a)
    mod.connect(add_cell, "B", zero)
    mod.connect(add_cell, "Y", out, direction="output")

    simplified = identity_simplify(mod)
    assert simplified == 1


def test_identity_double_not():
    """~~a -> a"""
    mod = Module(name="test")
    a = mod.add_net("a", 8)
    a_cell = mod.add_cell("a_p", PrimOp.INPUT, port_name="a")
    mod.connect(a_cell, "Y", a, direction="output")
    mod.ports["a"] = a

    na = mod.add_net("na", 8)
    not1 = mod.add_cell("not1", PrimOp.NOT)
    mod.connect(not1, "A", a)
    mod.connect(not1, "Y", na, direction="output")

    out = mod.add_net("out", 8)
    not2 = mod.add_cell("not2", PrimOp.NOT)
    mod.connect(not2, "A", na)
    mod.connect(not2, "Y", out, direction="output")

    simplified = identity_simplify(mod)
    assert simplified == 1
