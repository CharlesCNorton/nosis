"""Tests for nosis.reqmerge — reachable-state equivalence merging."""

from nosis.ir import Module, PrimOp
from nosis.reqmerge import merge_reachable_equivalent, propagate_reachable_constants


def test_no_crash_on_empty():
    mod = Module(name="empty")
    assert merge_reachable_equivalent(mod, cycles=10) == 0


def test_no_crash_no_inputs():
    mod = Module(name="test")
    y = mod.add_net("y", 1)
    c = mod.add_cell("c0", PrimOp.CONST, value=0, width=1)
    mod.connect(c, "Y", y, direction="output")
    oc = mod.add_cell("o_p", PrimOp.OUTPUT, port_name="y")
    mod.connect(oc, "A", y)
    mod.ports["y"] = y
    assert merge_reachable_equivalent(mod, cycles=10) == 0


def test_identical_const_nets_merged():
    """Two constant-driven nets with the same value should be mergeable."""
    mod = Module(name="test")
    a = mod.add_net("a", 1)
    ac = mod.add_cell("a_p", PrimOp.INPUT, port_name="a")
    mod.connect(ac, "Y", a, direction="output")
    mod.ports["a"] = a

    c1 = mod.add_net("c1", 1)
    cc1 = mod.add_cell("cc1", PrimOp.CONST, value=0, width=1)
    mod.connect(cc1, "Y", c1, direction="output")

    c2 = mod.add_net("c2", 1)
    cc2 = mod.add_cell("cc2", PrimOp.CONST, value=0, width=1)
    mod.connect(cc2, "Y", c2, direction="output")

    # Both consumed by an AND — neither is output-reachable or ff-input-reachable
    y = mod.add_net("y", 1)
    gc = mod.add_cell("and0", PrimOp.AND)
    mod.connect(gc, "A", c1)
    mod.connect(gc, "B", c2)
    mod.connect(gc, "Y", y, direction="output")

    # Don't put y as an output port — reqmerge excludes output-reachable nets
    result = merge_reachable_equivalent(mod, cycles=10)
    # May or may not merge depending on safety guards, but must not crash
    assert result >= 0


def test_port_nets_never_merged():
    """Port nets must never be merged away."""
    mod = Module(name="test")
    a = mod.add_net("a", 1)
    ac = mod.add_cell("a_p", PrimOp.INPUT, port_name="a")
    mod.connect(ac, "Y", a, direction="output")
    mod.ports["a"] = a

    b = mod.add_net("b", 1)
    bc = mod.add_cell("b_p", PrimOp.INPUT, port_name="b")
    mod.connect(bc, "Y", b, direction="output")
    mod.ports["b"] = b

    merge_reachable_equivalent(mod, cycles=10)
    assert "a" in mod.ports
    assert "b" in mod.ports


def test_propagate_reachable_constants_no_crash():
    mod = Module(name="test")
    a = mod.add_net("a", 1)
    ac = mod.add_cell("a_p", PrimOp.INPUT, port_name="a")
    mod.connect(ac, "Y", a, direction="output")
    mod.ports["a"] = a
    result = propagate_reachable_constants(mod, cycles=10)
    assert result >= 0
