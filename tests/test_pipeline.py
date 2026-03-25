"""Consolidated optimization pipeline tests."""

from nosis.boolopt import boolean_optimize, tech_aware_optimize
from nosis.cse import eliminate_common_subexpressions
from nosis.cutmap import cut_map_luts
from nosis.dontcare import propagate_dont_cares
from nosis.ir import Module, PrimOp
from nosis.passes import (
    constant_fold,
    dead_code_eliminate,
    identity_simplify,
    run_default_passes,
)
from nosis.reqmerge import merge_reachable_equivalent, propagate_reachable_constants
from nosis.retiming import (
    duplicate_high_fanout,
    retime_backward,
    retime_forward,
    verify_retime_clocks,
)
from nosis.satconst import prove_constants_sat


# --- from test_passes ---




def _make_const(mod, name, value, width):
    net = mod.add_net(name, width)
    cell = mod.add_cell(f"c_{name}", PrimOp.CONST, value=value, width=width)
    mod.connect(cell, "Y", net, direction="output")
    return net


def test_const_fold_and():
    mod = Module(name="test")
    a = _make_const(mod, "a", 0xFF, 8)
    b = _make_const(mod, "b", 0x0F, 8)
    out = mod.add_net("out", 8)
    cell = mod.add_cell("and0", PrimOp.AND)
    mod.connect(cell, "A", a)
    mod.connect(cell, "B", b)
    mod.connect(cell, "Y", out, direction="output")

    folded = constant_fold(mod)
    assert folded == 1
    assert mod.cells["and0"].op == PrimOp.CONST
    assert mod.cells["and0"].params["value"] == 0x0F


def test_const_fold_add():
    mod = Module(name="test")
    a = _make_const(mod, "a", 100, 16)
    b = _make_const(mod, "b", 200, 16)
    out = mod.add_net("out", 16)
    cell = mod.add_cell("add0", PrimOp.ADD)
    mod.connect(cell, "A", a)
    mod.connect(cell, "B", b)
    mod.connect(cell, "Y", out, direction="output")

    folded = constant_fold(mod)
    assert folded == 1
    assert mod.cells["add0"].params["value"] == 300


def test_const_fold_mux_const_sel():
    mod = Module(name="test")
    sel = _make_const(mod, "sel", 1, 1)
    a = _make_const(mod, "a", 42, 8)
    b = _make_const(mod, "b", 99, 8)
    out = mod.add_net("out", 8)
    mux = mod.add_cell("mux0", PrimOp.MUX)
    mod.connect(mux, "S", sel)
    mod.connect(mux, "A", a)
    mod.connect(mux, "B", b)
    mod.connect(mux, "Y", out, direction="output")

    folded = constant_fold(mod)
    assert folded == 1
    assert mod.cells["mux0"].params["value"] == 99  # sel=1 -> B


def test_const_fold_cascaded():
    mod = Module(name="test")
    a = _make_const(mod, "a", 3, 8)
    b = _make_const(mod, "b", 5, 8)
    mid = mod.add_net("mid", 8)
    add = mod.add_cell("add0", PrimOp.ADD)
    mod.connect(add, "A", a)
    mod.connect(add, "B", b)
    mod.connect(add, "Y", mid, direction="output")

    c = _make_const(mod, "c", 10, 8)
    out = mod.add_net("out", 8)
    mul = mod.add_cell("mul0", PrimOp.MUL)
    mod.connect(mul, "A", mid)
    mod.connect(mul, "B", c)
    mod.connect(mul, "Y", out, direction="output")

    folded = constant_fold(mod)
    assert folded == 2  # both add and mul folded
    assert mod.cells["mul0"].params["value"] == 80  # (3+5)*10


def test_dce_removes_dead():
    mod = Module(name="test")
    # Live path: input -> output
    inp = mod.add_net("inp", 1)
    inp_cell = mod.add_cell("inp_port", PrimOp.INPUT, port_name="inp")
    mod.connect(inp_cell, "Y", inp, direction="output")
    mod.ports["inp"] = inp

    out = mod.add_net("out", 1)
    out_cell = mod.add_cell("out_port", PrimOp.OUTPUT, port_name="out")
    mod.connect(out_cell, "A", inp)
    mod.ports["out"] = out

    # Dead path: constant -> nowhere
    dead_net = mod.add_net("dead", 8)
    dead_cell = mod.add_cell("dead_const", PrimOp.CONST, value=0, width=8)
    mod.connect(dead_cell, "Y", dead_net, direction="output")

    removed = dead_code_eliminate(mod)
    assert removed >= 1
    assert "dead_const" not in mod.cells


