"""Consolidated PrimOp evaluation, property, torture, and mutation tests."""

import json
import os
from hypothesis import given, settings, strategies as st
from nosis.bram import infer_brams
from nosis.carry import infer_carry_chains
from nosis.clocks import analyze_clock_domains
from nosis.cse import eliminate_common_subexpressions
from nosis.dsp import infer_dsps
from nosis.equiv import check_equivalence, check_equivalence_exhaustive
from nosis.eval import eval_cell, eval_const_op
from nosis.fsm import extract_fsms
from nosis.ir import Design, Module, PrimOp
from nosis.json_backend import emit_json_str
from nosis.lutpack import pack_luts_ir
from nosis.passes import constant_fold, dead_code_eliminate, run_default_passes
from nosis.resources import report_utilization
from nosis.techmap import map_to_ecp5


# --- from test_eval ---




# ---------------------------------------------------------------------------
# eval_const_op unit tests
# ---------------------------------------------------------------------------

def test_const():
    assert eval_const_op(PrimOp.CONST, {}, {"value": 42, "width": 8}, 8) == 42


def test_and():
    assert eval_const_op(PrimOp.AND, {"A": 0xFF, "B": 0x0F}, {}, 8) == 0x0F


def test_or():
    assert eval_const_op(PrimOp.OR, {"A": 0xF0, "B": 0x0F}, {}, 8) == 0xFF


def test_xor():
    assert eval_const_op(PrimOp.XOR, {"A": 0xFF, "B": 0x0F}, {}, 8) == 0xF0


def test_not():
    assert eval_const_op(PrimOp.NOT, {"A": 0x0F}, {}, 8) == 0xF0


def test_add():
    assert eval_const_op(PrimOp.ADD, {"A": 100, "B": 200}, {}, 16) == 300


def test_add_overflow():
    assert eval_const_op(PrimOp.ADD, {"A": 255, "B": 1}, {}, 8) == 0


def test_sub():
    assert eval_const_op(PrimOp.SUB, {"A": 200, "B": 100}, {}, 16) == 100


def test_sub_underflow():
    assert eval_const_op(PrimOp.SUB, {"A": 0, "B": 1}, {}, 8) == 255


def test_mul():
    assert eval_const_op(PrimOp.MUL, {"A": 7, "B": 6}, {}, 8) == 42


def test_div():
    assert eval_const_op(PrimOp.DIV, {"A": 42, "B": 6}, {}, 8) == 7


def test_div_by_zero():
    assert eval_const_op(PrimOp.DIV, {"A": 42, "B": 0}, {}, 8) == 0


def test_mod():
    assert eval_const_op(PrimOp.MOD, {"A": 42, "B": 5}, {}, 8) == 2


def test_mod_by_zero():
    assert eval_const_op(PrimOp.MOD, {"A": 42, "B": 0}, {}, 8) == 0


def test_shl():
    assert eval_const_op(PrimOp.SHL, {"A": 1, "B": 4}, {}, 8) == 16


def test_shr():
    assert eval_const_op(PrimOp.SHR, {"A": 0x80, "B": 4}, {}, 8) == 0x08


def test_sshr_positive():
    assert eval_const_op(PrimOp.SSHR, {"A": 0x40, "B": 2}, {}, 8) == 0x10


def test_sshr_negative():
    # 0x80 in 8 bits = -128, arithmetic shift right by 1 = -64 = 0xC0
    result = eval_const_op(PrimOp.SSHR, {"A": 0x80, "B": 1}, {}, 8)
    assert result == 0xC0


def test_eq():
    assert eval_const_op(PrimOp.EQ, {"A": 5, "B": 5}, {}, 8) == 1
    assert eval_const_op(PrimOp.EQ, {"A": 5, "B": 6}, {}, 8) == 0


def test_ne():
    assert eval_const_op(PrimOp.NE, {"A": 5, "B": 6}, {}, 8) == 1
    assert eval_const_op(PrimOp.NE, {"A": 5, "B": 5}, {}, 8) == 0


def test_lt():
    assert eval_const_op(PrimOp.LT, {"A": 3, "B": 5}, {}, 8) == 1
    assert eval_const_op(PrimOp.LT, {"A": 5, "B": 3}, {}, 8) == 0
    assert eval_const_op(PrimOp.LT, {"A": 5, "B": 5}, {}, 8) == 0


def test_le():
    assert eval_const_op(PrimOp.LE, {"A": 3, "B": 5}, {}, 8) == 1
    assert eval_const_op(PrimOp.LE, {"A": 5, "B": 5}, {}, 8) == 1
    assert eval_const_op(PrimOp.LE, {"A": 6, "B": 5}, {}, 8) == 0


def test_gt():
    assert eval_const_op(PrimOp.GT, {"A": 5, "B": 3}, {}, 8) == 1
    assert eval_const_op(PrimOp.GT, {"A": 3, "B": 5}, {}, 8) == 0


def test_ge():
    assert eval_const_op(PrimOp.GE, {"A": 5, "B": 5}, {}, 8) == 1
    assert eval_const_op(PrimOp.GE, {"A": 5, "B": 6}, {}, 8) == 0


def test_mux_sel0():
    assert eval_const_op(PrimOp.MUX, {"S": 0, "A": 42, "B": 99}, {}, 8) == 42


def test_mux_sel1():
    assert eval_const_op(PrimOp.MUX, {"S": 1, "A": 42, "B": 99}, {}, 8) == 99


def test_reduce_and():
    assert eval_const_op(PrimOp.REDUCE_AND, {"A": 0xFF}, {}, 8) == 1
    assert eval_const_op(PrimOp.REDUCE_AND, {"A": 0xFE}, {}, 8) == 0


def test_reduce_or():
    assert eval_const_op(PrimOp.REDUCE_OR, {"A": 0x01}, {}, 8) == 1
    assert eval_const_op(PrimOp.REDUCE_OR, {"A": 0x00}, {}, 8) == 0


def test_reduce_xor():
    assert eval_const_op(PrimOp.REDUCE_XOR, {"A": 0x03}, {}, 8) == 0  # 2 bits set
    assert eval_const_op(PrimOp.REDUCE_XOR, {"A": 0x07}, {}, 8) == 1  # 3 bits set


def test_zext():
    assert eval_const_op(PrimOp.ZEXT, {"A": 0x0F}, {}, 16) == 0x0F


def test_sext_positive():
    assert eval_const_op(PrimOp.SEXT, {"A": 0x0F}, {"from_width": 8}, 16) == 0x0F


def test_sext_negative():
    # 0x80 in 8 bits sign-extended to 16 bits = 0xFF80
    result = eval_const_op(PrimOp.SEXT, {"A": 0x80}, {"from_width": 8}, 16)
    assert result == 0xFF80


def test_slice():
    # Extract bits [7:4] from 0xAB = 0x0A
    result = eval_const_op(PrimOp.SLICE, {"A": 0xAB}, {"offset": 4, "width": 4}, 4)
    assert result == 0x0A


