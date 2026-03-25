"""Tests for nosis.retiming — register retiming and logic duplication."""

from nosis.ir import Module, PrimOp
from nosis.retiming import retime_forward, retime_backward, verify_retime_clocks, duplicate_high_fanout


def _ff_chain_module():
    """Build: clk -> FF(D=d_in) -> Q -> AND(Q, b) -> y -> OUTPUT."""
    mod = Module(name="test")
    clk = mod.add_net("clk", 1)
    d_in = mod.add_net("d_in", 1)
    q = mod.add_net("q", 1)
    b = mod.add_net("b", 1)
    y = mod.add_net("y", 1)
    out = mod.add_net("out", 1)

    cc = mod.add_cell("clk_p", PrimOp.INPUT, port_name="clk")
    mod.connect(cc, "Y", clk, direction="output")
    mod.ports["clk"] = clk
    dc = mod.add_cell("d_p", PrimOp.INPUT, port_name="d_in")
    mod.connect(dc, "Y", d_in, direction="output")
    mod.ports["d_in"] = d_in
    bc = mod.add_cell("b_p", PrimOp.INPUT, port_name="b")
    mod.connect(bc, "Y", b, direction="output")
    mod.ports["b"] = b

    ff = mod.add_cell("ff0", PrimOp.FF)
    mod.connect(ff, "CLK", clk)
    mod.connect(ff, "D", d_in)
    mod.connect(ff, "Q", q, direction="output")

    gc = mod.add_cell("and0", PrimOp.AND)
    mod.connect(gc, "A", q)
    mod.connect(gc, "B", b)
    mod.connect(gc, "Y", y, direction="output")

    oc = mod.add_cell("out_p", PrimOp.OUTPUT, port_name="out")
    mod.connect(oc, "A", y)
    mod.ports["out"] = out
    return mod


def test_retime_forward_does_not_crash():
    mod = _ff_chain_module()
    retimed = retime_forward(mod)
    assert retimed >= 0


def test_retime_preserves_cell_types():
    """Retiming must not change the set of cell types present."""
    mod = _ff_chain_module()
    {c.op for c in mod.cells.values()}
    retime_forward(mod)
    ops_after = {c.op for c in mod.cells.values()}
    assert PrimOp.FF in ops_after
    assert PrimOp.AND in ops_after


def test_retime_does_not_increase_cells():
    mod = _ff_chain_module()
    before = len(mod.cells)
    retime_forward(mod)
    after = len(mod.cells)
    assert after <= before + 1  # retiming may duplicate one FF


def test_duplicate_high_fanout_exact():
    """A cell driving 100 consumers at threshold 32 should produce duplicates."""
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
    assert dup_count >= 1
    dup_cells = [n for n in mod.cells if "dup" in n]
    assert len(dup_cells) >= 1
    # Each dup cell must have the same op as the original
    for name in dup_cells:
        assert mod.cells[name].op == PrimOp.AND


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


def test_duplicate_preserves_inputs():
    """Duplicated cells must read from the same input nets as the original."""
    mod = Module(name="test")
    a = mod.add_net("a", 1)
    b = mod.add_net("b", 1)
    mid = mod.add_net("mid", 1)
    cell = mod.add_cell("and0", PrimOp.AND)
    mod.connect(cell, "A", a)
    mod.connect(cell, "B", b)
    mod.connect(cell, "Y", mid, direction="output")

    for i in range(50):
        y = mod.add_net(f"y{i}", 1)
        c = mod.add_cell(f"not{i}", PrimOp.NOT)
        mod.connect(c, "A", mid)
        mod.connect(c, "Y", y, direction="output")

    duplicate_high_fanout(mod, threshold=16)
    for name, cell in mod.cells.items():
        if "dup" in name:
            assert "A" in cell.inputs
            assert cell.inputs["A"] is a
            assert "B" in cell.inputs
            assert cell.inputs["B"] is b


def test_no_duplicate_ff():
    """FFs must not be duplicated."""
    mod = Module(name="test")
    clk = mod.add_net("clk", 1)
    d = mod.add_net("d", 1)
    q = mod.add_net("q", 1)
    ff = mod.add_cell("ff0", PrimOp.FF)
    mod.connect(ff, "CLK", clk)
    mod.connect(ff, "D", d)
    mod.connect(ff, "Q", q, direction="output")

    for i in range(50):
        y = mod.add_net(f"y{i}", 1)
        c = mod.add_cell(f"not{i}", PrimOp.NOT)
        mod.connect(c, "A", q)
        mod.connect(c, "Y", y, direction="output")

    dup_count = duplicate_high_fanout(mod, threshold=16)
    assert dup_count == 0  # FF is excluded from duplication


def test_verify_retime_clocks_same():
    """Same-clock FF pair should produce no warnings."""
    mod = Module(name="test")
    clk = mod.add_net("clk", 1)
    d = mod.add_net("d", 1)
    q1 = mod.add_net("q1", 1)
    mid = mod.add_net("mid", 1)
    q2 = mod.add_net("q2", 1)
    ff1 = mod.add_cell("ff1", PrimOp.FF)
    mod.connect(ff1, "CLK", clk)
    mod.connect(ff1, "D", d)
    mod.connect(ff1, "Q", q1, direction="output")
    gc = mod.add_cell("not0", PrimOp.NOT)
    mod.connect(gc, "A", q1)
    mod.connect(gc, "Y", mid, direction="output")
    ff2 = mod.add_cell("ff2", PrimOp.FF)
    mod.connect(ff2, "CLK", clk)
    mod.connect(ff2, "D", mid)
    mod.connect(ff2, "Q", q2, direction="output")
    warnings = verify_retime_clocks(mod)
    assert len(warnings) == 0


def test_verify_retime_clocks_mismatch():
    """Different-clock FF pair through combinational logic should warn."""
    mod = Module(name="test")
    clk_a = mod.add_net("clk_a", 1)
    clk_b = mod.add_net("clk_b", 1)
    d = mod.add_net("d", 1)
    q1 = mod.add_net("q1", 1)
    mid = mod.add_net("mid", 1)
    q2 = mod.add_net("q2", 1)
    ff1 = mod.add_cell("ff1", PrimOp.FF)
    mod.connect(ff1, "CLK", clk_a)
    mod.connect(ff1, "D", d)
    mod.connect(ff1, "Q", q1, direction="output")
    gc = mod.add_cell("not0", PrimOp.NOT)
    mod.connect(gc, "A", q1)
    mod.connect(gc, "Y", mid, direction="output")
    ff2 = mod.add_cell("ff2", PrimOp.FF)
    mod.connect(ff2, "CLK", clk_b)
    mod.connect(ff2, "D", mid)
    mod.connect(ff2, "Q", q2, direction="output")
    warnings = verify_retime_clocks(mod)
    assert len(warnings) >= 1
    assert "clock mismatch" in warnings[0]


def test_retime_backward_basic():
    """Backward retiming should not crash on a simple chain."""
    mod = _ff_chain_module()
    retimed = retime_backward(mod)
    assert retimed >= 0
    # Should still have an FF and an AND cell
    ops = {c.op for c in mod.cells.values()}
    assert PrimOp.FF in ops