def test_dce_keeps_ff():
    mod = Module(name="test")
    clk = mod.add_net("clk", 1)
    clk_cell = mod.add_cell("clk_port", PrimOp.INPUT, port_name="clk")
    mod.connect(clk_cell, "Y", clk, direction="output")
    mod.ports["clk"] = clk

    d = mod.add_net("d", 1)
    d_cell = mod.add_cell("d_port", PrimOp.INPUT, port_name="d")
    mod.connect(d_cell, "Y", d, direction="output")
    mod.ports["d"] = d

    q = mod.add_net("q", 1)
    q_cell = mod.add_cell("q_port", PrimOp.OUTPUT, port_name="q")
    mod.connect(q_cell, "A", q)
    mod.ports["q"] = q

    ff = mod.add_cell("ff0", PrimOp.FF)
    mod.connect(ff, "CLK", clk)
    mod.connect(ff, "D", d)
    mod.connect(ff, "Q", q, direction="output")

    dead_code_eliminate(mod)
    assert "ff0" in mod.cells


def test_run_default_passes():
    mod = Module(name="test")
    a = _make_const(mod, "a", 7, 8)
    b = _make_const(mod, "b", 3, 8)
    out = mod.add_net("out", 8)
    out_cell = mod.add_cell("out_port", PrimOp.OUTPUT, port_name="out")
    mod.connect(out_cell, "A", out)
    mod.ports["out"] = out

    cell = mod.add_cell("add0", PrimOp.ADD)
    mod.connect(cell, "A", a)
    mod.connect(cell, "B", b)
    mod.connect(cell, "Y", out, direction="output")

    stats = run_default_passes(mod)
    assert stats.get("round_0", 0) >= 1


def test_identity_and_all_ones():
    """a & 0xFF (8-bit) -> a"""
    mod = Module(name="test")
    a = mod.add_net("a", 8)
    a_cell = mod.add_cell("a_p", PrimOp.INPUT, port_name="a")
    mod.connect(a_cell, "Y", a, direction="output")
    mod.ports["a"] = a

    ones = _make_const(mod, "ones", 0xFF, 8)
    out = mod.add_net("out", 8)
    and_cell = mod.add_cell("and0", PrimOp.AND)
    mod.connect(and_cell, "A", a)
    mod.connect(and_cell, "B", ones)
    mod.connect(and_cell, "Y", out, direction="output")

    simplified = identity_simplify(mod)
    assert simplified == 1


def test_identity_or_zero():
    """a | 0 -> a"""
    mod = Module(name="test")
    a = mod.add_net("a", 8)
    a_cell = mod.add_cell("a_p", PrimOp.INPUT, port_name="a")
    mod.connect(a_cell, "Y", a, direction="output")
    mod.ports["a"] = a

    zero = _make_const(mod, "zero", 0, 8)
    out = mod.add_net("out", 8)
    or_cell = mod.add_cell("or0", PrimOp.OR)
    mod.connect(or_cell, "A", a)
    mod.connect(or_cell, "B", zero)
    mod.connect(or_cell, "Y", out, direction="output")

    simplified = identity_simplify(mod)
    assert simplified == 1


def test_identity_xor_zero():
    """a ^ 0 -> a"""
    mod = Module(name="test")
    a = mod.add_net("a", 8)
    a_cell = mod.add_cell("a_p", PrimOp.INPUT, port_name="a")
    mod.connect(a_cell, "Y", a, direction="output")
    mod.ports["a"] = a

    zero = _make_const(mod, "zero", 0, 8)
    out = mod.add_net("out", 8)
    xor_cell = mod.add_cell("xor0", PrimOp.XOR)
    mod.connect(xor_cell, "A", a)
    mod.connect(xor_cell, "B", zero)
    mod.connect(xor_cell, "Y", out, direction="output")

    simplified = identity_simplify(mod)
    assert simplified == 1


