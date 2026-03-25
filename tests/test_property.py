"""Property-based tests — verify IR and evaluation invariants over random inputs.

Uses Hypothesis to generate random cell configurations, input values,
and module structures, then checks that synthesis invariants hold.
"""

from hypothesis import given, settings
from hypothesis import strategies as st

from nosis.ir import Module, PrimOp
from nosis.eval import eval_const_op
from nosis.passes import constant_fold, dead_code_eliminate
from nosis.cse import eliminate_common_subexpressions


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
    from nosis.ir import Design
    from nosis.techmap import map_to_ecp5

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
