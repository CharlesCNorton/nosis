"""Tests for nosis.cone — logic cone extraction."""

from nosis.ir import Module, PrimOp
from nosis.cone import extract_cone


def test_single_gate_cone():
    mod = Module(name="test")
    a = mod.add_net("a", 1)
    b = mod.add_net("b", 1)
    y = mod.add_net("y", 1)
    ac = mod.add_cell("a_p", PrimOp.INPUT, port_name="a")
    mod.connect(ac, "Y", a, direction="output")
    bc = mod.add_cell("b_p", PrimOp.INPUT, port_name="b")
    mod.connect(bc, "Y", b, direction="output")
    gc = mod.add_cell("and0", PrimOp.AND)
    mod.connect(gc, "A", a)
    mod.connect(gc, "B", b)
    mod.connect(gc, "Y", y, direction="output")

    cone = extract_cone(mod, "y")
    assert "and0" in cone.cells
    assert "a" in cone.nets
    assert "b" in cone.nets
    assert "y" in cone.nets


def test_cone_stops_at_ff():
    mod = Module(name="test")
    clk = mod.add_net("clk", 1)
    d = mod.add_net("d", 1)
    q = mod.add_net("q", 1)
    y = mod.add_net("y", 1)

    cc = mod.add_cell("clk_p", PrimOp.INPUT, port_name="clk")
    mod.connect(cc, "Y", clk, direction="output")
    dc = mod.add_cell("d_p", PrimOp.INPUT, port_name="d")
    mod.connect(dc, "Y", d, direction="output")
    ff = mod.add_cell("ff0", PrimOp.FF)
    mod.connect(ff, "CLK", clk)
    mod.connect(ff, "D", d)
    mod.connect(ff, "Q", q, direction="output")
    gc = mod.add_cell("not0", PrimOp.NOT)
    mod.connect(gc, "A", q)
    mod.connect(gc, "Y", y, direction="output")

    cone = extract_cone(mod, "y")
    assert "not0" in cone.cells
    assert "q" in cone.ports  # FF output is a cone boundary input
    assert "d" not in cone.nets  # d is behind the FF


def test_cone_unknown_net_raises():
    mod = Module(name="test")
    try:
        extract_cone(mod, "nonexistent")
        assert False
    except ValueError:
        pass


def test_cone_chain():
    mod = Module(name="test")
    a = mod.add_net("a", 1)
    ac = mod.add_cell("a_p", PrimOp.INPUT, port_name="a")
    mod.connect(ac, "Y", a, direction="output")

    prev = a
    for i in range(5):
        n = mod.add_net(f"n{i}", 1)
        c = mod.add_cell(f"not{i}", PrimOp.NOT)
        mod.connect(c, "A", prev)
        mod.connect(c, "Y", n, direction="output")
        prev = n

    cone = extract_cone(mod, "n4")
    # All 5 NOT cells should be in the cone
    for i in range(5):
        assert f"not{i}" in cone.cells


def test_cone_ignores_unrelated():
    mod = Module(name="test")
    a = mod.add_net("a", 1)
    b = mod.add_net("b", 1)
    y = mod.add_net("y", 1)
    z = mod.add_net("z", 1)

    ac = mod.add_cell("a_p", PrimOp.INPUT, port_name="a")
    mod.connect(ac, "Y", a, direction="output")
    bc = mod.add_cell("b_p", PrimOp.INPUT, port_name="b")
    mod.connect(bc, "Y", b, direction="output")

    gc = mod.add_cell("and0", PrimOp.AND)
    mod.connect(gc, "A", a)
    mod.connect(gc, "B", a)
    mod.connect(gc, "Y", y, direction="output")

    # z is unrelated to y
    gc2 = mod.add_cell("or0", PrimOp.OR)
    mod.connect(gc2, "A", b)
    mod.connect(gc2, "B", b)
    mod.connect(gc2, "Y", z, direction="output")

    cone = extract_cone(mod, "y")
    assert "and0" in cone.cells
    assert "or0" not in cone.cells  # unrelated to y
    assert "b" not in cone.nets