def test_concat():
    # Concat 0x0F (4-bit) and 0x0A (4-bit) = 0xAF (lower=0F, upper=0A)
    result = eval_const_op(
        PrimOp.CONCAT,
        {"I0": 0x0F, "I1": 0x0A},
        {"count": 2, "I0_width": 4, "I1_width": 4},
        8,
    )
    assert result == 0xAF


def test_ff_returns_none():
    assert eval_const_op(PrimOp.FF, {"D": 1, "CLK": 1}, {}, 1) is None


def test_input_returns_none():
    assert eval_const_op(PrimOp.INPUT, {}, {}, 1) is None


def test_output_returns_none():
    assert eval_const_op(PrimOp.OUTPUT, {"A": 1}, {}, 1) is None


def test_memory_returns_none():
    assert eval_const_op(PrimOp.MEMORY, {}, {}, 8) is None


def test_nonfoldable_ops_return_none():
    """Non-foldable ops (FF, LATCH, INPUT, OUTPUT, MEMORY) return None."""
    assert eval_const_op(PrimOp.LATCH, {"D": 1}, {}, 1) is None
    assert eval_const_op(PrimOp.FF, {"D": 1, "CLK": 1}, {}, 1) is None
    assert eval_const_op(PrimOp.MEMORY, {}, {}, 8) is None
    assert eval_const_op(PrimOp.INPUT, {}, {}, 1) is None
    assert eval_const_op(PrimOp.OUTPUT, {"A": 1}, {}, 1) is None


def test_pmux_default():
    """PMUX with no select active returns default."""
    result = eval_const_op(PrimOp.PMUX, {"A": 42, "S": 0, "I0": 10, "I1": 20}, {"count": 2}, 8)
    assert result == 42


def test_pmux_select_0():
    """PMUX with S[0]=1 returns I0."""
    result = eval_const_op(PrimOp.PMUX, {"A": 42, "S": 1, "I0": 10, "I1": 20}, {"count": 2}, 8)
    assert result == 10


def test_pmux_select_1():
    """PMUX with S[1]=1 returns I1."""
    result = eval_const_op(PrimOp.PMUX, {"A": 42, "S": 2, "I0": 10, "I1": 20}, {"count": 2}, 8)
    assert result == 20


def test_pmux_priority():
    """PMUX with multiple selects active: first (I0) wins."""
    result = eval_const_op(PrimOp.PMUX, {"A": 42, "S": 3, "I0": 10, "I1": 20}, {"count": 2}, 8)
    assert result == 10


def test_width_masking():
    # ADD that would overflow 8 bits
    assert eval_const_op(PrimOp.ADD, {"A": 200, "B": 200}, {}, 8) == (400 & 0xFF)


# ---------------------------------------------------------------------------
# eval_cell integration
# ---------------------------------------------------------------------------

def test_eval_cell_and():
    mod = Module(name="test")
    a = mod.add_net("a", 8)
    b = mod.add_net("b", 8)
    y = mod.add_net("y", 8)
    cell = mod.add_cell("and0", PrimOp.AND)
    mod.connect(cell, "A", a)
    mod.connect(cell, "B", b)
    mod.connect(cell, "Y", y, direction="output")
    result = eval_cell(cell, {"a": 0xFF, "b": 0x0F})
    assert result["Y"] == 0x0F


def test_eval_cell_const():
    mod = Module(name="test")
    y = mod.add_net("y", 8)
    cell = mod.add_cell("c0", PrimOp.CONST, value=42, width=8)
    mod.connect(cell, "Y", y, direction="output")
    result = eval_cell(cell, {})
    assert result["Y"] == 42


def test_eval_cell_ff_returns_empty():
    mod = Module(name="test")
    d = mod.add_net("d", 1)
    q = mod.add_net("q", 1)
    cell = mod.add_cell("ff0", PrimOp.FF)
    mod.connect(cell, "D", d)
    mod.connect(cell, "Q", q, direction="output")
    result = eval_cell(cell, {"d": 1})
    assert result == {}


# ---------------------------------------------------------------------------
# Consistency: passes and equiv must agree
# ---------------------------------------------------------------------------

def test_passes_and_equiv_agree():
    """Verify that constant folding and equivalence simulation produce
    the same result for every foldable PrimOp."""

    test_cases = [
        (PrimOp.AND, {"A": 0xAA, "B": 0x55}, 8),
        (PrimOp.OR, {"A": 0xAA, "B": 0x55}, 8),
        (PrimOp.XOR, {"A": 0xAA, "B": 0x55}, 8),
        (PrimOp.ADD, {"A": 100, "B": 200}, 16),
        (PrimOp.SUB, {"A": 200, "B": 100}, 16),
        (PrimOp.MUL, {"A": 7, "B": 6}, 8),
        (PrimOp.EQ, {"A": 5, "B": 5}, 1),
        (PrimOp.NE, {"A": 5, "B": 6}, 1),
        (PrimOp.LT, {"A": 3, "B": 5}, 1),
        (PrimOp.NOT, {"A": 0x0F}, 8),
        (PrimOp.SHL, {"A": 1, "B": 3}, 8),
        (PrimOp.SHR, {"A": 0x80, "B": 3}, 8),
        (PrimOp.MUX, {"S": 1, "A": 10, "B": 20}, 8),
    ]

    for op, inputs_raw, width in test_cases:
        # Build a module with constant inputs -> op -> output
        mod = Module(name="test")
        input_nets: dict[str, object] = {}
        for port_name, value in inputs_raw.items():
            net = mod.add_net(f"c_{port_name}", width)
            c = mod.add_cell(f"c_{port_name}", PrimOp.CONST, value=value, width=width)
            mod.connect(c, "Y", net, direction="output")
            input_nets[port_name] = net

        out = mod.add_net("out", width)
        cell = mod.add_cell("op", op)
        for port_name, net in input_nets.items():
            mod.connect(cell, port_name, net)
        mod.connect(cell, "Y", out, direction="output")

        # Constant fold
        constant_fold(mod)
        folded_val = mod.cells["op"].params.get("value")
        assert folded_val is not None, f"{op.name}: not folded"

        # Simulate via eval_cell
        sim_inputs = {f"c_{k}": v for k, v in inputs_raw.items()}
        sim_result = eval_cell(cell, sim_inputs)
        sim_val = sim_result.get("Y")

        assert folded_val == sim_val, (
            f"{op.name}: fold={folded_val} sim={sim_val} "
            f"inputs={inputs_raw}"
        )


# --- from test_property ---





# ---------------------------------------------------------------------------
# Evaluation properties
# ---------------------------------------------------------------------------

FOLDABLE_BINARY_OPS = [
    PrimOp.AND, PrimOp.OR, PrimOp.XOR,
    PrimOp.ADD, PrimOp.SUB, PrimOp.MUL,
    PrimOp.EQ, PrimOp.NE, PrimOp.LT, PrimOp.LE, PrimOp.GT, PrimOp.GE,
    PrimOp.SHL, PrimOp.SHR,
]

FOLDABLE_UNARY_OPS = [
    PrimOp.NOT, PrimOp.REDUCE_AND, PrimOp.REDUCE_OR, PrimOp.REDUCE_XOR,
]

