"""Tests for nosis.equiv — equivalence checking."""

from nosis.ir import Module, PrimOp
from nosis.equiv import check_equivalence, check_equivalence_exhaustive, EquivalenceResult


def _and_module(name: str) -> Module:
    mod = Module(name=name)
    a = mod.add_net("a", 1)
    b = mod.add_net("b", 1)
    y = mod.add_net("y", 1)
    a_cell = mod.add_cell("a_p", PrimOp.INPUT, port_name="a")
    mod.connect(a_cell, "Y", a, direction="output")
    mod.ports["a"] = a
    b_cell = mod.add_cell("b_p", PrimOp.INPUT, port_name="b")
    mod.connect(b_cell, "Y", b, direction="output")
    mod.ports["b"] = b
    y_cell = mod.add_cell("y_p", PrimOp.OUTPUT, port_name="y")
    mod.connect(y_cell, "A", y)
    mod.ports["y"] = y
    and_cell = mod.add_cell("and0", PrimOp.AND)
    mod.connect(and_cell, "A", a)
    mod.connect(and_cell, "B", b)
    mod.connect(and_cell, "Y", y, direction="output")
    return mod


def _or_module(name: str) -> Module:
    """Build an OR gate — NOT equivalent to AND."""
    mod = Module(name=name)
    a = mod.add_net("a", 1)
    b = mod.add_net("b", 1)
    y = mod.add_net("y", 1)
    a_cell = mod.add_cell("a_p", PrimOp.INPUT, port_name="a")
    mod.connect(a_cell, "Y", a, direction="output")
    mod.ports["a"] = a
    b_cell = mod.add_cell("b_p", PrimOp.INPUT, port_name="b")
    mod.connect(b_cell, "Y", b, direction="output")
    mod.ports["b"] = b
    y_cell = mod.add_cell("y_p", PrimOp.OUTPUT, port_name="y")
    mod.connect(y_cell, "A", y)
    mod.ports["y"] = y
    or_cell = mod.add_cell("or0", PrimOp.OR)
    mod.connect(or_cell, "A", a)
    mod.connect(or_cell, "B", b)
    mod.connect(or_cell, "Y", y, direction="output")
    return mod


def _double_not_module(name: str) -> Module:
    """Build ~~a & ~~b — equivalent to a & b."""
    mod = Module(name=name)
    a = mod.add_net("a", 1)
    b = mod.add_net("b", 1)
    y = mod.add_net("y", 1)
    a_cell = mod.add_cell("a_p", PrimOp.INPUT, port_name="a")
    mod.connect(a_cell, "Y", a, direction="output")
    mod.ports["a"] = a
    b_cell = mod.add_cell("b_p", PrimOp.INPUT, port_name="b")
    mod.connect(b_cell, "Y", b, direction="output")
    mod.ports["b"] = b
    y_cell = mod.add_cell("y_p", PrimOp.OUTPUT, port_name="y")
    mod.connect(y_cell, "A", y)
    mod.ports["y"] = y
    # ~~a
    na = mod.add_net("na", 1)
    not_a = mod.add_cell("not_a", PrimOp.NOT)
    mod.connect(not_a, "A", a)
    mod.connect(not_a, "Y", na, direction="output")
    nna = mod.add_net("nna", 1)
    not_na = mod.add_cell("not_na", PrimOp.NOT)
    mod.connect(not_na, "A", na)
    mod.connect(not_na, "Y", nna, direction="output")
    # ~~b
    nb = mod.add_net("nb", 1)
    not_b = mod.add_cell("not_b", PrimOp.NOT)
    mod.connect(not_b, "A", b)
    mod.connect(not_b, "Y", nb, direction="output")
    nnb = mod.add_net("nnb", 1)
    not_nb = mod.add_cell("not_nb", PrimOp.NOT)
    mod.connect(not_nb, "A", nb)
    mod.connect(not_nb, "Y", nnb, direction="output")
    # ~~a & ~~b
    and_cell = mod.add_cell("and0", PrimOp.AND)
    mod.connect(and_cell, "A", nna)
    mod.connect(and_cell, "B", nnb)
    mod.connect(and_cell, "Y", y, direction="output")
    return mod


def test_equivalent_identical():
    a = _and_module("a")
    b = _and_module("b")
    result = check_equivalence_exhaustive(a, b)
    assert result.equivalent
    assert result.method == "exhaustive"


def test_not_equivalent():
    a = _and_module("a")
    b = _or_module("b")
    result = check_equivalence_exhaustive(a, b)
    assert not result.equivalent
    assert result.counterexample is not None


def test_equivalent_double_not():
    a = _and_module("a")
    b = _double_not_module("b")
    result = check_equivalence_exhaustive(a, b)
    assert result.equivalent


def test_check_equivalence_auto_method():
    a = _and_module("a")
    b = _and_module("b")
    result = check_equivalence(a, b)
    assert result.equivalent


def test_counterexample_values():
    a = _and_module("a")
    b = _or_module("b")
    result = check_equivalence(a, b)
    assert not result.equivalent
    ce = result.counterexample
    assert ce is not None
    # At least one input combination where AND != OR
    # For a=0, b=1: AND=0, OR=1
    # For a=1, b=0: AND=0, OR=1
    assert "a" in ce or "b" in ce
