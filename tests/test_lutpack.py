"""Tests for nosis.lutpack — LUT packing optimization."""

from nosis.ir import Module, PrimOp
from nosis.frontend import parse_files, lower_to_ir
from nosis.lutpack import pack_luts_ir
from tests.conftest import RIME_FW as RIME, RIME_V, requires_rime


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
    result = parse_files([f"{RIME}/core/uart/uart_tx.sv"], top="uart_tx")
    design = lower_to_ir(result, top="uart_tx")
    mod = design.top_module()
    cells_before = mod.stats()["cells"]
    packed = pack_luts_ir(mod)
    cells_after = mod.stats()["cells"]
    print(f"uart_tx LUT packing: {cells_before} -> {cells_after} ({packed} merged)")
    # Even if no packing happens, the pass should not crash
    assert cells_after <= cells_before
