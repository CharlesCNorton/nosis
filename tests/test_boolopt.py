"""Tests for nosis.boolopt — Boolean algebraic optimization."""

from nosis.ir import Module, PrimOp
from nosis.boolopt import boolean_optimize


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