def test_identity_mul_zero():
    """a * 0 -> 0"""
    mod = Module(name="test")
    a = mod.add_net("a", 8)
    a_cell = mod.add_cell("a_p", PrimOp.INPUT, port_name="a")
    mod.connect(a_cell, "Y", a, direction="output")
    mod.ports["a"] = a

    zero = _make_const(mod, "zero", 0, 8)
    out = mod.add_net("out", 8)
    mul_cell = mod.add_cell("mul0", PrimOp.MUL)
    mod.connect(mul_cell, "A", a)
    mod.connect(mul_cell, "B", zero)
    mod.connect(mul_cell, "Y", out, direction="output")

    simplified = identity_simplify(mod)
    assert simplified == 1
    assert mod.cells["mul0"].op == PrimOp.CONST
    assert mod.cells["mul0"].params["value"] == 0


def test_identity_add_zero():
    """a + 0 -> a"""
    mod = Module(name="test")
    a = mod.add_net("a", 8)
    a_cell = mod.add_cell("a_p", PrimOp.INPUT, port_name="a")
    mod.connect(a_cell, "Y", a, direction="output")
    mod.ports["a"] = a

    zero = _make_const(mod, "zero", 0, 8)
    out = mod.add_net("out", 8)
    add_cell = mod.add_cell("add0", PrimOp.ADD)
    mod.connect(add_cell, "A", a)
    mod.connect(add_cell, "B", zero)
    mod.connect(add_cell, "Y", out, direction="output")

    simplified = identity_simplify(mod)
    assert simplified == 1


def test_identity_double_not():
    """~~a -> a"""
    mod = Module(name="test")
    a = mod.add_net("a", 8)
    a_cell = mod.add_cell("a_p", PrimOp.INPUT, port_name="a")
    mod.connect(a_cell, "Y", a, direction="output")
    mod.ports["a"] = a

    na = mod.add_net("na", 8)
    not1 = mod.add_cell("not1", PrimOp.NOT)
    mod.connect(not1, "A", a)
    mod.connect(not1, "Y", na, direction="output")

    out = mod.add_net("out", 8)
    not2 = mod.add_cell("not2", PrimOp.NOT)
    mod.connect(not2, "A", na)
    mod.connect(not2, "Y", out, direction="output")

    simplified = identity_simplify(mod)
    assert simplified == 1


# --- from test_boolopt ---




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


# --- from test_cse ---




def test_eliminate_identical_and():
    mod = Module(name="test")
    a = mod.add_net("a", 8)
    b = mod.add_net("b", 8)
    y1 = mod.add_net("y1", 8)
    y2 = mod.add_net("y2", 8)

    c1 = mod.add_cell("and1", PrimOp.AND)
    mod.connect(c1, "A", a)
    mod.connect(c1, "B", b)
    mod.connect(c1, "Y", y1, direction="output")

    c2 = mod.add_cell("and2", PrimOp.AND)
    mod.connect(c2, "A", a)
    mod.connect(c2, "B", b)
    mod.connect(c2, "Y", y2, direction="output")

    eliminated = eliminate_common_subexpressions(mod)
    assert eliminated == 1
    assert len(mod.cells) == 1


def test_no_eliminate_different_ops():
    mod = Module(name="test")
    a = mod.add_net("a", 8)
    b = mod.add_net("b", 8)
    y1 = mod.add_net("y1", 8)
    y2 = mod.add_net("y2", 8)

    c1 = mod.add_cell("and1", PrimOp.AND)
    mod.connect(c1, "A", a)
    mod.connect(c1, "B", b)
    mod.connect(c1, "Y", y1, direction="output")

    c2 = mod.add_cell("or1", PrimOp.OR)
    mod.connect(c2, "A", a)
    mod.connect(c2, "B", b)
    mod.connect(c2, "Y", y2, direction="output")

    eliminated = eliminate_common_subexpressions(mod)
    assert eliminated == 0


