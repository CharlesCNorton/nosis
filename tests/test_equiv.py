"""Tests for nosis.equiv — equivalence checking."""

from nosis.ir import Module, PrimOp
from nosis.equiv import (
    check_equivalence,
    check_equivalence_exhaustive,
    wildcard_eq,
    EquivalenceResult,
)


def _gate_module(name: str, op: PrimOp) -> Module:
    """Build a 1-bit binary gate module with ports a, b -> y."""
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
    gc = mod.add_cell("gate0", op)
    mod.connect(gc, "A", a)
    mod.connect(gc, "B", b)
    mod.connect(gc, "Y", y, direction="output")
    return mod


def _double_not_module(name: str) -> Module:
    """Build ~~a & ~~b — equivalent to a & b."""
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
    na = mod.add_net("na", 1)
    mod.add_cell("not_a", PrimOp.NOT)
    mod.connect(mod.cells["not_a"], "A", a)
    mod.connect(mod.cells["not_a"], "Y", na, direction="output")
    nna = mod.add_net("nna", 1)
    mod.add_cell("not_na", PrimOp.NOT)
    mod.connect(mod.cells["not_na"], "A", na)
    mod.connect(mod.cells["not_na"], "Y", nna, direction="output")
    nb = mod.add_net("nb", 1)
    mod.add_cell("not_b", PrimOp.NOT)
    mod.connect(mod.cells["not_b"], "A", b)
    mod.connect(mod.cells["not_b"], "Y", nb, direction="output")
    nnb = mod.add_net("nnb", 1)
    mod.add_cell("not_nb", PrimOp.NOT)
    mod.connect(mod.cells["not_nb"], "A", nb)
    mod.connect(mod.cells["not_nb"], "Y", nnb, direction="output")
    gc = mod.add_cell("and0", PrimOp.AND)
    mod.connect(gc, "A", nna)
    mod.connect(gc, "B", nnb)
    mod.connect(gc, "Y", y, direction="output")
    return mod


# --- Exhaustive equivalence ---

def test_equivalent_identical():
    a = _gate_module("a", PrimOp.AND)
    b = _gate_module("b", PrimOp.AND)
    r = check_equivalence_exhaustive(a, b)
    assert r.equivalent
    assert r.method == "exhaustive"


def test_not_equivalent_and_vs_or():
    a = _gate_module("a", PrimOp.AND)
    b = _gate_module("b", PrimOp.OR)
    r = check_equivalence_exhaustive(a, b)
    assert not r.equivalent
    assert r.counterexample is not None


def test_not_equivalent_and_vs_xor():
    a = _gate_module("a", PrimOp.AND)
    b = _gate_module("b", PrimOp.XOR)
    r = check_equivalence_exhaustive(a, b)
    assert not r.equivalent


def test_equivalent_double_not():
    a = _gate_module("a", PrimOp.AND)
    b = _double_not_module("b")
    r = check_equivalence_exhaustive(a, b)
    assert r.equivalent


# --- Auto method selection ---

def test_auto_selects_exhaustive_for_small():
    a = _gate_module("a", PrimOp.AND)
    b = _gate_module("b", PrimOp.AND)
    r = check_equivalence(a, b)
    assert r.equivalent
    assert r.method in ("exhaustive", "sat", "random_simulation")


def test_auto_detects_nonequivalent():
    a = _gate_module("a", PrimOp.AND)
    b = _gate_module("b", PrimOp.OR)
    r = check_equivalence(a, b)
    assert not r.equivalent


# --- Counterexample validation ---

def test_counterexample_is_valid():
    """The counterexample must actually produce different outputs."""
    from nosis.equiv import _simulate_combinational
    a = _gate_module("a", PrimOp.AND)
    b = _gate_module("b", PrimOp.OR)
    r = check_equivalence(a, b)
    assert not r.equivalent
    ce = r.counterexample
    assert ce is not None
    vals_a = _simulate_combinational(a, ce)
    vals_b = _simulate_combinational(b, ce)
    # At least one output must differ at the counterexample
    a_out = vals_a.get("y", 0)
    b_out = vals_b.get("y", 0)
    assert a_out != b_out, f"counterexample {ce} does not produce different outputs"


# --- Wildcard equality ---

def test_wildcard_exact_match():
    assert wildcard_eq(0b1010, 0b1010, 0b1111, 4)


def test_wildcard_mismatch():
    assert not wildcard_eq(0b1010, 0b1011, 0b1111, 4)


def test_wildcard_dont_care():
    assert wildcard_eq(0b1010, 0b1011, 0b1110, 4)


def test_wildcard_full_dont_care():
    assert wildcard_eq(0b0000, 0b1111, 0b0000, 4)


def test_wildcard_casez_pattern():
    # 4'b1??0 matches 4'b1010: mask bits 1,2 are don't-care
    assert wildcard_eq(0b1010, 0b1000, 0b1001, 4)
    # 4'b1??0 does NOT match 4'b1011: bit 0 differs
    assert not wildcard_eq(0b1011, 0b1000, 0b1001, 4)


# --- XOR identity ---