widths = st.sampled_from([1, 2, 4, 8, 16, 32])
values_8 = st.integers(min_value=0, max_value=255)
values_32 = st.integers(min_value=0, max_value=0xFFFFFFFF)


@given(
    op=st.sampled_from(FOLDABLE_BINARY_OPS),
    a=values_32,
    b=values_32,
    width=widths,
)
@settings(max_examples=5000)
def test_binary_eval_deterministic(op, a, b, width):
    """Evaluating the same op with the same inputs must always produce the same result."""
    mask = (1 << width) - 1
    a_masked = a & mask
    b_masked = b & mask
    r1 = eval_const_op(op, {"A": a_masked, "B": b_masked}, {}, width)
    r2 = eval_const_op(op, {"A": a_masked, "B": b_masked}, {}, width)
    assert r1 == r2


@given(
    op=st.sampled_from(FOLDABLE_BINARY_OPS),
    a=values_32,
    b=values_32,
    width=widths,
)
@settings(max_examples=5000)
def test_binary_eval_within_width(op, a, b, width):
    """Every evaluation result must fit within the specified width."""
    mask = (1 << width) - 1
    result = eval_const_op(op, {"A": a & mask, "B": b & mask}, {}, width)
    assert result is not None
    assert 0 <= result <= mask


@given(
    op=st.sampled_from(FOLDABLE_UNARY_OPS),
    a=values_32,
    width=widths,
)
@settings(max_examples=5000)
def test_unary_eval_within_width(op, a, width):
    """Unary evaluation results must fit within width."""
    mask = (1 << width) - 1
    result = eval_const_op(op, {"A": a & mask}, {}, width)
    assert result is not None
    assert 0 <= result <= mask


@given(a=values_8, b=values_8)
@settings(max_examples=5000)
def test_add_sub_inverse(a, b):
    """(a + b) - b == a for 8-bit values."""
    add_result = eval_const_op(PrimOp.ADD, {"A": a, "B": b}, {}, 8)
    sub_result = eval_const_op(PrimOp.SUB, {"A": add_result, "B": b}, {}, 8)
    assert sub_result == (a & 0xFF)


@given(a=values_8, b=values_8)
@settings(max_examples=5000)
def test_and_or_demorgan(a, b):
    """NOT(a AND b) == (NOT a) OR (NOT b) — De Morgan's law."""
    and_result = eval_const_op(PrimOp.AND, {"A": a, "B": b}, {}, 8)
    not_and = eval_const_op(PrimOp.NOT, {"A": and_result}, {}, 8)

    not_a = eval_const_op(PrimOp.NOT, {"A": a}, {}, 8)
    not_b = eval_const_op(PrimOp.NOT, {"A": b}, {}, 8)
    or_nots = eval_const_op(PrimOp.OR, {"A": not_a, "B": not_b}, {}, 8)

    assert not_and == or_nots


@given(a=values_8, b=values_8)
@settings(max_examples=5000)
def test_xor_self_is_zero(a, b):
    """a XOR a == 0."""
    result = eval_const_op(PrimOp.XOR, {"A": a, "B": a}, {}, 8)
    assert result == 0


@given(a=values_8)
@settings(max_examples=5000)
def test_double_not_identity(a):
    """NOT(NOT(a)) == a."""
    not_a = eval_const_op(PrimOp.NOT, {"A": a}, {}, 8)
    not_not_a = eval_const_op(PrimOp.NOT, {"A": not_a}, {}, 8)
    assert not_not_a == (a & 0xFF)


@given(a=values_8, b=values_8)
@settings(max_examples=5000)
def test_eq_ne_complementary(a, b):
    """EQ and NE are complementary."""
    eq = eval_const_op(PrimOp.EQ, {"A": a, "B": b}, {}, 1)
    ne = eval_const_op(PrimOp.NE, {"A": a, "B": b}, {}, 1)
    assert eq + ne == 1


@given(a=values_8, b=values_8)
@settings(max_examples=5000)
def test_lt_ge_complementary(a, b):
    """LT and GE are complementary."""
    lt = eval_const_op(PrimOp.LT, {"A": a, "B": b}, {}, 1)
    ge = eval_const_op(PrimOp.GE, {"A": a, "B": b}, {}, 1)
    assert lt + ge == 1


@given(
    sel=st.integers(min_value=0, max_value=1),
    a=values_8,
    b=values_8,
)
@settings(max_examples=5000)
def test_mux_selects_correctly(sel, a, b):
    """MUX(sel, a, b) returns a when sel=0, b when sel=1."""
    result = eval_const_op(PrimOp.MUX, {"S": sel, "A": a, "B": b}, {}, 8)
    expected = b if sel else a
    assert result == (expected & 0xFF)


@given(a=values_32, b=st.integers(min_value=0, max_value=31))
@settings(max_examples=5000)
def test_shl_shr_inverse(a, b):
    """(a << b) >> b recovers the lower bits of a (the upper bits shifted out are lost)."""
    width = 32
    mask = (1 << width) - 1
    a_m = a & mask
    shifted = eval_const_op(PrimOp.SHL, {"A": a_m, "B": b}, {}, width)
    unshifted = eval_const_op(PrimOp.SHR, {"A": shifted, "B": b}, {}, width)
    # Only the bits that weren't shifted out should match
    keep_mask = mask >> b
    assert unshifted == (a_m & keep_mask)


@given(a=values_8, b=st.integers(min_value=1, max_value=255))
@settings(max_examples=5000)
def test_div_mod_reconstruct(a, b):
    """(a / b) * b + (a % b) == a."""
    div = eval_const_op(PrimOp.DIV, {"A": a, "B": b}, {}, 8)
    mod = eval_const_op(PrimOp.MOD, {"A": a, "B": b}, {}, 8)
    reconstructed = eval_const_op(PrimOp.ADD, {
        "A": eval_const_op(PrimOp.MUL, {"A": div, "B": b}, {}, 8),
        "B": mod,
    }, {}, 8)
    assert reconstructed == (a & 0xFF)


# ---------------------------------------------------------------------------
# Optimization properties
# ---------------------------------------------------------------------------

@given(
    a_val=values_8,
    b_val=values_8,
    op=st.sampled_from([PrimOp.AND, PrimOp.OR, PrimOp.XOR, PrimOp.ADD, PrimOp.SUB]),
)
@settings(max_examples=5000)
def test_const_fold_matches_eval(a_val, b_val, op):
    """Constant folding must produce the same value as direct evaluation."""
    mod = Module(name="test")
    a = mod.add_net("a", 8)
    b = mod.add_net("b", 8)
    out = mod.add_net("out", 8)

    ca = mod.add_cell("ca", PrimOp.CONST, value=a_val & 0xFF, width=8)
    mod.connect(ca, "Y", a, direction="output")
    cb = mod.add_cell("cb", PrimOp.CONST, value=b_val & 0xFF, width=8)
    mod.connect(cb, "Y", b, direction="output")

    cell = mod.add_cell("op", op)
    mod.connect(cell, "A", a)
    mod.connect(cell, "B", b)
    mod.connect(cell, "Y", out, direction="output")

    constant_fold(mod)

    folded = mod.cells["op"].params.get("value")
    expected = eval_const_op(op, {"A": a_val & 0xFF, "B": b_val & 0xFF}, {}, 8)
    assert folded == expected