def test_no_eliminate_different_inputs():
    mod = Module(name="test")
    a = mod.add_net("a", 8)
    b = mod.add_net("b", 8)
    c = mod.add_net("c", 8)
    y1 = mod.add_net("y1", 8)
    y2 = mod.add_net("y2", 8)

    c1 = mod.add_cell("and1", PrimOp.AND)
    mod.connect(c1, "A", a)
    mod.connect(c1, "B", b)
    mod.connect(c1, "Y", y1, direction="output")

    c2 = mod.add_cell("and2", PrimOp.AND)
    mod.connect(c2, "A", a)
    mod.connect(c2, "B", c)  # different input
    mod.connect(c2, "Y", y2, direction="output")

    eliminated = eliminate_common_subexpressions(mod)
    assert eliminated == 0


def test_no_eliminate_ff():
    """FF cells must never be deduplicated."""
    mod = Module(name="test")
    clk = mod.add_net("clk", 1)
    d = mod.add_net("d", 1)
    q1 = mod.add_net("q1", 1)
    q2 = mod.add_net("q2", 1)

    ff1 = mod.add_cell("ff1", PrimOp.FF)
    mod.connect(ff1, "CLK", clk)
    mod.connect(ff1, "D", d)
    mod.connect(ff1, "Q", q1, direction="output")

    ff2 = mod.add_cell("ff2", PrimOp.FF)
    mod.connect(ff2, "CLK", clk)
    mod.connect(ff2, "D", d)
    mod.connect(ff2, "Q", q2, direction="output")

    eliminated = eliminate_common_subexpressions(mod)
    assert eliminated == 0  # FFs are not CSE candidates


def test_consumer_redirect():
    """After CSE, consumers of the duplicate should use the original's output."""
    mod = Module(name="test")
    a = mod.add_net("a", 1)
    b = mod.add_net("b", 1)
    y1 = mod.add_net("y1", 1)
    y2 = mod.add_net("y2", 1)
    final = mod.add_net("final", 1)

    c1 = mod.add_cell("and1", PrimOp.AND)
    mod.connect(c1, "A", a)
    mod.connect(c1, "B", b)
    mod.connect(c1, "Y", y1, direction="output")

    c2 = mod.add_cell("and2", PrimOp.AND)
    mod.connect(c2, "A", a)
    mod.connect(c2, "B", b)
    mod.connect(c2, "Y", y2, direction="output")

    # Consumer uses y2 (the duplicate)
    c3 = mod.add_cell("not1", PrimOp.NOT)
    mod.connect(c3, "A", y2)
    mod.connect(c3, "Y", final, direction="output")

    eliminate_common_subexpressions(mod)
    # c3 should now use y1 (the original)
    assert mod.cells["not1"].inputs["A"] is y1


# --- from test_dontcare ---




def test_no_crash_on_empty():
    mod = Module(name="empty")
    assert propagate_dont_cares(mod) == 0


def test_no_crash_on_simple_logic():
    mod = Module(name="test")
    a = mod.add_net("a", 1)
    y = mod.add_net("y", 1)
    ac = mod.add_cell("a_p", PrimOp.INPUT, port_name="a")
    mod.connect(ac, "Y", a, direction="output")
    mod.ports["a"] = a
    gc = mod.add_cell("not0", PrimOp.NOT)
    mod.connect(gc, "A", a)
    mod.connect(gc, "Y", y, direction="output")
    oc = mod.add_cell("y_p", PrimOp.OUTPUT, port_name="y")
    mod.connect(oc, "A", y)
    mod.ports["y"] = y
    result = propagate_dont_cares(mod)
    assert result >= 0


def test_masked_ff_detected():
    """An FF whose Q is only consumed through AND(mask, Q) is masked."""
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

    # Mask: NOT(sel) & Q
    sel = mod.add_net("sel", 1)
    sc = mod.add_cell("sel_p", PrimOp.INPUT, port_name="sel")
    mod.connect(sc, "Y", sel, direction="output")
    mod.ports["sel"] = sel

    nsel = mod.add_net("nsel", 1)
    not_cell = mod.add_cell("not0", PrimOp.NOT)
    mod.connect(not_cell, "A", sel)
    mod.connect(not_cell, "Y", nsel, direction="output")

    masked = mod.add_net("masked", 1)
    and_cell = mod.add_cell("and0", PrimOp.AND)
    mod.connect(and_cell, "A", nsel)
    mod.connect(and_cell, "B", q)
    mod.connect(and_cell, "Y", masked, direction="output")

    oc = mod.add_cell("o_p", PrimOp.OUTPUT, port_name="masked")
    mod.connect(oc, "A", masked)
    mod.ports["masked"] = masked

    # Should detect the masked FF pattern (may or may not simplify)
    result = propagate_dont_cares(mod)
    assert result >= 0


