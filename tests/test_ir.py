"""Tests for nosis.ir — intermediate representation."""

from nosis.ir import Cell, Design, Module, Net, PrimOp


def test_net_creation():
    net = Net(name="clk", width=1)
    assert net.name == "clk"
    assert net.width == 1
    assert net.driver is None


def test_cell_creation():
    cell = Cell(name="and0", op=PrimOp.AND)
    assert cell.op == PrimOp.AND
    assert cell.inputs == {}
    assert cell.outputs == {}


def test_module_add_net():
    mod = Module(name="top")
    net = mod.add_net("sig", 8)
    assert "sig" in mod.nets
    assert net.width == 8


def test_module_add_cell():
    mod = Module(name="top")
    cell = mod.add_cell("and0", PrimOp.AND)
    assert "and0" in mod.cells
    assert cell.op == PrimOp.AND


def test_module_connect():
    mod = Module(name="top")
    a = mod.add_net("a", 1)
    b = mod.add_net("b", 1)
    y = mod.add_net("y", 1)
    cell = mod.add_cell("and0", PrimOp.AND)
    mod.connect(cell, "A", a)
    mod.connect(cell, "B", b)
    mod.connect(cell, "Y", y, direction="output")
    assert cell.inputs["A"] is a
    assert cell.inputs["B"] is b
    assert cell.outputs["Y"] is y
    assert y.driver is cell


def test_module_duplicate_net_raises():
    mod = Module(name="top")
    mod.add_net("x", 1)
    try:
        mod.add_net("x", 1)
        assert False, "should have raised"
    except ValueError:
        pass


def test_module_stats():
    mod = Module(name="top")
    mod.add_net("a", 1)
    mod.add_net("b", 1)
    mod.add_cell("and0", PrimOp.AND)
    mod.add_cell("or0", PrimOp.OR)
    stats = mod.stats()
    assert stats["nets"] == 2
    assert stats["cells"] == 2
    assert stats["AND"] == 1
    assert stats["OR"] == 1


def test_design_top_module():
    design = Design()
    mod = design.add_module("top")
    design.top = "top"
    assert design.top_module() is mod


def test_design_single_module_default_top():
    design = Design()
    mod = design.add_module("only")
    assert design.top_module() is mod


def test_design_multiple_modules_no_top_raises():
    design = Design()
    design.add_module("a")
    design.add_module("b")
    try:
        design.top_module()
        assert False, "should have raised"
    except ValueError:
        pass


def test_primop_coverage():
    # Verify all PrimOps are distinct
    ops = list(PrimOp)
    assert len(ops) == len(set(ops))
    assert len(ops) >= 30
