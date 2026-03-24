"""Tests for nosis.formal — bounded model checking."""

from nosis.ir import Module, PrimOp
from nosis.formal import check_assertion_bmc, check_output_reachable


def _const_module(val: int) -> Module:
    mod = Module(name="test")
    c = mod.add_net("c", 8)
    cc = mod.add_cell("c_c", PrimOp.CONST, value=val, width=8)
    mod.connect(cc, "Y", c, direction="output")
    oc = mod.add_cell("out_p", PrimOp.OUTPUT, port_name="out")
    mod.connect(oc, "A", c)
    mod.ports["out"] = c
    return mod


def _gate_module() -> Module:
    mod = Module(name="test")
    a = mod.add_net("a", 1)
    b = mod.add_net("b", 1)
    y = mod.add_net("y", 1)
    ac = mod.add_cell("a_p", PrimOp.INPUT, port_name="a")
    mod.connect(ac, "Y", a, direction="output")
    mod.ports["a"] = a
    bc = mod.add_cell("b_p", PrimOp.INPUT, port_name="b")
    mod.connect(bc, "Y", b, direction="output")
    mod.ports["b"] = b
    gc = mod.add_cell("and0", PrimOp.AND)
    mod.connect(gc, "A", a)
    mod.connect(gc, "B", b)
    mod.connect(gc, "Y", y, direction="output")
    oc = mod.add_cell("out_p", PrimOp.OUTPUT, port_name="y")
    mod.connect(oc, "A", y)
    mod.ports["y"] = y
    return mod


def test_constant_assertion_holds():
    mod = _const_module(42)
    r = check_assertion_bmc(mod, "c", 42, bound=10)
    assert r.holds


def test_constant_assertion_fails():
    mod = _const_module(42)
    r = check_assertion_bmc(mod, "c", 99, bound=10)
    assert not r.holds


def test_output_reachable():
    mod = _gate_module()
    # AND(1,1) = 1, so output 1 is reachable
    r = check_output_reachable(mod, "y", 1, bound=100)
    assert r.holds


def test_output_unreachable_for_and():
    # AND can never output 2 (it's 1-bit)
    mod = _gate_module()
    r = check_output_reachable(mod, "y", 2, bound=100)
    assert not r.holds
