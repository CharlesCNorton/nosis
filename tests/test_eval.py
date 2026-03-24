"""Tests for nosis.eval — shared cell evaluation semantics.

Every PrimOp that can be constant-folded or simulated must produce
identical results whether called from passes.py or equiv.py. These
tests verify the single source of truth.
"""

from nosis.ir import Cell, Module, PrimOp
from nosis.eval import eval_const_op, eval_cell, UnsupportedOpError


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


def test_unsupported_op_raises():
    """An unrecognized PrimOp must raise UnsupportedOpError, not silently return 0."""
    # PMUX is recognized but not fully implemented — it should still have defined behavior
    # Create a truly fake op by patching
    import enum
    FakeOp = PrimOp.PMUX  # PMUX is in the enum but not handled by eval_const_op
    # Actually, PMUX falls through to the raise. Test it.
    try:
        eval_const_op(PrimOp.PMUX, {"A": 1}, {}, 8)
        assert False, "should have raised UnsupportedOpError"
    except UnsupportedOpError as exc:
        assert exc.op == PrimOp.PMUX


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
    from nosis.passes import constant_fold

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
