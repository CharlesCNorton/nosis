"""Tests for nosis.ir — intermediate representation."""

from nosis.ir import Cell, Design, Module, Net, PrimOp, emit_verilog


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


def test_eliminate_dead_modules():
    """Modules not reachable from top should be removed."""
    design = Design()
    design.add_module("top")
    design.add_module("unused")
    design.top = "top"
    removed = design.eliminate_dead_modules()
    assert "unused" in removed
    assert "top" in design.modules
    assert "unused" not in design.modules


def test_eliminate_dead_modules_no_top():
    """Without a top set, nothing should be removed."""
    design = Design()
    design.add_module("a")
    design.add_module("b")
    removed = design.eliminate_dead_modules()
    assert len(removed) == 0


def test_emit_verilog_basic():
    """emit_verilog should produce valid Verilog structure."""
    mod = Module(name="test")
    a = mod.add_net("a", 8)
    y = mod.add_net("y", 8)
    inp = mod.add_cell("a_p", PrimOp.INPUT, port_name="a")
    mod.connect(inp, "Y", a, direction="output")
    mod.ports["a"] = a
    out = mod.add_cell("y_p", PrimOp.OUTPUT, port_name="y")
    mod.connect(out, "A", y)
    mod.ports["y"] = y
    gc = mod.add_cell("not0", PrimOp.NOT)
    mod.connect(gc, "A", a)
    mod.connect(gc, "Y", y, direction="output")

    v = emit_verilog(mod)
    assert "module test" in v
    assert "endmodule" in v
    assert "input" in v
    assert "output" in v
    assert "assign" in v


def test_emit_verilog_ff():
    """emit_verilog should produce always blocks for FFs."""
    mod = Module(name="test")
    clk = mod.add_net("clk", 1)
    d = mod.add_net("d", 1)
    q = mod.add_net("q", 1)
    cc = mod.add_cell("clk_p", PrimOp.INPUT, port_name="clk")
    mod.connect(cc, "Y", clk, direction="output")
    mod.ports["clk"] = clk
    dc = mod.add_cell("d_p", PrimOp.INPUT, port_name="d")
    mod.connect(dc, "Y", d, direction="output")
    mod.ports["d"] = d
    oc = mod.add_cell("q_p", PrimOp.OUTPUT, port_name="q")
    mod.connect(oc, "A", q)
    mod.ports["q"] = q
    ff = mod.add_cell("ff0", PrimOp.FF)
    mod.connect(ff, "CLK", clk)
    mod.connect(ff, "D", d)
    mod.connect(ff, "Q", q, direction="output")

    v = emit_verilog(mod)
    assert "always" in v
    assert "posedge" in v
    assert "<=" in v


def test_emit_verilog_arithmetic():
    """emit_verilog should produce + and - for ADD and SUB."""
    mod = Module(name="test")
    a = mod.add_net("a", 8)
    b = mod.add_net("b", 8)
    s = mod.add_net("s", 8)
    ac = mod.add_cell("a_p", PrimOp.INPUT, port_name="a")
    mod.connect(ac, "Y", a, direction="output")
    mod.ports["a"] = a
    bc = mod.add_cell("b_p", PrimOp.INPUT, port_name="b")
    mod.connect(bc, "Y", b, direction="output")
    mod.ports["b"] = b
    oc = mod.add_cell("s_p", PrimOp.OUTPUT, port_name="s")
    mod.connect(oc, "A", s)
    mod.ports["s"] = s
    gc = mod.add_cell("add0", PrimOp.ADD)
    mod.connect(gc, "A", a)
    mod.connect(gc, "B", b)
    mod.connect(gc, "Y", s, direction="output")

    v = emit_verilog(mod)
    assert "+" in v
