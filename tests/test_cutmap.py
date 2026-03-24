"""Tests for nosis.cutmap — cut-based technology mapping."""

from nosis.ir import Module, PrimOp
from nosis.cutmap import cut_map_luts


def test_absorb_chain_of_3():
    """A chain of 3 single-bit ops with ≤4 total inputs should merge."""
    mod = Module(name="test")
    a = mod.add_net("a", 1)
    b = mod.add_net("b", 1)
    c = mod.add_net("c", 1)
    m1 = mod.add_net("m1", 1)
    m2 = mod.add_net("m2", 1)
    out = mod.add_net("out", 1)

    # a & b -> m1
    c1 = mod.add_cell("and0", PrimOp.AND)
    mod.connect(c1, "A", a)
    mod.connect(c1, "B", b)
    mod.connect(c1, "Y", m1, direction="output")

    # m1 | c -> m2
    c2 = mod.add_cell("or0", PrimOp.OR)
    mod.connect(c2, "A", m1)
    mod.connect(c2, "B", c)
    mod.connect(c2, "Y", m2, direction="output")

    # ~m2 -> out
    c3 = mod.add_cell("not0", PrimOp.NOT)
    mod.connect(c3, "A", m2)
    mod.connect(c3, "Y", out, direction="output")

    # Add ports so DCE doesn't kill everything
    for name, net in [("a", a), ("b", b), ("c", c)]:
        inp = mod.add_cell(f"inp_{name}", PrimOp.INPUT, port_name=name)
        mod.connect(inp, "Y", net, direction="output")
        mod.ports[name] = net
    outp = mod.add_cell("out_p", PrimOp.OUTPUT, port_name="out")
    mod.connect(outp, "A", out)
    mod.ports["out"] = out

    absorbed = cut_map_luts(mod)
    # not0 should absorb and0 and or0 into a single 3-input function
    assert absorbed >= 1
    # The surviving cell should have a packed_lut_init
    surviving = [c for c in mod.cells.values()
                 if c.params.get("packed") and c.op != PrimOp.CONST]
    assert len(surviving) >= 1


def test_no_absorb_multi_fanout():
    """A cell whose output feeds multiple consumers must not be absorbed."""
    mod = Module(name="test")
    a = mod.add_net("a", 1)
    b = mod.add_net("b", 1)
    mid = mod.add_net("mid", 1)
    out1 = mod.add_net("out1", 1)
    out2 = mod.add_net("out2", 1)

    c1 = mod.add_cell("and0", PrimOp.AND)
    mod.connect(c1, "A", a)
    mod.connect(c1, "B", b)
    mod.connect(c1, "Y", mid, direction="output")

    c2 = mod.add_cell("not0", PrimOp.NOT)
    mod.connect(c2, "A", mid)
    mod.connect(c2, "Y", out1, direction="output")

    c3 = mod.add_cell("not1", PrimOp.NOT)
    mod.connect(c3, "A", mid)  # mid has 2 consumers
    mod.connect(c3, "Y", out2, direction="output")

    absorbed = cut_map_luts(mod)
    assert absorbed == 0  # and0 has 2 consumers, can't absorb


def test_no_absorb_wide_output():
    """Multi-bit cells should not be absorbed (only 1-bit functions)."""
    mod = Module(name="test")
    a = mod.add_net("a", 8)
    b = mod.add_net("b", 8)
    y = mod.add_net("y", 8)

    c1 = mod.add_cell("and0", PrimOp.AND)
    mod.connect(c1, "A", a)
    mod.connect(c1, "B", b)
    mod.connect(c1, "Y", y, direction="output")

    absorbed = cut_map_luts(mod)
    assert absorbed == 0


def test_cut_map_never_increases_cells():
    """cut_map_luts must never increase the cell count."""
    mod = Module(name="test")
    a = mod.add_net("a", 1)
    y = mod.add_net("y", 1)
    c = mod.add_cell("not0", PrimOp.NOT)
    mod.connect(c, "A", a)
    mod.connect(c, "Y", y, direction="output")

    before = len(mod.cells)
    cut_map_luts(mod)
    assert len(mod.cells) <= before