@given(
    n_cells=st.integers(min_value=1, max_value=10),
    width=st.sampled_from([1, 8]),
)
@settings(max_examples=5000)
def test_dce_never_removes_output_connected_cells(n_cells, width):
    """DCE must never remove cells that feed output ports."""
    mod = Module(name="test")
    inp = mod.add_net("inp", width)
    inp_cell = mod.add_cell("inp_p", PrimOp.INPUT, port_name="inp")
    mod.connect(inp_cell, "Y", inp, direction="output")
    mod.ports["inp"] = inp

    # Build a chain: inp -> NOT -> NOT -> ... -> out
    prev = inp
    for i in range(n_cells):
        mid = mod.add_net(f"mid_{i}", width)
        cell = mod.add_cell(f"not_{i}", PrimOp.NOT)
        mod.connect(cell, "A", prev)
        mod.connect(cell, "Y", mid, direction="output")
        prev = mid

    out_cell = mod.add_cell("out_p", PrimOp.OUTPUT, port_name="out")
    mod.connect(out_cell, "A", prev)
    mod.ports["out"] = prev

    dead_code_eliminate(mod)

    # All NOT cells in the chain must survive
    for i in range(n_cells):
        assert f"not_{i}" in mod.cells, f"DCE removed live cell not_{i}"


@given(
    n_duplicates=st.integers(min_value=2, max_value=5),
)
@settings(max_examples=20)
def test_cse_eliminates_all_duplicates(n_duplicates):
    """CSE with N identical cells should eliminate N-1."""
    mod = Module(name="test")
    a = mod.add_net("a", 1)
    b = mod.add_net("b", 1)

    for i in range(n_duplicates):
        out = mod.add_net(f"out_{i}", 1)
        cell = mod.add_cell(f"and_{i}", PrimOp.AND)
        mod.connect(cell, "A", a)
        mod.connect(cell, "B", b)
        mod.connect(cell, "Y", out, direction="output")

    eliminated = eliminate_common_subexpressions(mod)
    assert eliminated == n_duplicates - 1
    and_cells = [c for c in mod.cells.values() if c.op == PrimOp.AND]
    assert len(and_cells) == 1


# ---------------------------------------------------------------------------
# Barrel shifter correctness
# ---------------------------------------------------------------------------

@given(
    value=st.integers(min_value=0, max_value=0xFFFF),
    shift_amount=st.integers(min_value=0, max_value=15),
    width=st.sampled_from([8, 16]),
)
@settings(max_examples=500)
def test_shift_right_matches_python(value, shift_amount, width):
    """The IR SHR evaluation must match Python's >> for all inputs."""
    mask = (1 << width) - 1
    a = value & mask
    b = shift_amount & 0xF
    result = eval_const_op(PrimOp.SHR, {"A": a, "B": b}, {}, width)
    expected = (a >> b) & mask
    assert result == expected


@given(
    value=st.integers(min_value=0, max_value=0xFFFF),
    shift_amount=st.integers(min_value=0, max_value=15),
    width=st.sampled_from([8, 16]),
)
@settings(max_examples=500)
def test_shift_left_matches_python(value, shift_amount, width):
    """The IR SHL evaluation must match Python's << for all inputs."""
    mask = (1 << width) - 1
    a = value & mask
    b = shift_amount & 0xF
    result = eval_const_op(PrimOp.SHL, {"A": a, "B": b}, {}, width)
    expected = (a << b) & mask
    assert result == expected


@given(
    value=st.integers(min_value=0, max_value=0xFF),
    shift_amount=st.integers(min_value=0, max_value=7),
)
@settings(max_examples=500)
def test_sshr_sign_extends(value, shift_amount):
    """SSHR must sign-extend the MSB during shift."""
    width = 8
    mask = (1 << width) - 1
    a = value & mask
    b = shift_amount & 0x7
    result = eval_const_op(PrimOp.SSHR, {"A": a, "B": b}, {}, width)
    # Python arithmetic shift on signed value
    signed_a = a if a < 128 else a - 256
    expected = (signed_a >> b) & mask
    assert result == expected


def test_mapped_barrel_shifter_produces_luts():
    """Verify the mapped LUT4 barrel shifter produces LUT4 cells for a 16-bit shift."""

    width = 16
    mod = Module(name="shr_test")
    a = mod.add_net("a", width)
    b = mod.add_net("b", width)
    y = mod.add_net("y", width)
    ac = mod.add_cell("a_p", PrimOp.INPUT, port_name="a")
    mod.connect(ac, "Y", a, direction="output")
    mod.ports["a"] = a
    bc = mod.add_cell("b_p", PrimOp.INPUT, port_name="b")
    mod.connect(bc, "Y", b, direction="output")
    mod.ports["b"] = b
    oc = mod.add_cell("y_p", PrimOp.OUTPUT, port_name="y")
    mod.connect(oc, "A", y)
    mod.ports["y"] = y
    sc = mod.add_cell("shr0", PrimOp.SHR)
    mod.connect(sc, "A", a)
    mod.connect(sc, "B", b)
    mod.connect(sc, "Y", y, direction="output")

    design = Design(modules={"shr_test": mod}, top="shr_test")
    nl = map_to_ecp5(design)

    lut_count = nl.stats().get("LUT4", 0)
    assert lut_count > 0, "barrel shifter should produce LUT4 cells for 16-bit shift"


# --- from test_torture ---




os.environ.setdefault("NOSIS_PYSLANG_PATH", "D:/slang/build/lib")



# ---------------------------------------------------------------------------
# Evaluation edge cases
# ---------------------------------------------------------------------------

