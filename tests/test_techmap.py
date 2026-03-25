"""Tests for nosis.techmap — ECP5 technology mapping."""

from nosis.ir import Design, Module, PrimOp
from nosis.techmap import ECP5Netlist, map_to_ecp5


def _simple_design(name="test"):
    design = Design()
    mod = design.add_module(name)
    design.top = name
    return design, mod


def _add_input(mod, name, width):
    net = mod.add_net(name, width)
    cell = mod.add_cell(f"inp_{name}", PrimOp.INPUT, port_name=name)
    mod.connect(cell, "Y", net, direction="output")
    mod.ports[name] = net
    return net


def _add_output(mod, name, net):
    cell = mod.add_cell(f"out_{name}", PrimOp.OUTPUT, port_name=name)
    mod.connect(cell, "A", net)
    mod.ports[name] = net


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
    # Constants become tied bits — no TRELLIS_SLICE needed
    luts = [c for c in nl.cells.values() if c.cell_type == "LUT4"]
    assert len(luts) == 0


def test_map_ff_per_bit():
    design, mod = _simple_design()
    clk = _add_input(mod, "clk", 1)
    d = _add_input(mod, "d", 4)
    q = mod.add_net("q", 4)
    _add_output(mod, "q", q)
    ff = mod.add_cell("ff0", PrimOp.FF)
    mod.connect(ff, "CLK", clk)
    mod.connect(ff, "D", d)
    mod.connect(ff, "Q", q, direction="output")
    nl = map_to_ecp5(design)
    ff_cells = [c for c in nl.cells.values() if c.cell_type == "TRELLIS_FF"]
    assert len(ff_cells) == 4  # one FF per bit


def test_map_ff_has_clock_port():
    """Every TRELLIS_FF must have a CLK port connected."""
    design, mod = _simple_design()
    clk = _add_input(mod, "clk", 1)
    d = _add_input(mod, "d", 1)
    q = mod.add_net("q", 1)
    _add_output(mod, "q", q)
    ff = mod.add_cell("ff0", PrimOp.FF)
    mod.connect(ff, "CLK", clk)
    mod.connect(ff, "D", d)
    mod.connect(ff, "Q", q, direction="output")
    nl = map_to_ecp5(design)
    for cell in nl.cells.values():
        if cell.cell_type == "TRELLIS_FF":
            assert "CLK" in cell.ports, f"TRELLIS_FF {cell.name} missing CLK"
            assert "DI" in cell.ports, f"TRELLIS_FF {cell.name} missing DI"
            assert "Q" in cell.ports, f"TRELLIS_FF {cell.name} missing Q"


def test_map_and_init():
    design, mod = _simple_design()
    a = _add_input(mod, "a", 1)
    b = _add_input(mod, "b", 1)
    y = mod.add_net("y", 1)
    _add_output(mod, "y", y)
    gc = mod.add_cell("and0", PrimOp.AND)
    mod.connect(gc, "A", a)
    mod.connect(gc, "B", b)
    mod.connect(gc, "Y", y, direction="output")
    nl = map_to_ecp5(design)
    luts = [c for c in nl.cells.values() if c.cell_type == "LUT4"]
    assert len(luts) == 1
    assert luts[0].parameters["INIT"] == "1000100010001000"


def test_map_or_init():
    design, mod = _simple_design()
    a = _add_input(mod, "a", 1)
    b = _add_input(mod, "b", 1)
    y = mod.add_net("y", 1)
    _add_output(mod, "y", y)
    gc = mod.add_cell("or0", PrimOp.OR)
    mod.connect(gc, "A", a)
    mod.connect(gc, "B", b)
    mod.connect(gc, "Y", y, direction="output")
    nl = map_to_ecp5(design)
    luts = [c for c in nl.cells.values() if c.cell_type == "LUT4"]
    assert len(luts) == 1
    assert luts[0].parameters["INIT"] == "1110111011101110"


def test_map_xor_init():
    design, mod = _simple_design()
    a = _add_input(mod, "a", 1)
    b = _add_input(mod, "b", 1)
    y = mod.add_net("y", 1)
    _add_output(mod, "y", y)
    gc = mod.add_cell("xor0", PrimOp.XOR)
    mod.connect(gc, "A", a)
    mod.connect(gc, "B", b)
    mod.connect(gc, "Y", y, direction="output")
    nl = map_to_ecp5(design)
    luts = [c for c in nl.cells.values() if c.cell_type == "LUT4"]
    assert len(luts) == 1
    assert luts[0].parameters["INIT"] == "0110011001100110"