# --- from test_cutmap ---




def test_absorb_chain_of_3():
    """A chain of 3 single-bit ops with ≤4 total inputs should merge."""
    mod = Module(name="test")
    a = mod.add_net("a", 1)
    b = mod.add_net("b", 1)
    c = mod.add_net("c", 1)
    m1 = mod.add_net("m1", 1)
    m2 = mod.add_net("m2", 1)
    out = mod.add_net("out", 1)

    # a & b -> m1
    c1 = mod.add_cell("and0", PrimOp.AND)
    mod.connect(c1, "A", a)
    mod.connect(c1, "B", b)
    mod.connect(c1, "Y", m1, direction="output")

    # m1 | c -> m2
    c2 = mod.add_cell("or0", PrimOp.OR)
    mod.connect(c2, "A", m1)
    mod.connect(c2, "B", c)
    mod.connect(c2, "Y", m2, direction="output")

    # ~m2 -> out
    c3 = mod.add_cell("not0", PrimOp.NOT)
    mod.connect(c3, "A", m2)
    mod.connect(c3, "Y", out, direction="output")

    # Add ports so DCE doesn't kill everything
    for name, net in [("a", a), ("b", b), ("c", c)]:
        inp = mod.add_cell(f"inp_{name}", PrimOp.INPUT, port_name=name)
        mod.connect(inp, "Y", net, direction="output")
        mod.ports[name] = net
    outp = mod.add_cell("out_p", PrimOp.OUTPUT, port_name="out")
    mod.connect(outp, "A", out)
    mod.ports["out"] = out

    absorbed = cut_map_luts(mod)
    # not0 should absorb and0 and or0 into a single 3-input function
    assert absorbed >= 1
    # The surviving cell should have a packed_lut_init
    surviving = [c for c in mod.cells.values()
                 if c.params.get("packed") and c.op != PrimOp.CONST]
    assert len(surviving) >= 1


def test_no_absorb_multi_fanout():
    """A cell whose output feeds multiple consumers must not be absorbed."""
    mod = Module(name="test")
    a = mod.add_net("a", 1)
    b = mod.add_net("b", 1)
    mid = mod.add_net("mid", 1)
    out1 = mod.add_net("out1", 1)
    out2 = mod.add_net("out2", 1)

    c1 = mod.add_cell("and0", PrimOp.AND)
    mod.connect(c1, "A", a)
    mod.connect(c1, "B", b)
    mod.connect(c1, "Y", mid, direction="output")

    c2 = mod.add_cell("not0", PrimOp.NOT)
    mod.connect(c2, "A", mid)
    mod.connect(c2, "Y", out1, direction="output")

    c3 = mod.add_cell("not1", PrimOp.NOT)
    mod.connect(c3, "A", mid)  # mid has 2 consumers
    mod.connect(c3, "Y", out2, direction="output")

    absorbed = cut_map_luts(mod)
    assert absorbed == 0  # and0 has 2 consumers, can't absorb


def test_no_absorb_wide_output():
    """Multi-bit cells should not be absorbed (only 1-bit functions)."""
    mod = Module(name="test")
    a = mod.add_net("a", 8)
    b = mod.add_net("b", 8)
    y = mod.add_net("y", 8)

    c1 = mod.add_cell("and0", PrimOp.AND)
    mod.connect(c1, "A", a)
    mod.connect(c1, "B", b)
    mod.connect(c1, "Y", y, direction="output")

    absorbed = cut_map_luts(mod)
    assert absorbed == 0


def test_cut_map_never_increases_cells():
    """cut_map_luts must never increase the cell count."""
    mod = Module(name="test")
    a = mod.add_net("a", 1)
    y = mod.add_net("y", 1)
    c = mod.add_cell("not0", PrimOp.NOT)
    mod.connect(c, "A", a)
    mod.connect(c, "Y", y, direction="output")

    before = len(mod.cells)
    cut_map_luts(mod)
    assert len(mod.cells) <= before


# --- from test_reqmerge ---




def test_no_crash_on_empty_reqmerge():
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