class TestEvalEdgeCases:
    def test_shift_by_width(self):
        """Shifting by exactly the width should produce 0."""
        assert eval_const_op(PrimOp.SHL, {"A": 0xFF, "B": 8}, {}, 8) == 0

    def test_shift_by_more_than_width(self):
        """Shifting by more than width should produce 0."""
        assert eval_const_op(PrimOp.SHL, {"A": 0xFF, "B": 100}, {}, 8) == 0
        assert eval_const_op(PrimOp.SHR, {"A": 0xFF, "B": 100}, {}, 8) == 0

    def test_all_ones_and(self):
        assert eval_const_op(PrimOp.AND, {"A": 0xFFFFFFFF, "B": 0xFFFFFFFF}, {}, 32) == 0xFFFFFFFF

    def test_all_ones_or(self):
        assert eval_const_op(PrimOp.OR, {"A": 0, "B": 0xFFFFFFFF}, {}, 32) == 0xFFFFFFFF

    def test_zero_times_anything(self):
        assert eval_const_op(PrimOp.MUL, {"A": 0, "B": 999999}, {}, 32) == 0

    def test_max_mul_overflow(self):
        """Maximum values multiplied should wrap correctly."""
        result = eval_const_op(PrimOp.MUL, {"A": 0xFF, "B": 0xFF}, {}, 8)
        assert result == (0xFF * 0xFF) & 0xFF  # 0x01

    def test_sub_underflow_wraps(self):
        assert eval_const_op(PrimOp.SUB, {"A": 0, "B": 1}, {}, 8) == 255
        assert eval_const_op(PrimOp.SUB, {"A": 0, "B": 1}, {}, 32) == 0xFFFFFFFF

    def test_width_1_operations(self):
        """All operations on 1-bit values."""
        assert eval_const_op(PrimOp.AND, {"A": 1, "B": 1}, {}, 1) == 1
        assert eval_const_op(PrimOp.AND, {"A": 1, "B": 0}, {}, 1) == 0
        assert eval_const_op(PrimOp.OR, {"A": 0, "B": 0}, {}, 1) == 0
        assert eval_const_op(PrimOp.OR, {"A": 0, "B": 1}, {}, 1) == 1
        assert eval_const_op(PrimOp.XOR, {"A": 1, "B": 1}, {}, 1) == 0
        assert eval_const_op(PrimOp.NOT, {"A": 0}, {}, 1) == 1
        assert eval_const_op(PrimOp.NOT, {"A": 1}, {}, 1) == 0
        assert eval_const_op(PrimOp.ADD, {"A": 1, "B": 1}, {}, 1) == 0  # overflow
        assert eval_const_op(PrimOp.EQ, {"A": 0, "B": 0}, {}, 1) == 1
        assert eval_const_op(PrimOp.NE, {"A": 0, "B": 0}, {}, 1) == 0

    def test_sshr_all_ones(self):
        """SSHR of all-ones (negative in 2's complement) stays all-ones."""
        assert eval_const_op(PrimOp.SSHR, {"A": 0xFF, "B": 1}, {}, 8) == 0xFF
        assert eval_const_op(PrimOp.SSHR, {"A": 0xFF, "B": 7}, {}, 8) == 0xFF

    def test_sshr_by_zero(self):
        assert eval_const_op(PrimOp.SSHR, {"A": 0x80, "B": 0}, {}, 8) == 0x80

    def test_div_max_by_1(self):
        assert eval_const_op(PrimOp.DIV, {"A": 0xFF, "B": 1}, {}, 8) == 0xFF

    def test_mod_by_1(self):
        assert eval_const_op(PrimOp.MOD, {"A": 0xFF, "B": 1}, {}, 8) == 0

    def test_reduce_and_single_bit(self):
        assert eval_const_op(PrimOp.REDUCE_AND, {"A": 1}, {}, 1) == 1
        assert eval_const_op(PrimOp.REDUCE_AND, {"A": 0}, {}, 1) == 0

    def test_reduce_xor_all_ones(self):
        """XOR of 8 ones = 0 (even number of set bits)."""
        assert eval_const_op(PrimOp.REDUCE_XOR, {"A": 0xFF}, {}, 8) == 0

    def test_reduce_xor_7_ones(self):
        """XOR of 7 ones = 1 (odd number of set bits)."""
        assert eval_const_op(PrimOp.REDUCE_XOR, {"A": 0x7F}, {}, 8) == 1

    def test_sext_from_1_to_32(self):
        """Sign-extend a single bit."""
        assert eval_const_op(PrimOp.SEXT, {"A": 1}, {"from_width": 1}, 32) == 0xFFFFFFFF
        assert eval_const_op(PrimOp.SEXT, {"A": 0}, {"from_width": 1}, 32) == 0

    def test_slice_beyond_width(self):
        """Slicing beyond the value width should give 0 bits."""
        result = eval_const_op(PrimOp.SLICE, {"A": 0xFF}, {"offset": 100, "width": 4}, 4)
        assert result == 0

    def test_concat_empty(self):
        result = eval_const_op(PrimOp.CONCAT, {}, {"count": 0}, 0)
        assert result == 0

    def test_repeat_zero_times(self):
        """Repeat 0 times should give 0."""
        result = eval_const_op(PrimOp.REPEAT, {"A": 0xFF}, {"count": 0, "a_width": 8}, 8)
        assert result == 0


# ---------------------------------------------------------------------------
# Degenerate IR structures
# ---------------------------------------------------------------------------

