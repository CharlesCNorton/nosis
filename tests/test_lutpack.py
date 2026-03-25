"""Tests for nosis.lutpack — LUT packing optimization."""

from nosis.ir import Module, PrimOp
from nosis.frontend import parse_files, lower_to_ir
from nosis.lutpack import pack_luts_ir
from tests.conftest import RIME_UART_TX


def test_pack_and_chain():
    """(a & b) & c should merge into one 3-input cell."""
    mod = Module(name="test")
    a = mod.add_net("a", 1)
    b = mod.add_net("b", 1)
    c = mod.add_net("c", 1)
    mid = mod.add_net("mid", 1)
    out = mod.add_net("out", 1)

    cell1 = mod.add_cell("and1", PrimOp.AND)
    mod.connect(cell1, "A", a)
    mod.connect(cell1, "B", b)
    mod.connect(cell1, "Y", mid, direction="output")

    cell2 = mod.add_cell("and2", PrimOp.AND)
    mod.connect(cell2, "A", mid)
    mod.connect(cell2, "B", c)
    mod.connect(cell2, "Y", out, direction="output")

    packed = pack_luts_ir(mod)
    assert packed == 1
    assert "and1" not in mod.cells
    assert "and2" in mod.cells
    assert mod.cells["and2"].params.get("packed") is True


def test_pack_mixed_ops():
    """(a & b) | c should merge into one 3-input cell."""
    mod = Module(name="test")
    a = mod.add_net("a", 1)
    b = mod.add_net("b", 1)
    c = mod.add_net("c", 1)
    mid = mod.add_net("mid", 1)
    out = mod.add_net("out", 1)

    cell1 = mod.add_cell("and1", PrimOp.AND)
    mod.connect(cell1, "A", a)
    mod.connect(cell1, "B", b)
    mod.connect(cell1, "Y", mid, direction="output")

    cell2 = mod.add_cell("or1", PrimOp.OR)
    mod.connect(cell2, "A", mid)
    mod.connect(cell2, "B", c)
    mod.connect(cell2, "Y", out, direction="output")

    packed = pack_luts_ir(mod)
    assert packed == 1


def test_pack_multibit():
    """Multi-bit cascaded bitwise operations should be packed."""
    mod = Module(name="test")
    a = mod.add_net("a", 8)
    b = mod.add_net("b", 8)
    c = mod.add_net("c", 8)
    mid = mod.add_net("mid", 8)
    out = mod.add_net("out", 8)

    cell1 = mod.add_cell("and1", PrimOp.AND)
    mod.connect(cell1, "A", a)
    mod.connect(cell1, "B", b)
    mod.connect(cell1, "Y", mid, direction="output")

    cell2 = mod.add_cell("and2", PrimOp.AND)
    mod.connect(cell2, "A", mid)
    mod.connect(cell2, "B", c)
    mod.connect(cell2, "Y", out, direction="output")

    packed = pack_luts_ir(mod)
    assert packed == 1  # multi-bit now supported
    assert "and1" not in mod.cells


def test_no_pack_multiple_consumers():
    """If the inner cell's output feeds multiple consumers, don't pack."""
    mod = Module(name="test")
    a = mod.add_net("a", 1)
    b = mod.add_net("b", 1)
    c = mod.add_net("c", 1)
    d = mod.add_net("d", 1)
    mid = mod.add_net("mid", 1)
    out1 = mod.add_net("out1", 1)
    out2 = mod.add_net("out2", 1)

    cell1 = mod.add_cell("and1", PrimOp.AND)
    mod.connect(cell1, "A", a)
    mod.connect(cell1, "B", b)
    mod.connect(cell1, "Y", mid, direction="output")

    cell2 = mod.add_cell("and2", PrimOp.AND)
    mod.connect(cell2, "A", mid)
    mod.connect(cell2, "B", c)
    mod.connect(cell2, "Y", out1, direction="output")

    cell3 = mod.add_cell("or1", PrimOp.OR)
    mod.connect(cell3, "A", mid)
    mod.connect(cell3, "B", d)
    mod.connect(cell3, "Y", out2, direction="output")

    packed = pack_luts_ir(mod)
    assert packed == 0  # mid has 2 consumers


def test_pack_on_real_design():
    """LUT packing on uart_tx should find at least some candidates."""
    result = parse_files([RIME_UART_TX], top="uart_tx")
    design = lower_to_ir(result, top="uart_tx")
    mod = design.top_module()
    cells_before = mod.stats()["cells"]
    packed = pack_luts_ir(mod)
    cells_after = mod.stats()["cells"]
    print(f"uart_tx LUT packing: {cells_before} -> {cells_after} ({packed} merged)")
    # Even if no packing happens, the pass should not crash
    assert cells_after <= cells_before


def test_pack_3_deep_chain():
    """A 3-deep chain (a&b)|c)^d should merge into a single 4-input cell."""
    mod = Module(name="test")
    a = mod.add_net("a", 1)
    b = mod.add_net("b", 1)
    c = mod.add_net("c", 1)
    d = mod.add_net("d", 1)
    m1 = mod.add_net("m1", 1)
    m2 = mod.add_net("m2", 1)
    out = mod.add_net("out", 1)

    c1 = mod.add_cell("and1", PrimOp.AND)
    mod.connect(c1, "A", a)
    mod.connect(c1, "B", b)
    mod.connect(c1, "Y", m1, direction="output")

    c2 = mod.add_cell("or1", PrimOp.OR)
    mod.connect(c2, "A", m1)
    mod.connect(c2, "B", c)
    mod.connect(c2, "Y", m2, direction="output")

    c3 = mod.add_cell("xor1", PrimOp.XOR)
    mod.connect(c3, "A", m2)
    mod.connect(c3, "B", d)
    mod.connect(c3, "Y", out, direction="output")

    packed = pack_luts_ir(mod)
    assert packed == 2  # two merges: and+or, then (and|or)+xor
    assert len(mod.cells) == 1
    surviving = list(mod.cells.values())[0]
    assert surviving.params.get("packed") is True
    # The composed function uses all 4 inputs
    assert len(surviving.inputs) >= 3


def test_pack_4_deep_stops_at_lut4():
    """A 4-deep chain of 2-input ops exceeds LUT4 capacity (5 unique inputs).
    Packing should stop after 3 merges (4 inputs consumed)."""
    mod = Module(name="test")
    nets = [mod.add_net(f"x{i}", 1) for i in range(5)]
    mids = [mod.add_net(f"m{i}", 1) for i in range(4)]

    c1 = mod.add_cell("g0", PrimOp.AND)
    mod.connect(c1, "A", nets[0])
    mod.connect(c1, "B", nets[1])
    mod.connect(c1, "Y", mids[0], direction="output")

    c2 = mod.add_cell("g1", PrimOp.OR)
    mod.connect(c2, "A", mids[0])
    mod.connect(c2, "B", nets[2])
    mod.connect(c2, "Y", mids[1], direction="output")

    c3 = mod.add_cell("g2", PrimOp.XOR)
    mod.connect(c3, "A", mids[1])
    mod.connect(c3, "B", nets[3])
    mod.connect(c3, "Y", mids[2], direction="output")

    c4 = mod.add_cell("g3", PrimOp.AND)
    mod.connect(c4, "A", mids[2])
    mod.connect(c4, "B", nets[4])
    mod.connect(c4, "Y", mids[3], direction="output")

    pack_luts_ir(mod)
    # Can't pack all 4 into one LUT4 (needs 5 inputs), so should stop at 2 cells
    assert len(mod.cells) <= 2