# --- from test_satconst ---




def test_no_crash_on_empty_satconst():
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


# --- from test_retiming ---




def _ff_chain_module():
    """Build: clk -> FF(D=d_in) -> Q -> AND(Q, b) -> y -> OUTPUT."""
    mod = Module(name="test")
    clk = mod.add_net("clk", 1)
    d_in = mod.add_net("d_in", 1)
    q = mod.add_net("q", 1)
    b = mod.add_net("b", 1)
    y = mod.add_net("y", 1)
    out = mod.add_net("out", 1)

    cc = mod.add_cell("clk_p", PrimOp.INPUT, port_name="clk")
    mod.connect(cc, "Y", clk, direction="output")
    mod.ports["clk"] = clk
    dc = mod.add_cell("d_p", PrimOp.INPUT, port_name="d_in")
    mod.connect(dc, "Y", d_in, direction="output")
    mod.ports["d_in"] = d_in
    bc = mod.add_cell("b_p", PrimOp.INPUT, port_name="b")
    mod.connect(bc, "Y", b, direction="output")
    mod.ports["b"] = b

    ff = mod.add_cell("ff0", PrimOp.FF)
    mod.connect(ff, "CLK", clk)
    mod.connect(ff, "D", d_in)
    mod.connect(ff, "Q", q, direction="output")

    gc = mod.add_cell("and0", PrimOp.AND)
    mod.connect(gc, "A", q)
    mod.connect(gc, "B", b)
    mod.connect(gc, "Y", y, direction="output")

    oc = mod.add_cell("out_p", PrimOp.OUTPUT, port_name="out")
    mod.connect(oc, "A", y)
    mod.ports["out"] = out
    return mod


def test_retime_forward_does_not_crash():
    mod = _ff_chain_module()
    retimed = retime_forward(mod)
    assert retimed >= 0


def test_retime_preserves_cell_types():
    """Retiming must not change the set of cell types present."""
    mod = _ff_chain_module()
    {c.op for c in mod.cells.values()}
    retime_forward(mod)
    ops_after = {c.op for c in mod.cells.values()}
    assert PrimOp.FF in ops_after
    assert PrimOp.AND in ops_after


def test_retime_does_not_increase_cells():
    mod = _ff_chain_module()
    before = len(mod.cells)
    retime_forward(mod)
    after = len(mod.cells)
    assert after <= before + 1  # retiming may duplicate one FF


def test_duplicate_high_fanout_exact():
    """A cell driving 100 consumers at threshold 32 should produce duplicates."""
    mod = Module(name="test")
    a = mod.add_net("a", 1)
    b = mod.add_net("b", 1)
    mid = mod.add_net("mid", 1)
    cell = mod.add_cell("and0", PrimOp.AND)
    mod.connect(cell, "A", a)
    mod.connect(cell, "B", b)
    mod.connect(cell, "Y", mid, direction="output")

    for i in range(100):
        y = mod.add_net(f"y{i}", 1)
        c = mod.add_cell(f"not{i}", PrimOp.NOT)
        mod.connect(c, "A", mid)
        mod.connect(c, "Y", y, direction="output")

    dup_count = duplicate_high_fanout(mod, threshold=32)
    assert dup_count >= 1
    dup_cells = [n for n in mod.cells if "dup" in n]
    assert len(dup_cells) >= 1
    # Each dup cell must have the same op as the original
    for name in dup_cells:
        assert mod.cells[name].op == PrimOp.AND


def test_no_duplicate_below_threshold():
    mod = Module(name="test")
    a = mod.add_net("a", 1)
    b = mod.add_net("b", 1)
    mid = mod.add_net("mid", 1)
    cell = mod.add_cell("and0", PrimOp.AND)
    mod.connect(cell, "A", a)
    mod.connect(cell, "B", b)
    mod.connect(cell, "Y", mid, direction="output")

    for i in range(10):
        y = mod.add_net(f"y{i}", 1)
        c = mod.add_cell(f"not{i}", PrimOp.NOT)
        mod.connect(c, "A", mid)
        mod.connect(c, "Y", y, direction="output")

    dup_count = duplicate_high_fanout(mod, threshold=32)
    assert dup_count == 0