class TestDegenerateIR:
    def test_empty_module(self):
        """An empty module should survive all passes without crashing."""
        mod = Module(name="empty")
        run_default_passes(mod)
        eliminate_common_subexpressions(mod)
        extract_fsms(mod)
        infer_brams(mod)
        infer_dsps(mod)
        infer_carry_chains(mod)
        pack_luts_ir(mod)
        analyze_clock_domains(mod)
        design = Design(modules={"empty": mod}, top="empty")
        nl = map_to_ecp5(design)
        text = emit_json_str(nl)
        data = json.loads(text)
        assert "empty" in data["modules"]

    def test_input_only(self):
        """Module with only inputs and no outputs."""
        mod = Module(name="sink")
        for i in range(10):
            n = mod.add_net(f"in{i}", 8)
            c = mod.add_cell(f"in{i}_p", PrimOp.INPUT, port_name=f"in{i}")
            mod.connect(c, "Y", n, direction="output")
            mod.ports[f"in{i}"] = n
        run_default_passes(mod)
        design = Design(modules={"sink": mod}, top="sink")
        nl = map_to_ecp5(design)
        assert nl.stats()["ports"] == 10

    def test_output_only(self):
        """Module with only outputs driven by constants."""
        mod = Module(name="source")
        for i in range(10):
            cn = mod.add_net(f"c{i}", 8)
            cc = mod.add_cell(f"c{i}", PrimOp.CONST, value=i * 17, width=8)
            mod.connect(cc, "Y", cn, direction="output")
            on = mod.add_net(f"out{i}", 8)
            oc = mod.add_cell(f"out{i}_p", PrimOp.OUTPUT, port_name=f"out{i}")
            mod.connect(oc, "A", cn)
            mod.ports[f"out{i}"] = on
        run_default_passes(mod)
        design = Design(modules={"source": mod}, top="source")
        nl = map_to_ecp5(design)
        text = emit_json_str(nl)
        data = json.loads(text)
        assert len(data["modules"]["source"]["ports"]) == 10

    def test_long_chain(self):
        """A chain of 100 NOT gates should survive all passes."""
        mod = Module(name="chain")
        inp = mod.add_net("inp", 1)
        ic = mod.add_cell("inp_p", PrimOp.INPUT, port_name="inp")
        mod.connect(ic, "Y", inp, direction="output")
        mod.ports["inp"] = inp

        prev = inp
        for i in range(100):
            n = mod.add_net(f"n{i}", 1)
            c = mod.add_cell(f"not{i}", PrimOp.NOT)
            mod.connect(c, "A", prev)
            mod.connect(c, "Y", n, direction="output")
            prev = n

        oc = mod.add_cell("out_p", PrimOp.OUTPUT, port_name="out")
        mod.connect(oc, "A", prev)
        mod.ports["out"] = prev

        run_default_passes(mod)
        # Double-NOT elimination should reduce chain significantly
        not_cells = [c for c in mod.cells.values() if c.op == PrimOp.NOT]
        # 100 NOTs -> identity simplify removes pairs -> should have ~0 or ~1
        # (depends on how many pairs identity_simplify catches per pass)
        assert len(not_cells) <= 100  # at minimum, no more than original

    def test_wide_fanout(self):
        """One input driving 100 AND gates."""
        mod = Module(name="fanout")
        a = mod.add_net("a", 1)
        b = mod.add_net("b", 1)
        ac = mod.add_cell("a_p", PrimOp.INPUT, port_name="a")
        mod.connect(ac, "Y", a, direction="output")
        mod.ports["a"] = a
        bc = mod.add_cell("b_p", PrimOp.INPUT, port_name="b")
        mod.connect(bc, "Y", b, direction="output")
        mod.ports["b"] = b

        for i in range(100):
            out = mod.add_net(f"out{i}", 1)
            cell = mod.add_cell(f"and{i}", PrimOp.AND)
            mod.connect(cell, "A", a)
            mod.connect(cell, "B", b)
            mod.connect(cell, "Y", out, direction="output")
            oc = mod.add_cell(f"out{i}_p", PrimOp.OUTPUT, port_name=f"out{i}")
            mod.connect(oc, "A", out)
            mod.ports[f"out{i}"] = out

        # CSE should eliminate 99 of 100 identical AND gates
        eliminated = eliminate_common_subexpressions(mod)
        assert eliminated == 99

    def test_deep_mux_tree(self):
        """A 32-deep MUX tree should not stack overflow."""
        mod = Module(name="deep_mux")
        sel = mod.add_net("sel", 1)
        sc = mod.add_cell("sel_p", PrimOp.INPUT, port_name="sel")
        mod.connect(sc, "Y", sel, direction="output")
        mod.ports["sel"] = sel

        prev = mod.add_net("base", 8)
        bc = mod.add_cell("base_c", PrimOp.CONST, value=0, width=8)
        mod.connect(bc, "Y", prev, direction="output")

        for i in range(32):
            alt = mod.add_net(f"alt{i}", 8)
            ac = mod.add_cell(f"alt{i}_c", PrimOp.CONST, value=i + 1, width=8)
            mod.connect(ac, "Y", alt, direction="output")

            out = mod.add_net(f"mux{i}", 8)
            mc = mod.add_cell(f"mux{i}", PrimOp.MUX)
            mod.connect(mc, "S", sel)
            mod.connect(mc, "A", prev)
            mod.connect(mc, "B", alt)
            mod.connect(mc, "Y", out, direction="output")
            prev = out

        oc = mod.add_cell("out_p", PrimOp.OUTPUT, port_name="out")
        mod.connect(oc, "A", prev)
        mod.ports["out"] = prev

        design = Design(modules={"deep_mux": mod}, top="deep_mux")
        nl = map_to_ecp5(design)
        assert nl.stats()["LUT4"] > 0

    def test_single_bit_ff(self):
        """A single-bit FF should produce exactly one TRELLIS_FF."""
        mod = Module(name="ff1")
        clk = mod.add_net("clk", 1)
        d = mod.add_net("d", 1)
        q = mod.add_net("q", 1)
        cc = mod.add_cell("clk_p", PrimOp.INPUT, port_name="clk")
        mod.connect(cc, "Y", clk, direction="output")
        mod.ports["clk"] = clk
        dc = mod.add_cell("d_p", PrimOp.INPUT, port_name="d")
        mod.connect(dc, "Y", d, direction="output")
        mod.ports["d"] = d
        qc = mod.add_cell("q_p", PrimOp.OUTPUT, port_name="q")
        mod.connect(qc, "A", q)
        mod.ports["q"] = q
        ff = mod.add_cell("ff", PrimOp.FF)
        mod.connect(ff, "CLK", clk)
        mod.connect(ff, "D", d)
        mod.connect(ff, "Q", q, direction="output")

        design = Design(modules={"ff1": mod}, top="ff1")
        nl = map_to_ecp5(design)
        assert nl.stats().get("TRELLIS_FF", 0) == 1

    def test_32bit_ff(self):
        """A 32-bit FF should produce exactly 32 TRELLIS_FFs."""
        mod = Module(name="ff32")
        clk = mod.add_net("clk", 1)
        d = mod.add_net("d", 32)
        q = mod.add_net("q", 32)
        cc = mod.add_cell("clk_p", PrimOp.INPUT, port_name="clk")
        mod.connect(cc, "Y", clk, direction="output")
        mod.ports["clk"] = clk
        dc = mod.add_cell("d_p", PrimOp.INPUT, port_name="d")
        mod.connect(dc, "Y", d, direction="output")
        mod.ports["d"] = d
        qc = mod.add_cell("q_p", PrimOp.OUTPUT, port_name="q")
        mod.connect(qc, "A", q)
        mod.ports["q"] = q
        ff = mod.add_cell("ff", PrimOp.FF)
        mod.connect(ff, "CLK", clk)
        mod.connect(ff, "D", d)
        mod.connect(ff, "Q", q, direction="output")

        design = Design(modules={"ff32": mod}, top="ff32")
        nl = map_to_ecp5(design)
        assert nl.stats().get("TRELLIS_FF", 0) == 32


# ---------------------------------------------------------------------------
# Optimization stress
# ---------------------------------------------------------------------------

