"""Mutation tests — verify that intentional evaluation errors are caught.

For each PrimOp, we verify that computing the WRONG result would be
detected by the equivalence checker or by direct comparison. This
confirms that our test suite would catch a regression in eval_const_op.
"""

from nosis.ir import Module, PrimOp
from nosis.eval import eval_const_op
from nosis.equiv import check_equivalence_exhaustive


def _make_gate_module(name: str, op: PrimOp) -> Module:
    """Build a 1-bit module: a OP b -> y."""
    mod = Module(name=name)
    a = mod.add_net("a", 1)
    b = mod.add_net("b", 1)
    y = mod.add_net("y", 1)
    ac = mod.add_cell("a_p", PrimOp.INPUT, port_name="a")
    mod.connect(ac, "Y", a, direction="output")
    mod.ports["a"] = a
    bc = mod.add_cell("b_p", PrimOp.INPUT, port_name="b")
    mod.connect(bc, "Y", b, direction="output")
    mod.ports["b"] = b
    yc = mod.add_cell("y_p", PrimOp.OUTPUT, port_name="y")
    mod.connect(yc, "A", y)
    mod.ports["y"] = y
    cell = mod.add_cell("gate", op)
    mod.connect(cell, "A", a)
    mod.connect(cell, "B", b)
    mod.connect(cell, "Y", y, direction="output")
    return mod


def test_and_not_or():
    """AND and OR must be distinguishable by the equivalence checker."""
    r = check_equivalence_exhaustive(
        _make_gate_module("a", PrimOp.AND),
        _make_gate_module("b", PrimOp.OR),
    )
    assert not r.equivalent


def test_and_not_xor():
    r = check_equivalence_exhaustive(
        _make_gate_module("a", PrimOp.AND),
        _make_gate_module("b", PrimOp.XOR),
    )
    assert not r.equivalent


def test_or_not_xor():
    r = check_equivalence_exhaustive(
        _make_gate_module("a", PrimOp.OR),
        _make_gate_module("b", PrimOp.XOR),
    )
    assert not r.equivalent


def test_eq_not_ne():
    r = check_equivalence_exhaustive(
        _make_gate_module("a", PrimOp.EQ),
        _make_gate_module("b", PrimOp.NE),
    )
    assert not r.equivalent


def test_lt_not_ge():
    r = check_equivalence_exhaustive(
        _make_gate_module("a", PrimOp.LT),
        _make_gate_module("b", PrimOp.GE),
    )
    assert not r.equivalent


def test_le_not_gt():
    r = check_equivalence_exhaustive(
        _make_gate_module("a", PrimOp.LE),
        _make_gate_module("b", PrimOp.GT),
    )
    assert not r.equivalent


def test_add_not_sub():
    """ADD and SUB must produce different results for non-zero B."""
    # For 4-bit values, ADD and SUB differ on many inputs
    r = check_equivalence_exhaustive(
        _make_4bit_arith("a", PrimOp.ADD),
        _make_4bit_arith("b", PrimOp.SUB),
        max_input_bits=8,
    )
    assert not r.equivalent


def _make_4bit_arith(name: str, op: PrimOp) -> Module:
    mod = Module(name=name)
    a = mod.add_net("a", 4)
    b = mod.add_net("b", 4)
    y = mod.add_net("y", 4)
    ac = mod.add_cell("a_p", PrimOp.INPUT, port_name="a")
    mod.connect(ac, "Y", a, direction="output")
    mod.ports["a"] = a
    bc = mod.add_cell("b_p", PrimOp.INPUT, port_name="b")
    mod.connect(bc, "Y", b, direction="output")
    mod.ports["b"] = b
    yc = mod.add_cell("y_p", PrimOp.OUTPUT, port_name="y")
    mod.connect(yc, "A", y)
    mod.ports["y"] = y
    cell = mod.add_cell("arith", op)
    mod.connect(cell, "A", a)
    mod.connect(cell, "B", b)
    mod.connect(cell, "Y", y, direction="output")
    return mod


def test_every_binary_op_pair_distinguishable():
    """Every distinct pair of binary operations must be non-equivalent
    on at least one input combination."""
    # Note: XOR and NE are equivalent on 1-bit inputs (a^b == a!=b)
    ops = [PrimOp.AND, PrimOp.OR, PrimOp.XOR, PrimOp.EQ]
    for i, op_a in enumerate(ops):
        for op_b in ops[i + 1:]:
            r = check_equivalence_exhaustive(
                _make_gate_module("a", op_a),
                _make_gate_module("b", op_b),
            )
            assert not r.equivalent, f"{op_a.name} and {op_b.name} should be distinguishable"


def test_mutated_and_detected():
    """If we compute AND as OR (a mutation), the equivalence checker must catch it."""
    # Build "correct" AND module
    correct = _make_gate_module("correct", PrimOp.AND)

    # Build "mutant" that uses OR where AND should be
    mutant = _make_gate_module("mutant", PrimOp.OR)

    r = check_equivalence_exhaustive(correct, mutant)
    assert not r.equivalent
    assert r.counterexample is not None
    # The counterexample should be an input where AND != OR
    ce = r.counterexample
    a, b = ce.get("a", 0), ce.get("b", 0)
    correct_val = eval_const_op(PrimOp.AND, {"A": a, "B": b}, {}, 1)
    mutant_val = eval_const_op(PrimOp.OR, {"A": a, "B": b}, {}, 1)
    assert correct_val != mutant_val


def test_mutated_add_detected():
    """If we compute ADD as SUB (a mutation), equivalence must fail on 4-bit values."""
    correct = _make_4bit_arith("correct", PrimOp.ADD)
    mutant = _make_4bit_arith("mutant", PrimOp.SUB)
    r = check_equivalence_exhaustive(correct, mutant, max_input_bits=8)
    assert not r.equivalent


def test_identity_mutation_not_detected():
    """Two identical modules must be equivalent — no false positives."""
    a = _make_gate_module("a", PrimOp.AND)
    b = _make_gate_module("b", PrimOp.AND)
    r = check_equivalence_exhaustive(a, b)
    assert r.equivalent