def test_map_not_init():
    design, mod = _simple_design()
    a = _add_input(mod, "a", 1)
    y = mod.add_net("y", 1)
    _add_output(mod, "y", y)
    gc = mod.add_cell("not0", PrimOp.NOT)
    mod.connect(gc, "A", a)
    mod.connect(gc, "Y", y, direction="output")
    nl = map_to_ecp5(design)
    luts = [c for c in nl.cells.values() if c.cell_type == "LUT4"]
    assert len(luts) == 1
    assert luts[0].parameters["INIT"] == "0101010101010101"


def test_map_mux_init():
    design, mod = _simple_design()
    s = _add_input(mod, "s", 1)
    a = _add_input(mod, "a", 1)
    b = _add_input(mod, "b", 1)
    y = mod.add_net("y", 1)
    _add_output(mod, "y", y)
    mc = mod.add_cell("mux0", PrimOp.MUX)
    mod.connect(mc, "S", s)
    mod.connect(mc, "A", a)
    mod.connect(mc, "B", b)
    mod.connect(mc, "Y", y, direction="output")
    nl = map_to_ecp5(design)
    luts = [c for c in nl.cells.values() if c.cell_type == "LUT4"]
    assert len(luts) == 1
    assert luts[0].parameters["INIT"] == "1110010011100100"


def test_map_multibit_produces_per_bit_luts():
    """An 8-bit XOR must produce 8 LUT4 cells."""
    design, mod = _simple_design()
    a = _add_input(mod, "a", 8)
    b = _add_input(mod, "b", 8)
    y = mod.add_net("y", 8)
    _add_output(mod, "y", y)
    gc = mod.add_cell("xor0", PrimOp.XOR)
    mod.connect(gc, "A", a)
    mod.connect(gc, "B", b)
    mod.connect(gc, "Y", y, direction="output")
    nl = map_to_ecp5(design)
    assert nl.stats()["LUT4"] == 8  # one LUT4 per output bit


def test_map_add_produces_ccu2c():
    """A 16-bit ADD must produce CCU2C cells."""
    design, mod = _simple_design()
    a = _add_input(mod, "a", 16)
    b = _add_input(mod, "b", 16)
    y = mod.add_net("y", 16)
    _add_output(mod, "y", y)
    gc = mod.add_cell("add0", PrimOp.ADD)
    mod.connect(gc, "A", a)
    mod.connect(gc, "B", b)
    mod.connect(gc, "Y", y, direction="output")
    nl = map_to_ecp5(design)
    ccu2c = [c for c in nl.cells.values() if c.cell_type == "CCU2C"]
    assert len(ccu2c) == 8  # 16 bits / 2 bits per CCU2C


def test_map_ports_direction():
    design, mod = _simple_design()
    a = _add_input(mod, "a", 4)
    b = mod.add_net("b", 4)
    _add_output(mod, "b", b)
    nl = map_to_ecp5(design)
    assert nl.ports["a"]["direction"] == "input"
    assert nl.ports["b"]["direction"] == "output"
    assert len(nl.ports["a"]["bits"]) == 4
    assert len(nl.ports["b"]["bits"]) == 4


def test_map_netlist_stats():
    design, mod = _simple_design()
    a = _add_input(mod, "a", 8)
    b = _add_input(mod, "b", 8)
    y = mod.add_net("y", 8)
    _add_output(mod, "y", y)
    gc = mod.add_cell("xor0", PrimOp.XOR)
    mod.connect(gc, "A", a)
    mod.connect(gc, "B", b)
    mod.connect(gc, "Y", y, direction="output")
    nl = map_to_ecp5(design)
    stats = nl.stats()
    assert stats["LUT4"] == 8  # one LUT4 per output bit
    assert stats["ports"] == 3


def test_map_concat_is_wiring_only():
    """CONCAT must not produce physical cells — it's pure wiring."""
    design, mod = _simple_design()
    a = _add_input(mod, "a", 4)
    b = _add_input(mod, "b", 4)
    y = mod.add_net("y", 8)
    _add_output(mod, "y", y)
    cc = mod.add_cell("cat0", PrimOp.CONCAT, count=2)
    mod.connect(cc, "I0", a)
    mod.connect(cc, "I1", b)
    mod.connect(cc, "Y", y, direction="output")
    nl = map_to_ecp5(design)
    assert nl.stats().get("LUT4", 0) == 0


def test_map_slice_is_wiring_only():
    """SLICE must not produce physical cells."""
    design, mod = _simple_design()
    a = _add_input(mod, "a", 8)
    y = mod.add_net("y", 4)
    _add_output(mod, "y", y)
    sc = mod.add_cell("sl0", PrimOp.SLICE, offset=2, width=4)
    mod.connect(sc, "A", a)
    mod.connect(sc, "Y", y, direction="output")
    nl = map_to_ecp5(design)
    assert nl.stats().get("LUT4", 0) == 0