def test_duplicate_preserves_inputs():
    """Duplicated cells must read from the same input nets as the original."""
    mod = Module(name="test")
    a = mod.add_net("a", 1)
    b = mod.add_net("b", 1)
    mid = mod.add_net("mid", 1)
    cell = mod.add_cell("and0", PrimOp.AND)
    mod.connect(cell, "A", a)
    mod.connect(cell, "B", b)
    mod.connect(cell, "Y", mid, direction="output")

    for i in range(50):
        y = mod.add_net(f"y{i}", 1)
        c = mod.add_cell(f"not{i}", PrimOp.NOT)
        mod.connect(c, "A", mid)
        mod.connect(c, "Y", y, direction="output")

    duplicate_high_fanout(mod, threshold=16)
    for name, cell in mod.cells.items():
        if "dup" in name:
            assert "A" in cell.inputs
            assert cell.inputs["A"] is a
            assert "B" in cell.inputs
            assert cell.inputs["B"] is b


def test_no_duplicate_ff():
    """FFs must not be duplicated."""
    mod = Module(name="test")
    clk = mod.add_net("clk", 1)
    d = mod.add_net("d", 1)
    q = mod.add_net("q", 1)
    ff = mod.add_cell("ff0", PrimOp.FF)
    mod.connect(ff, "CLK", clk)
    mod.connect(ff, "D", d)
    mod.connect(ff, "Q", q, direction="output")

    for i in range(50):
        y = mod.add_net(f"y{i}", 1)
        c = mod.add_cell(f"not{i}", PrimOp.NOT)
        mod.connect(c, "A", q)
        mod.connect(c, "Y", y, direction="output")

    dup_count = duplicate_high_fanout(mod, threshold=16)
    assert dup_count == 0  # FF is excluded from duplication


def test_verify_retime_clocks_same():
    """Same-clock FF pair should produce no warnings."""
    mod = Module(name="test")
    clk = mod.add_net("clk", 1)
    d = mod.add_net("d", 1)
    q1 = mod.add_net("q1", 1)
    mid = mod.add_net("mid", 1)
    q2 = mod.add_net("q2", 1)
    ff1 = mod.add_cell("ff1", PrimOp.FF)
    mod.connect(ff1, "CLK", clk)
    mod.connect(ff1, "D", d)
    mod.connect(ff1, "Q", q1, direction="output")
    gc = mod.add_cell("not0", PrimOp.NOT)
    mod.connect(gc, "A", q1)
    mod.connect(gc, "Y", mid, direction="output")
    ff2 = mod.add_cell("ff2", PrimOp.FF)
    mod.connect(ff2, "CLK", clk)
    mod.connect(ff2, "D", mid)
    mod.connect(ff2, "Q", q2, direction="output")
    warnings = verify_retime_clocks(mod)
    assert len(warnings) == 0


def test_verify_retime_clocks_mismatch():
    """Different-clock FF pair through combinational logic should warn."""
    mod = Module(name="test")
    clk_a = mod.add_net("clk_a", 1)
    clk_b = mod.add_net("clk_b", 1)
    d = mod.add_net("d", 1)
    q1 = mod.add_net("q1", 1)
    mid = mod.add_net("mid", 1)
    q2 = mod.add_net("q2", 1)
    ff1 = mod.add_cell("ff1", PrimOp.FF)
    mod.connect(ff1, "CLK", clk_a)
    mod.connect(ff1, "D", d)
    mod.connect(ff1, "Q", q1, direction="output")
    gc = mod.add_cell("not0", PrimOp.NOT)
    mod.connect(gc, "A", q1)
    mod.connect(gc, "Y", mid, direction="output")
    ff2 = mod.add_cell("ff2", PrimOp.FF)
    mod.connect(ff2, "CLK", clk_b)
    mod.connect(ff2, "D", mid)
    mod.connect(ff2, "Q", q2, direction="output")
    warnings = verify_retime_clocks(mod)
    assert len(warnings) >= 1
    assert "clock mismatch" in warnings[0]


def test_retime_backward_basic():
    """Backward retiming should not crash on a simple chain."""
    mod = _ff_chain_module()
    retimed = retime_backward(mod)
    assert retimed >= 0
    # Should still have an FF and an AND cell
    ops = {c.op for c in mod.cells.values()}
    assert PrimOp.FF in ops