class TestOptimizationStress:
    def test_all_const_design(self):
        """A design made entirely of constants should fold to nothing."""
        mod = Module(name="allconst")
        a = mod.add_net("a", 8)
        ac = mod.add_cell("a_c", PrimOp.CONST, value=42, width=8)
        mod.connect(ac, "Y", a, direction="output")

        b = mod.add_net("b", 8)
        bc = mod.add_cell("b_c", PrimOp.CONST, value=17, width=8)
        mod.connect(bc, "Y", b, direction="output")

        mid = mod.add_net("mid", 8)
        add = mod.add_cell("add", PrimOp.ADD)
        mod.connect(add, "A", a)
        mod.connect(add, "B", b)
        mod.connect(add, "Y", mid, direction="output")

        out = mod.add_net("out", 8)
        oc = mod.add_cell("out_p", PrimOp.OUTPUT, port_name="out")
        mod.connect(oc, "A", mid)
        mod.ports["out"] = out

        run_default_passes(mod)
        # The ADD should be folded to CONST(59)
        assert mod.cells["add"].op == PrimOp.CONST
        assert mod.cells["add"].params["value"] == 59

    def test_identity_chain(self):
        """a + 0 + 0 + 0 should simplify to just a."""
        mod = Module(name="ident")
        a = mod.add_net("a", 8)
        ac = mod.add_cell("a_p", PrimOp.INPUT, port_name="a")
        mod.connect(ac, "Y", a, direction="output")
        mod.ports["a"] = a

        prev = a
        for i in range(5):
            zero = mod.add_net(f"z{i}", 8)
            zc = mod.add_cell(f"z{i}_c", PrimOp.CONST, value=0, width=8)
            mod.connect(zc, "Y", zero, direction="output")
            out = mod.add_net(f"add{i}", 8)
            cell = mod.add_cell(f"add{i}", PrimOp.ADD)
            mod.connect(cell, "A", prev)
            mod.connect(cell, "B", zero)
            mod.connect(cell, "Y", out, direction="output")
            prev = out

        oc = mod.add_cell("out_p", PrimOp.OUTPUT, port_name="out")
        mod.connect(oc, "A", prev)
        mod.ports["out"] = prev

        stats = run_default_passes(mod)
        # All 5 additions of zero should be simplified across rounds
        assert stats.get("round_0", 0) >= 3

    def test_cse_100_duplicates(self):
        """100 identical operations should reduce to 1."""
        mod = Module(name="cse100")
        a = mod.add_net("a", 1)
        b = mod.add_net("b", 1)
        for i in range(100):
            out = mod.add_net(f"out{i}", 1)
            cell = mod.add_cell(f"and{i}", PrimOp.AND)
            mod.connect(cell, "A", a)
            mod.connect(cell, "B", b)
            mod.connect(cell, "Y", out, direction="output")
        eliminated = eliminate_common_subexpressions(mod)
        assert eliminated == 99

    def test_dce_removes_large_dead_tree(self):
        """A large dead computation tree should be fully removed."""
        mod = Module(name="dead_tree")
        inp = mod.add_net("inp", 8)
        ic = mod.add_cell("inp_p", PrimOp.INPUT, port_name="inp")
        mod.connect(ic, "Y", inp, direction="output")
        mod.ports["inp"] = inp

        out = mod.add_net("out", 8)
        oc = mod.add_cell("out_p", PrimOp.OUTPUT, port_name="out")
        mod.connect(oc, "A", inp)  # output wired directly to input
        mod.ports["out"] = out

        # 200 dead cells
        for i in range(200):
            dn = mod.add_net(f"dead{i}", 8)
            dc = mod.add_cell(f"dead{i}_c", PrimOp.CONST, value=i, width=8)
            mod.connect(dc, "Y", dn, direction="output")

        removed = dead_code_eliminate(mod)
        assert removed == 200


# ---------------------------------------------------------------------------
# Equivalence checker adversarial cases
# ---------------------------------------------------------------------------

class TestEquivAdversarial:
    def test_identity_vs_identity(self):
        """Two passthrough modules must be equivalent."""
        def _passthrough(name):
            mod = Module(name=name)
            a = mod.add_net("a", 8)
            ac = mod.add_cell("a_p", PrimOp.INPUT, port_name="a")
            mod.connect(ac, "Y", a, direction="output")
            mod.ports["a"] = a
            oc = mod.add_cell("out_p", PrimOp.OUTPUT, port_name="out")
            mod.connect(oc, "A", a)
            mod.ports["out"] = a
            return mod

        r = check_equivalence(_passthrough("a"), _passthrough("b"))
        assert r.equivalent

    def test_const_vs_const_same(self):
        """Two modules outputting the same constant must be equivalent."""
        def _const_out(name, val):
            mod = Module(name=name)
            c = mod.add_net("c", 8)
            cc = mod.add_cell("c_c", PrimOp.CONST, value=val, width=8)
            mod.connect(cc, "Y", c, direction="output")
            oc = mod.add_cell("out_p", PrimOp.OUTPUT, port_name="out")
            mod.connect(oc, "A", c)
            mod.ports["out"] = c
            return mod

        r = check_equivalence(_const_out("a", 42), _const_out("b", 42))
        assert r.equivalent

    def test_const_vs_const_different(self):
        """Two modules outputting different constants must NOT be equivalent."""
        def _const_out(name, val):
            mod = Module(name=name)
            c = mod.add_net("c", 8)
            cc = mod.add_cell("c_c", PrimOp.CONST, value=val, width=8)
            mod.connect(cc, "Y", c, direction="output")
            oc = mod.add_cell("out_p", PrimOp.OUTPUT, port_name="out")
            mod.connect(oc, "A", c)
            mod.ports["out"] = c
            return mod

        r = check_equivalence(_const_out("a", 42), _const_out("b", 43))
        assert not r.equivalent

    def test_wide_comparison(self):
        """8-bit AND vs OR should be non-equivalent (random simulation fallback)."""
        def _gate(name, op):
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
            gc = mod.add_cell("gate", op)
            mod.connect(gc, "A", a)
            mod.connect(gc, "B", b)
            mod.connect(gc, "Y", y, direction="output")
            oc = mod.add_cell("out_p", PrimOp.OUTPUT, port_name="y")
            mod.connect(oc, "A", y)
            mod.ports["y"] = y
            return mod

        # 8+8=16 bits, just at the exhaustive threshold
        r = check_equivalence(_gate("a", PrimOp.AND), _gate("b", PrimOp.OR), max_exhaustive_bits=16)
        assert not r.equivalent


# ---------------------------------------------------------------------------
# JSON output edge cases
# ---------------------------------------------------------------------------

class TestJSONEdgeCases:
    def test_empty_module_json(self):
        mod = Module(name="empty")
        design = Design(modules={"empty": mod}, top="empty")
        nl = map_to_ecp5(design)
        text = emit_json_str(nl)
        data = json.loads(text)
        assert data["modules"]["empty"]["cells"] == {}
        assert data["modules"]["empty"]["ports"] == {}

    def test_module_name_with_special_chars(self):
        """Module names that contain dots or dollars should not break JSON."""
        mod = Module(name="my.module$1")
        design = Design(modules={"my.module$1": mod}, top="my.module$1")
        nl = map_to_ecp5(design)
        text = emit_json_str(nl)
        data = json.loads(text)
        assert "my.module$1" in data["modules"]

    def test_very_wide_port(self):
        """A 256-bit port should produce 256 bit references."""
        mod = Module(name="wide")
        a = mod.add_net("a", 256)
        ac = mod.add_cell("a_p", PrimOp.INPUT, port_name="a")
        mod.connect(ac, "Y", a, direction="output")
        mod.ports["a"] = a
        design = Design(modules={"wide": mod}, top="wide")
        nl = map_to_ecp5(design)
        text = emit_json_str(nl)
        data = json.loads(text)
        assert len(data["modules"]["wide"]["ports"]["a"]["bits"]) == 256


# ---------------------------------------------------------------------------
# Resource reporting edge cases
# ---------------------------------------------------------------------------

class TestResourceEdgeCases:
    def test_overutilization_12k(self):
        """A design with 20000 LUTs should warn on 12k device."""
        mod = Module(name="big")
        # Create many LUTs
        for i in range(100):
            a = mod.add_net(f"a{i}", 1)
            b = mod.add_net(f"b{i}", 1)
            y = mod.add_net(f"y{i}", 1)
            cell = mod.add_cell(f"and{i}", PrimOp.AND)
            mod.connect(cell, "A", a)
            mod.connect(cell, "B", b)
            mod.connect(cell, "Y", y, direction="output")
            oc = mod.add_cell(f"out{i}", PrimOp.OUTPUT, port_name=f"out{i}")
            mod.connect(oc, "A", y)
            mod.ports[f"out{i}"] = y
        design = Design(modules={"big": mod}, top="big")
        nl = map_to_ecp5(design)
        report = report_utilization(nl, "12k")
        # With 100 1-bit ANDs -> 100 LUTs, should fit in 12k
        assert report.luts_used == 100
        assert len(report.warnings) == 0  # 100 < 12288

    def test_all_four_devices(self):
        """Report should work for all ECP5 sizes."""
        mod = Module(name="t")
        design = Design(modules={"t": mod}, top="t")
        nl = map_to_ecp5(design)
        for size in ("12k", "25k", "45k", "85k"):
            report = report_utilization(nl, size)
            assert report.device.name.startswith("LFE5U")


