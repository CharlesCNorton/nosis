"""Tests for nosis.techmap — ECP5 technology mapping."""

from nosis.ir import Design, Module, PrimOp
from nosis.techmap import ECP5Netlist, map_to_ecp5


def _simple_design(name="test"):
    design = Design()
    mod = design.add_module(name)
    design.top = name
    return design, mod


def test_map_const():
    design, mod = _simple_design()
    net = mod.add_net("c", 8)
    cell = mod.add_cell("c0", PrimOp.CONST, value=0xA5, width=8)
    mod.connect(cell, "Y", net, direction="output")
    out_net = mod.add_net("out", 8)
    out_cell = mod.add_cell("out_port", PrimOp.OUTPUT, port_name="out")
    mod.connect(out_cell, "A", net)
    mod.ports["out"] = out_net

    nl = map_to_ecp5(design)
    # Constants become tied bits, no physical cells needed for CONST
    assert nl.stats()["cells"] == 0 or all(
        c.cell_type != "TRELLIS_SLICE" for c in nl.cells.values()
        if "c0" in c.name
    )


def test_map_ff():
    design, mod = _simple_design()
    clk = mod.add_net("clk", 1)
    clk_cell = mod.add_cell("clk_p", PrimOp.INPUT, port_name="clk")
    mod.connect(clk_cell, "Y", clk, direction="output")
    mod.ports["clk"] = clk

    d = mod.add_net("d", 4)
    d_cell = mod.add_cell("d_p", PrimOp.INPUT, port_name="d")
    mod.connect(d_cell, "Y", d, direction="output")
    mod.ports["d"] = d

    q = mod.add_net("q", 4)
    q_cell = mod.add_cell("q_p", PrimOp.OUTPUT, port_name="q")
    mod.connect(q_cell, "A", q)
    mod.ports["q"] = q

    ff = mod.add_cell("ff0", PrimOp.FF)
    mod.connect(ff, "CLK", clk)
    mod.connect(ff, "D", d)
    mod.connect(ff, "Q", q, direction="output")

    nl = map_to_ecp5(design)
    ff_cells = [c for c in nl.cells.values() if c.cell_type == "TRELLIS_FF"]
    assert len(ff_cells) == 4  # one FF per bit


def test_map_and():
    design, mod = _simple_design()
    a = mod.add_net("a", 1)
    a_cell = mod.add_cell("a_p", PrimOp.INPUT, port_name="a")
    mod.connect(a_cell, "Y", a, direction="output")
    mod.ports["a"] = a

    b = mod.add_net("b", 1)
    b_cell = mod.add_cell("b_p", PrimOp.INPUT, port_name="b")
    mod.connect(b_cell, "Y", b, direction="output")
    mod.ports["b"] = b

    y = mod.add_net("y", 1)
    y_cell = mod.add_cell("y_p", PrimOp.OUTPUT, port_name="y")
    mod.connect(y_cell, "A", y)
    mod.ports["y"] = y

    and_cell = mod.add_cell("and0", PrimOp.AND)
    mod.connect(and_cell, "A", a)
    mod.connect(and_cell, "B", b)
    mod.connect(and_cell, "Y", y, direction="output")

    nl = map_to_ecp5(design)
    luts = [c for c in nl.cells.values() if c.cell_type == "TRELLIS_SLICE"]
    assert len(luts) == 1
    # AND truth table: A&B with C,D as don't-care -> 0x8888
    # Bit pattern: for each (D,C) combination, A&B=1 only when A=1,B=1 (bit 3)
    assert luts[0].parameters["LUT0_INITVAL"] == "0x8888"


def test_map_ports():
    design, mod = _simple_design()
    a = mod.add_net("a", 4)
    a_cell = mod.add_cell("a_p", PrimOp.INPUT, port_name="a")
    mod.connect(a_cell, "Y", a, direction="output")
    mod.ports["a"] = a

    b = mod.add_net("b", 4)
    b_cell = mod.add_cell("b_p", PrimOp.OUTPUT, port_name="b")
    mod.connect(b_cell, "A", b)
    mod.ports["b"] = b

    nl = map_to_ecp5(design)
    assert "a" in nl.ports
    assert "b" in nl.ports
    assert nl.ports["a"]["direction"] == "input"
    assert nl.ports["b"]["direction"] == "output"
    assert len(nl.ports["a"]["bits"]) == 4
    assert len(nl.ports["b"]["bits"]) == 4


def test_map_netlist_stats():
    design, mod = _simple_design()
    a = mod.add_net("a", 8)
    a_cell = mod.add_cell("a_p", PrimOp.INPUT, port_name="a")
    mod.connect(a_cell, "Y", a, direction="output")
    mod.ports["a"] = a

    b = mod.add_net("b", 8)
    b_cell = mod.add_cell("b_p", PrimOp.INPUT, port_name="b")
    mod.connect(b_cell, "Y", b, direction="output")
    mod.ports["b"] = b

    y = mod.add_net("y", 8)
    y_cell = mod.add_cell("y_p", PrimOp.OUTPUT, port_name="y")
    mod.connect(y_cell, "A", y)
    mod.ports["y"] = y

    xor_cell = mod.add_cell("xor0", PrimOp.XOR)
    mod.connect(xor_cell, "A", a)
    mod.connect(xor_cell, "B", b)
    mod.connect(xor_cell, "Y", y, direction="output")

    nl = map_to_ecp5(design)
    stats = nl.stats()
    assert stats["TRELLIS_SLICE"] == 8  # one LUT per bit
    assert stats["ports"] == 3