def test_xor_self_equivalent():
    """a XOR a = 0 for all inputs — a constant-zero module should be equivalent."""
    mod_xor = Module(name="xor_self")
    a = mod_xor.add_net("a", 1)
    y = mod_xor.add_net("y", 1)
    ac = mod_xor.add_cell("a_p", PrimOp.INPUT, port_name="a")
    mod_xor.connect(ac, "Y", a, direction="output")
    mod_xor.ports["a"] = a
    yc = mod_xor.add_cell("y_p", PrimOp.OUTPUT, port_name="y")
    mod_xor.connect(yc, "A", y)
    mod_xor.ports["y"] = y
    xc = mod_xor.add_cell("xor0", PrimOp.XOR)
    mod_xor.connect(xc, "A", a)
    mod_xor.connect(xc, "B", a)
    mod_xor.connect(xc, "Y", y, direction="output")

    mod_zero = Module(name="zero")
    a2 = mod_zero.add_net("a", 1)
    y2 = mod_zero.add_net("y", 1)
    ac2 = mod_zero.add_cell("a_p", PrimOp.INPUT, port_name="a")
    mod_zero.connect(ac2, "Y", a2, direction="output")
    mod_zero.ports["a"] = a2
    yc2 = mod_zero.add_cell("y_p", PrimOp.OUTPUT, port_name="y")
    mod_zero.connect(yc2, "A", y2)
    mod_zero.ports["y"] = y2
    cc = mod_zero.add_cell("c0", PrimOp.CONST, value=0, width=1)
    mod_zero.connect(cc, "Y", y2, direction="output")

    r = check_equivalence_exhaustive(mod_xor, mod_zero)
    assert r.equivalent


# --- Multi-bit SAT encoding ---

def _multibit_add_module(name: str, width: int) -> Module:
    """Build an N-bit adder: y = a + b."""
    mod = Module(name=name)
    a = mod.add_net("a", width)
    b = mod.add_net("b", width)
    y = mod.add_net("y", width)
    ac = mod.add_cell("a_p", PrimOp.INPUT, port_name="a")
    mod.connect(ac, "Y", a, direction="output")
    mod.ports["a"] = a
    bc = mod.add_cell("b_p", PrimOp.INPUT, port_name="b")
    mod.connect(bc, "Y", b, direction="output")
    mod.ports["b"] = b
    yc = mod.add_cell("y_p", PrimOp.OUTPUT, port_name="y")
    mod.connect(yc, "A", y)
    mod.ports["y"] = y
    gc = mod.add_cell("add0", PrimOp.ADD)
    mod.connect(gc, "A", a)
    mod.connect(gc, "B", b)
    mod.connect(gc, "Y", y, direction="output")
    return mod


def test_multibit_add_equivalent():
    """Two identical 4-bit adders must be SAT-equivalent."""
    a = _multibit_add_module("a", 4)
    b = _multibit_add_module("b", 4)
    r = check_equivalence(a, b, max_exhaustive_bits=0)  # force SAT path
    assert r.equivalent


def test_multibit_add_vs_sub_not_equivalent():
    """A 4-bit adder and subtractor must NOT be equivalent."""
    mod_add = _multibit_add_module("add", 4)

    mod_sub = Module(name="sub")
    a = mod_sub.add_net("a", 4)
    b = mod_sub.add_net("b", 4)
    y = mod_sub.add_net("y", 4)
    ac = mod_sub.add_cell("a_p", PrimOp.INPUT, port_name="a")
    mod_sub.connect(ac, "Y", a, direction="output")
    mod_sub.ports["a"] = a
    bc = mod_sub.add_cell("b_p", PrimOp.INPUT, port_name="b")
    mod_sub.connect(bc, "Y", b, direction="output")
    mod_sub.ports["b"] = b
    yc = mod_sub.add_cell("y_p", PrimOp.OUTPUT, port_name="y")
    mod_sub.connect(yc, "A", y)
    mod_sub.ports["y"] = y
    gc = mod_sub.add_cell("sub0", PrimOp.SUB)
    mod_sub.connect(gc, "A", a)
    mod_sub.connect(gc, "B", b)
    mod_sub.connect(gc, "Y", y, direction="output")

    r = check_equivalence(mod_add, mod_sub, max_exhaustive_bits=0)
    assert not r.equivalent


def test_multibit_and_equivalent():
    """Two identical 8-bit AND gates must be equivalent via SAT."""
    def _and_mod(name):
        mod = Module(name=name)
        a = mod.add_net("a", 8)
        b = mod.add_net("b", 8)
        y = mod.add_net("y", 8)
        ac = mod.add_cell("a_p", PrimOp.INPUT, port_name="a")
        mod.connect(ac, "Y", a, direction="output")
        mod.ports["a"] = a
        bc = mod.add_cell("b_p", PrimOp.INPUT, port_name="b")
        mod.connect(bc, "Y", b, direction="output")
        mod.ports["b"] = b
        yc = mod.add_cell("y_p", PrimOp.OUTPUT, port_name="y")
        mod.connect(yc, "A", y)
        mod.ports["y"] = y
        gc = mod.add_cell("and0", PrimOp.AND)
        mod.connect(gc, "A", a)
        mod.connect(gc, "B", b)
        mod.connect(gc, "Y", y, direction="output")
        return mod
    a = _and_mod("a")
    b = _and_mod("b")
    r = check_equivalence(a, b, max_exhaustive_bits=0)
    assert r.equivalent
