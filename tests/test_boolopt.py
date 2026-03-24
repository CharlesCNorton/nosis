"""Tests for nosis.boolopt — Boolean algebraic optimization."""

from nosis.ir import Module, PrimOp
from nosis.boolopt import boolean_optimize, tech_aware_optimize


def test_idempotent_and():
    """a & a -> a"""
    mod = Module(name="test")
    a = mod.add_net("a", 1)
    y = mod.add_net("y", 1)
    cell = mod.add_cell("and0", PrimOp.AND)
    mod.connect(cell, "A", a)
    mod.connect(cell, "B", a)  # same net
    mod.connect(cell, "Y", y, direction="output")

    eliminated = boolean_optimize(mod)
    assert eliminated == 1
    assert "and0" not in mod.cells


def test_idempotent_or():
    """a | a -> a"""
    mod = Module(name="test")
    a = mod.add_net("a", 1)
    y = mod.add_net("y", 1)
    cell = mod.add_cell("or0", PrimOp.OR)
    mod.connect(cell, "A", a)
    mod.connect(cell, "B", a)
    mod.connect(cell, "Y", y, direction="output")

    eliminated = boolean_optimize(mod)
    assert eliminated == 1


def test_xor_self():
    """a ^ a -> 0"""
    mod = Module(name="test")
    a = mod.add_net("a", 8)
    y = mod.add_net("y", 8)
    cell = mod.add_cell("xor0", PrimOp.XOR)
    mod.connect(cell, "A", a)
    mod.connect(cell, "B", a)
    mod.connect(cell, "Y", y, direction="output")

    eliminated = boolean_optimize(mod)
    assert eliminated == 1
    assert mod.cells["xor0"].op == PrimOp.CONST
    assert mod.cells["xor0"].params["value"] == 0


def test_and_distribution():
    """(a & b) | (a & c) -> a & (b | c)"""
    mod = Module(name="test")
    a = mod.add_net("a", 1)
    b = mod.add_net("b", 1)
    c = mod.add_net("c", 1)
    ab = mod.add_net("ab", 1)
    ac = mod.add_net("ac", 1)
    y = mod.add_net("y", 1)

    and1 = mod.add_cell("and1", PrimOp.AND)
    mod.connect(and1, "A", a)
    mod.connect(and1, "B", b)
    mod.connect(and1, "Y", ab, direction="output")

    and2 = mod.add_cell("and2", PrimOp.AND)
    mod.connect(and2, "A", a)
    mod.connect(and2, "B", c)
    mod.connect(and2, "Y", ac, direction="output")

    or1 = mod.add_cell("or1", PrimOp.OR)
    mod.connect(or1, "A", ab)
    mod.connect(or1, "B", ac)
    mod.connect(or1, "Y", y, direction="output")

    eliminated = boolean_optimize(mod)
    assert eliminated >= 1  # and2 should be eliminated


def test_no_optimization_different_nets():
    """a & b where a != b should not be optimized."""
    mod = Module(name="test")
    a = mod.add_net("a", 1)
    b = mod.add_net("b", 1)
    y = mod.add_net("y", 1)
    cell = mod.add_cell("and0", PrimOp.AND)
    mod.connect(cell, "A", a)
    mod.connect(cell, "B", b)
    mod.connect(cell, "Y", y, direction="output")

    eliminated = boolean_optimize(mod)
    assert eliminated == 0


def test_tech_aware_double_not():
    """tech_aware_optimize should cancel double NOT."""
    mod = Module(name="test")
    a = mod.add_net("a", 1)
    na = mod.add_net("na", 1)
    nna = mod.add_net("nna", 1)

    not1 = mod.add_cell("not1", PrimOp.NOT)
    mod.connect(not1, "A", a)
    mod.connect(not1, "Y", na, direction="output")

    not2 = mod.add_cell("not2", PrimOp.NOT)
    mod.connect(not2, "A", na)
    mod.connect(not2, "Y", nna, direction="output")

    # Consumer of nna
    y = mod.add_net("y", 1)
    gc = mod.add_cell("and0", PrimOp.AND)
    mod.connect(gc, "A", nna)
    mod.connect(gc, "B", a)
    mod.connect(gc, "Y", y, direction="output")

    eliminated = tech_aware_optimize(mod)
    assert eliminated == 2  # both NOTs cancelled
    assert "not1" not in mod.cells
    assert "not2" not in mod.cells
    # and0 should now read from a directly
    assert mod.cells["and0"].inputs["A"] is a


def test_tech_aware_no_merge_exceeding_lut4():
    """tech_aware_optimize must not merge when combined inputs exceed LUT4."""
    mod = Module(name="test")
    nets = [mod.add_net(f"x{i}", 1) for i in range(5)]
    mid = mod.add_net("mid", 1)
    out = mod.add_net("out", 1)

    # 3-input function: (x0 & x1) | x2 -> needs 3 inputs
    c1 = mod.add_cell("and0", PrimOp.AND)
    mod.connect(c1, "A", nets[0])
    mod.connect(c1, "B", nets[1])
    mod.connect(c1, "Y", mid, direction="output")

    # Outer has 2 more inputs: mid & x3 -> total unique = {x0,x1,x3} = 3, fits LUT4
    c2 = mod.add_cell("and1", PrimOp.AND)
    mod.connect(c2, "A", mid)
    mod.connect(c2, "B", nets[3])
    mod.connect(c2, "Y", out, direction="output")

    eliminated = tech_aware_optimize(mod, lut_inputs=2)  # artificially low limit
    assert eliminated == 0  # 3 inputs > limit of 2
