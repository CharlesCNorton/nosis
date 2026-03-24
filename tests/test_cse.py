"""Tests for nosis.cse — common subexpression elimination."""

from nosis.ir import Module, PrimOp
from nosis.cse import eliminate_common_subexpressions


def test_eliminate_identical_and():
    mod = Module(name="test")
    a = mod.add_net("a", 8)
    b = mod.add_net("b", 8)
    y1 = mod.add_net("y1", 8)
    y2 = mod.add_net("y2", 8)

    c1 = mod.add_cell("and1", PrimOp.AND)
    mod.connect(c1, "A", a)
    mod.connect(c1, "B", b)
    mod.connect(c1, "Y", y1, direction="output")

    c2 = mod.add_cell("and2", PrimOp.AND)
    mod.connect(c2, "A", a)
    mod.connect(c2, "B", b)
    mod.connect(c2, "Y", y2, direction="output")

    eliminated = eliminate_common_subexpressions(mod)
    assert eliminated == 1
    assert len(mod.cells) == 1


def test_no_eliminate_different_ops():
    mod = Module(name="test")
    a = mod.add_net("a", 8)
    b = mod.add_net("b", 8)
    y1 = mod.add_net("y1", 8)
    y2 = mod.add_net("y2", 8)

    c1 = mod.add_cell("and1", PrimOp.AND)
    mod.connect(c1, "A", a)
    mod.connect(c1, "B", b)
    mod.connect(c1, "Y", y1, direction="output")

    c2 = mod.add_cell("or1", PrimOp.OR)
    mod.connect(c2, "A", a)
    mod.connect(c2, "B", b)
    mod.connect(c2, "Y", y2, direction="output")

    eliminated = eliminate_common_subexpressions(mod)
    assert eliminated == 0


def test_no_eliminate_different_inputs():
    mod = Module(name="test")
    a = mod.add_net("a", 8)
    b = mod.add_net("b", 8)
    c = mod.add_net("c", 8)
    y1 = mod.add_net("y1", 8)
    y2 = mod.add_net("y2", 8)

    c1 = mod.add_cell("and1", PrimOp.AND)
    mod.connect(c1, "A", a)
    mod.connect(c1, "B", b)
    mod.connect(c1, "Y", y1, direction="output")

    c2 = mod.add_cell("and2", PrimOp.AND)
    mod.connect(c2, "A", a)
    mod.connect(c2, "B", c)  # different input
    mod.connect(c2, "Y", y2, direction="output")

    eliminated = eliminate_common_subexpressions(mod)
    assert eliminated == 0


def test_no_eliminate_ff():
    """FF cells must never be deduplicated."""
    mod = Module(name="test")
    clk = mod.add_net("clk", 1)
    d = mod.add_net("d", 1)
    q1 = mod.add_net("q1", 1)
    q2 = mod.add_net("q2", 1)

    ff1 = mod.add_cell("ff1", PrimOp.FF)
    mod.connect(ff1, "CLK", clk)
    mod.connect(ff1, "D", d)
    mod.connect(ff1, "Q", q1, direction="output")

    ff2 = mod.add_cell("ff2", PrimOp.FF)
    mod.connect(ff2, "CLK", clk)
    mod.connect(ff2, "D", d)
    mod.connect(ff2, "Q", q2, direction="output")

    eliminated = eliminate_common_subexpressions(mod)
    assert eliminated == 0  # FFs are not CSE candidates


def test_consumer_redirect():
    """After CSE, consumers of the duplicate should use the original's output."""
    mod = Module(name="test")
    a = mod.add_net("a", 1)
    b = mod.add_net("b", 1)
    y1 = mod.add_net("y1", 1)
    y2 = mod.add_net("y2", 1)
    final = mod.add_net("final", 1)

    c1 = mod.add_cell("and1", PrimOp.AND)
    mod.connect(c1, "A", a)
    mod.connect(c1, "B", b)
    mod.connect(c1, "Y", y1, direction="output")

    c2 = mod.add_cell("and2", PrimOp.AND)
    mod.connect(c2, "A", a)
    mod.connect(c2, "B", b)
    mod.connect(c2, "Y", y2, direction="output")

    # Consumer uses y2 (the duplicate)
    c3 = mod.add_cell("not1", PrimOp.NOT)
    mod.connect(c3, "A", y2)
    mod.connect(c3, "Y", final, direction="output")

    eliminate_common_subexpressions(mod)
    # c3 should now use y1 (the original)
    assert mod.cells["not1"].inputs["A"] is y1