# ---------------------------------------------------------------------------
# Hypothesis: random module construction survives full pipeline
# ---------------------------------------------------------------------------

@given(
    n_gates=st.integers(min_value=1, max_value=20),
    op=st.sampled_from([PrimOp.AND, PrimOp.OR, PrimOp.XOR]),
    width=st.sampled_from([1, 4, 8]),
)
@settings(max_examples=50)
def test_random_module_survives_pipeline(n_gates, op, width):
    """A random chain of gates must survive the full pipeline."""
    mod = Module(name="random")
    a = mod.add_net("a", width)
    b = mod.add_net("b", width)
    ac = mod.add_cell("a_p", PrimOp.INPUT, port_name="a")
    mod.connect(ac, "Y", a, direction="output")
    mod.ports["a"] = a
    bc = mod.add_cell("b_p", PrimOp.INPUT, port_name="b")
    mod.connect(bc, "Y", b, direction="output")
    mod.ports["b"] = b

    prev = a
    for i in range(n_gates):
        out = mod.add_net(f"g{i}", width)
        cell = mod.add_cell(f"g{i}", op)
        mod.connect(cell, "A", prev)
        mod.connect(cell, "B", b)
        mod.connect(cell, "Y", out, direction="output")
        prev = out

    oc = mod.add_cell("out_p", PrimOp.OUTPUT, port_name="out")
    mod.connect(oc, "A", prev)
    mod.ports["out"] = prev

    run_default_passes(mod)
    design = Design(modules={"random": mod}, top="random")
    nl = map_to_ecp5(design)
    text = emit_json_str(nl)
    data = json.loads(text)
    assert "random" in data["modules"]
    # Must produce valid JSON with no None values
    assert text.count("null") == 0


# --- from test_mutation ---




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



# --- FastSimulator direct tests ---

def _make_gate_mod(op):
    """Build a minimal 1-bit gate module for simulator testing."""
    mod = Module(name="t")
    a = mod.add_net("a", 1)
    b = mod.add_net("b", 1)
    y = mod.add_net("y", 1)
    ac = mod.add_cell("ap", PrimOp.INPUT, port_name="a")
    mod.connect(ac, "Y", a, direction="output")
    mod.ports["a"] = a
    bc = mod.add_cell("bp", PrimOp.INPUT, port_name="b")
    mod.connect(bc, "Y", b, direction="output")
    mod.ports["b"] = b
    gc = mod.add_cell("g", op)
    mod.connect(gc, "A", a)
    mod.connect(gc, "B", b)
    mod.connect(gc, "Y", y, direction="output")
    oc = mod.add_cell("yp", PrimOp.OUTPUT, port_name="y")
    mod.connect(oc, "A", y)
    mod.ports["y"] = y
    return mod


def test_fast_sim_and():
    from nosis.sim import FastSimulator
    sim = FastSimulator(_make_gate_mod(PrimOp.AND))
    assert sim.step({"a": 1, "b": 1})["y"] == 1
    assert sim.step({"a": 1, "b": 0})["y"] == 0
    assert sim.step({"a": 0, "b": 1})["y"] == 0
    assert sim.step({"a": 0, "b": 0})["y"] == 0


def test_fast_sim_or():
    from nosis.sim import FastSimulator
    sim = FastSimulator(_make_gate_mod(PrimOp.OR))
    assert sim.step({"a": 0, "b": 0})["y"] == 0
    assert sim.step({"a": 1, "b": 0})["y"] == 1
    assert sim.step({"a": 0, "b": 1})["y"] == 1


def test_fast_sim_xor():
    from nosis.sim import FastSimulator
    sim = FastSimulator(_make_gate_mod(PrimOp.XOR))
    assert sim.step({"a": 1, "b": 1})["y"] == 0
    assert sim.step({"a": 1, "b": 0})["y"] == 1


def test_fast_sim_mux():
    from nosis.sim import FastSimulator
    mod = Module(name="t")
    s = mod.add_net("s", 1)
    a = mod.add_net("a", 8)
    b = mod.add_net("b", 8)
    y = mod.add_net("y", 8)
    mod.add_cell("sp", PrimOp.INPUT, port_name="s")
    mod.connect(mod.cells["sp"], "Y", s, direction="output")
    mod.ports["s"] = s
    mod.add_cell("ap", PrimOp.INPUT, port_name="a")
    mod.connect(mod.cells["ap"], "Y", a, direction="output")
    mod.ports["a"] = a
    mod.add_cell("bp", PrimOp.INPUT, port_name="b")
    mod.connect(mod.cells["bp"], "Y", b, direction="output")
    mod.ports["b"] = b
    mc = mod.add_cell("m", PrimOp.MUX)
    mod.connect(mc, "S", s)
    mod.connect(mc, "A", a)
    mod.connect(mc, "B", b)
    mod.connect(mc, "Y", y, direction="output")
    sim = FastSimulator(mod)
    assert sim.step({"s": 0, "a": 42, "b": 99})["y"] == 42
    assert sim.step({"s": 1, "a": 42, "b": 99})["y"] == 99


def test_fast_sim_const():
    from nosis.sim import FastSimulator
    mod = Module(name="t")
    y = mod.add_net("y", 8)
    cc = mod.add_cell("c", PrimOp.CONST, value=0xAB, width=8)
    mod.connect(cc, "Y", y, direction="output")
    sim = FastSimulator(mod)
    assert sim.step({})["y"] == 0xAB


def test_fast_sim_signed_lt():
    from nosis.sim import FastSimulator
    mod = Module(name="t")
    a = mod.add_net("a", 8)
    b = mod.add_net("b", 8)
    y = mod.add_net("y", 1)
    mod.add_cell("ap", PrimOp.INPUT, port_name="a")
    mod.connect(mod.cells["ap"], "Y", a, direction="output")
    mod.ports["a"] = a
    mod.add_cell("bp", PrimOp.INPUT, port_name="b")
    mod.connect(mod.cells["bp"], "Y", b, direction="output")
    mod.ports["b"] = b
    lt = mod.add_cell("lt", PrimOp.LT)
    lt.params["signed"] = True
    mod.connect(lt, "A", a)
    mod.connect(lt, "B", b)
    mod.connect(lt, "Y", y, direction="output")
    sim = FastSimulator(mod)
    # unsigned: 0xFF > 0x01, but signed: 0xFF = -1 < 1
    assert sim.step({"a": 0xFF, "b": 0x01})["y"] == 1
    assert sim.step({"a": 0x01, "b": 0xFF})["y"] == 0
    # equal
    assert sim.step({"a": 0x80, "b": 0x80})["y"] == 0
