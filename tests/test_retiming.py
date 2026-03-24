"""Tests for nosis.retiming — register retiming and logic duplication."""

from nosis.ir import Module, PrimOp
from nosis.retiming import retime_forward, duplicate_high_fanout


def test_duplicate_high_fanout():
    """A cell driving 100 consumers should be duplicated."""
    mod = Module(name="test")
    a = mod.add_net("a", 1)
    b = mod.add_net("b", 1)
    mid = mod.add_net("mid", 1)
    cell = mod.add_cell("and0", PrimOp.AND)
    mod.connect(cell, "A", a)
    mod.connect(cell, "B", b)
    mod.connect(cell, "Y", mid, direction="output")

    for i in range(100):
        y = mod.add_net(f"y{i}", 1)
        c = mod.add_cell(f"not{i}", PrimOp.NOT)
        mod.connect(c, "A", mid)
        mod.connect(c, "Y", y, direction="output")

    dup_count = duplicate_high_fanout(mod, threshold=32)
    assert dup_count >= 1  # at least one duplication
    # The duplicated cells should exist
    dup_cells = [n for n in mod.cells if "dup" in n]
    assert len(dup_cells) >= 1


def test_no_duplicate_below_threshold():
    mod = Module(name="test")
    a = mod.add_net("a", 1)
    b = mod.add_net("b", 1)
    mid = mod.add_net("mid", 1)
    cell = mod.add_cell("and0", PrimOp.AND)
    mod.connect(cell, "A", a)
    mod.connect(cell, "B", b)
    mod.connect(cell, "Y", mid, direction="output")

    for i in range(10):
        y = mod.add_net(f"y{i}", 1)
        c = mod.add_cell(f"not{i}", PrimOp.NOT)
        mod.connect(c, "A", mid)
        mod.connect(c, "Y", y, direction="output")

    dup_count = duplicate_high_fanout(mod, threshold=32)
    assert dup_count == 0


def test_retime_forward_basic():
    """Retiming should not crash on a simple FF -> AND chain."""
    mod = Module(name="test")
    clk = mod.add_net("clk", 1)
    d = mod.add_net("d", 1)
    q = mod.add_net("q", 1)
    b = mod.add_net("b", 1)
    y = mod.add_net("y", 1)
    out = mod.add_net("out", 1)

    cc = mod.add_cell("clk_p", PrimOp.INPUT, port_name="clk")
    mod.connect(cc, "Y", clk, direction="output")
    dc = mod.add_cell("d_p", PrimOp.INPUT, port_name="d")
    mod.connect(dc, "Y", d, direction="output")
    bc = mod.add_cell("b_p", PrimOp.INPUT, port_name="b")
    mod.connect(bc, "Y", b, direction="output")

    ff = mod.add_cell("ff0", PrimOp.FF)
    mod.connect(ff, "CLK", clk)
    mod.connect(ff, "D", d)
    mod.connect(ff, "Q", q, direction="output")

    gc = mod.add_cell("and0", PrimOp.AND)
    mod.connect(gc, "A", q)
    mod.connect(gc, "B", b)
    mod.connect(gc, "Y", y, direction="output")

    oc = mod.add_cell("out_p", PrimOp.OUTPUT, port_name="out")
    mod.connect(oc, "A", y)
    mod.ports["out"] = out

    # Should not crash
    retimed = retime_forward(mod)
    # May or may not retime depending on single-consumer check
    assert retimed >= 0
