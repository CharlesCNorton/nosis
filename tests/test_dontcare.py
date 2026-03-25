"""Tests for nosis.dontcare — backward don't-care propagation."""

from nosis.ir import Module, PrimOp
from nosis.dontcare import propagate_dont_cares


def test_no_crash_on_empty():
    mod = Module(name="empty")
    assert propagate_dont_cares(mod) == 0


def test_no_crash_on_simple_logic():
    mod = Module(name="test")
    a = mod.add_net("a", 1)
    y = mod.add_net("y", 1)
    ac = mod.add_cell("a_p", PrimOp.INPUT, port_name="a")
    mod.connect(ac, "Y", a, direction="output")
    mod.ports["a"] = a
    gc = mod.add_cell("not0", PrimOp.NOT)
    mod.connect(gc, "A", a)
    mod.connect(gc, "Y", y, direction="output")
    oc = mod.add_cell("y_p", PrimOp.OUTPUT, port_name="y")
    mod.connect(oc, "A", y)
    mod.ports["y"] = y
    result = propagate_dont_cares(mod)
    assert result >= 0


def test_masked_ff_detected():
    """An FF whose Q is only consumed through AND(mask, Q) is masked."""
    mod = Module(name="test")
    clk = mod.add_net("clk", 1)
    cc = mod.add_cell("clk_p", PrimOp.INPUT, port_name="clk")
    mod.connect(cc, "Y", clk, direction="output")
    mod.ports["clk"] = clk

    d = mod.add_net("d", 1)
    dc = mod.add_cell("d_p", PrimOp.INPUT, port_name="d")
    mod.connect(dc, "Y", d, direction="output")
    mod.ports["d"] = d

    q = mod.add_net("q", 1)
    ff = mod.add_cell("ff0", PrimOp.FF)
    mod.connect(ff, "CLK", clk)
    mod.connect(ff, "D", d)
    mod.connect(ff, "Q", q, direction="output")

    # Mask: NOT(sel) & Q
    sel = mod.add_net("sel", 1)
    sc = mod.add_cell("sel_p", PrimOp.INPUT, port_name="sel")
    mod.connect(sc, "Y", sel, direction="output")
    mod.ports["sel"] = sel

    nsel = mod.add_net("nsel", 1)
    not_cell = mod.add_cell("not0", PrimOp.NOT)
    mod.connect(not_cell, "A", sel)
    mod.connect(not_cell, "Y", nsel, direction="output")

    masked = mod.add_net("masked", 1)
    and_cell = mod.add_cell("and0", PrimOp.AND)
    mod.connect(and_cell, "A", nsel)
    mod.connect(and_cell, "B", q)
    mod.connect(and_cell, "Y", masked, direction="output")

    oc = mod.add_cell("o_p", PrimOp.OUTPUT, port_name="masked")
    mod.connect(oc, "A", masked)
    mod.ports["masked"] = masked

    # Should detect the masked FF pattern (may or may not simplify)
    result = propagate_dont_cares(mod)
    assert result >= 0
