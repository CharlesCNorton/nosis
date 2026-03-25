"""Tests for nosis.satconst — SAT-based constant proof."""

from nosis.ir import Module, PrimOp
from nosis.satconst import prove_constants_sat


def test_no_crash_on_empty():
    mod = Module(name="empty")
    assert prove_constants_sat(mod, {}) == {}


def test_proves_constant_and_with_zero():
    """AND(x, 0) = 0 for all x — provably constant."""
    mod = Module(name="test")
    x = mod.add_net("x", 1)
    xc = mod.add_cell("x_p", PrimOp.INPUT, port_name="x")
    mod.connect(xc, "Y", x, direction="output")
    mod.ports["x"] = x

    zero = mod.add_net("zero", 1)
    zc = mod.add_cell("zc", PrimOp.CONST, value=0, width=1)
    mod.connect(zc, "Y", zero, direction="output")

    y = mod.add_net("y", 1)
    gc = mod.add_cell("and0", PrimOp.AND)
    mod.connect(gc, "A", x)
    mod.connect(gc, "B", zero)
    mod.connect(gc, "Y", y, direction="output")

    proven = prove_constants_sat(mod, {"y": 0}, max_cone_inputs=16)
    assert "y" in proven
    assert proven["y"] == 0


def test_does_not_prove_variable_net():
    """AND(x, y) is NOT constant — SAT should find a counterexample."""
    mod = Module(name="test")
    x = mod.add_net("x", 1)
    xc = mod.add_cell("x_p", PrimOp.INPUT, port_name="x")
    mod.connect(xc, "Y", x, direction="output")
    mod.ports["x"] = x

    y = mod.add_net("y_in", 1)
    yc = mod.add_cell("y_p", PrimOp.INPUT, port_name="y_in")
    mod.connect(yc, "Y", y, direction="output")
    mod.ports["y_in"] = y

    z = mod.add_net("z", 1)
    gc = mod.add_cell("and0", PrimOp.AND)
    mod.connect(gc, "A", x)
    mod.connect(gc, "B", y)
    mod.connect(gc, "Y", z, direction="output")

    # Candidate claims z is always 0, but AND(1,1) = 1 — should NOT prove
    proven = prove_constants_sat(mod, {"z": 0}, max_cone_inputs=16)
    assert "z" not in proven


def test_skips_multibit_nets():
    """Only 1-bit nets are proved constant (current limitation)."""
    mod = Module(name="test")
    x = mod.add_net("x", 8)
    xc = mod.add_cell("x_p", PrimOp.INPUT, port_name="x")
    mod.connect(xc, "Y", x, direction="output")
    mod.ports["x"] = x

    proven = prove_constants_sat(mod, {"x": 0}, max_cone_inputs=16)
    assert "x" not in proven


def test_skips_ff_boundary():
    """Cones containing FF outputs should be skipped (unsound)."""
    mod = Module(name="test")
    clk = mod.add_net("clk", 1)
    cc = mod.add_cell("clk_p", PrimOp.INPUT, port_name="clk")
    mod.connect(cc, "Y", clk, direction="output")
    mod.ports["clk"] = clk

    d = mod.add_net("d", 1)
    dc = mod.add_cell("d_p", PrimOp.INPUT, port_name="d")
    mod.connect(dc, "Y", d, direction="output")
    mod.ports["d"] = d

    q = mod.add_net("q", 1)
    ff = mod.add_cell("ff0", PrimOp.FF)
    mod.connect(ff, "CLK", clk)
    mod.connect(ff, "D", d)
    mod.connect(ff, "Q", q, direction="output")

    # y depends on FF output
    y = mod.add_net("y", 1)
    gc = mod.add_cell("not0", PrimOp.NOT)
    mod.connect(gc, "A", q)
    mod.connect(gc, "Y", y, direction="output")

    proven = prove_constants_sat(mod, {"y": 0}, max_cone_inputs=16)
    assert "y" not in proven  # FF boundary -> skipped
