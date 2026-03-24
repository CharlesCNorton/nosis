"""Tests for nosis.formal — bounded model checking."""

from nosis.ir import Module, PrimOp
from nosis.formal import (
    check_assertion_bmc,
    check_assertion_bmc_sat,
    check_output_reachable,
    check_optimization_equivalence,
    check_sequential_equivalence,
)


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


# ---------------------------------------------------------------------------
# Pre/post optimization equivalence checking
# ---------------------------------------------------------------------------

def test_optimization_equivalence_identical():
    """Two identical modules must be equivalent."""
    mod_a = _gate_module()
    mod_b = _gate_module()
    r = check_optimization_equivalence(mod_a, mod_b)
    assert r.holds
    assert "equiv" in r.method


def test_optimization_equivalence_different():
    """A module changed to OR instead of AND must be non-equivalent."""
    mod_a = _gate_module()

    # Build an OR version
    mod_b = Module(name="test_or")
    a = mod_b.add_net("a", 1)
    b = mod_b.add_net("b", 1)
    y = mod_b.add_net("y", 1)
    ac = mod_b.add_cell("a_p", PrimOp.INPUT, port_name="a")
    mod_b.connect(ac, "Y", a, direction="output")
    mod_b.ports["a"] = a
    bc = mod_b.add_cell("b_p", PrimOp.INPUT, port_name="b")
    mod_b.connect(bc, "Y", b, direction="output")
    mod_b.ports["b"] = b
    gc = mod_b.add_cell("or0", PrimOp.OR)
    mod_b.connect(gc, "A", a)
    mod_b.connect(gc, "B", b)
    mod_b.connect(gc, "Y", y, direction="output")
    oc = mod_b.add_cell("out_p", PrimOp.OUTPUT, port_name="y")
    mod_b.connect(oc, "A", y)
    mod_b.ports["y"] = y

    r = check_optimization_equivalence(mod_a, mod_b)
    assert not r.holds


# ---------------------------------------------------------------------------
# Sequential equivalence checking
# ---------------------------------------------------------------------------

def test_sequential_equivalence_identical():
    """Two identical combinational modules must be sequentially equivalent."""
    mod_a = _gate_module()
    mod_b = _gate_module()
    r = check_sequential_equivalence(mod_a, mod_b, cycles=20)
    assert r.holds
    assert r.method == "sequential_sim"


def test_sequential_equivalence_different():
    """AND vs OR must fail sequential equivalence."""
    mod_a = _gate_module()

    mod_b = Module(name="test_or")
    a = mod_b.add_net("a", 1)
    b = mod_b.add_net("b", 1)
    y = mod_b.add_net("y", 1)
    ac = mod_b.add_cell("a_p", PrimOp.INPUT, port_name="a")
    mod_b.connect(ac, "Y", a, direction="output")
    mod_b.ports["a"] = a
    bc = mod_b.add_cell("b_p", PrimOp.INPUT, port_name="b")
    mod_b.connect(bc, "Y", b, direction="output")
    mod_b.ports["b"] = b
    gc = mod_b.add_cell("or0", PrimOp.OR)
    mod_b.connect(gc, "A", a)
    mod_b.connect(gc, "B", b)
    mod_b.connect(gc, "Y", y, direction="output")
    oc = mod_b.add_cell("out_p", PrimOp.OUTPUT, port_name="y")
    mod_b.connect(oc, "A", y)
    mod_b.ports["y"] = y

    r = check_sequential_equivalence(mod_a, mod_b, cycles=20)
    assert not r.holds
    assert r.counterexample_cycle is not None


# ---------------------------------------------------------------------------
# SAT-based BMC
# ---------------------------------------------------------------------------

def test_sat_bmc_constant_holds():
    """SAT BMC on a constant module: assertion holds."""
    mod = _const_module(42)
    r = check_assertion_bmc_sat(mod, "c", 42)
    assert r.holds


def test_sat_bmc_constant_fails():
    """SAT BMC on a constant module: assertion fails."""
    mod = _const_module(42)
    r = check_assertion_bmc_sat(mod, "c", 99)
    assert not r.holds


def test_sat_bmc_gate():
    """SAT BMC: AND gate can output 0 when either input is 0."""
    mod = _gate_module()
    # AND output should be able to produce 0 (when a=0 or b=0)
    r = check_assertion_bmc_sat(mod, "y", 1, bound=100)
    # This checks if y is ALWAYS 1 — it's not, so it should fail
    assert not r.holds
