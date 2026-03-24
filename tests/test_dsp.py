"""Tests for nosis.dsp — DSP inference."""

from nosis.ir import Module, PrimOp
from nosis.dsp import infer_dsps


def _mul_module(a_width: int, b_width: int) -> Module:
    mod = Module(name="test")
    a = mod.add_net("a", a_width)
    b = mod.add_net("b", b_width)
    y = mod.add_net("y", a_width + b_width)
    cell = mod.add_cell("mul0", PrimOp.MUL)
    mod.connect(cell, "A", a)
    mod.connect(cell, "B", b)
    mod.connect(cell, "Y", y, direction="output")
    return mod


def test_infer_18x18():
    mod = _mul_module(16, 16)
    tagged = infer_dsps(mod)
    assert tagged == 1
    assert mod.cells["mul0"].params["dsp_config"] == "MULT18X18D"


def test_infer_8x8():
    mod = _mul_module(8, 8)
    tagged = infer_dsps(mod)
    assert tagged == 1
    assert mod.cells["mul0"].params["dsp_config"] == "MULT18X18D"


def test_infer_32x32_decomposed():
    mod = _mul_module(32, 32)
    tagged = infer_dsps(mod)
    assert tagged == 1
    assert mod.cells["mul0"].params["dsp_config"] == "MULT18X18D_DECOMPOSED"
    assert mod.cells["mul0"].params["dsp_count"] == 4


def test_no_mul_no_tag():
    mod = Module(name="test")
    a = mod.add_net("a", 8)
    b = mod.add_net("b", 8)
    y = mod.add_net("y", 8)
    cell = mod.add_cell("add0", PrimOp.ADD)
    mod.connect(cell, "A", a)
    mod.connect(cell, "B", b)
    mod.connect(cell, "Y", y, direction="output")
    tagged = infer_dsps(mod)
    assert tagged == 0


def test_exact_18x18_boundary():
    """Exactly 18x18 must fit in a single MULT18X18D."""
    mod = _mul_module(18, 18)
    tagged = infer_dsps(mod)
    assert tagged == 1
    assert mod.cells["mul0"].params["dsp_config"] == "MULT18X18D"


def test_19x1_exceeds_single():
    """19-bit input exceeds MULT18X18D — should decompose."""
    mod = _mul_module(19, 1)
    tagged = infer_dsps(mod)
    assert tagged == 1
    assert mod.cells["mul0"].params["dsp_config"] == "MULT18X18D_DECOMPOSED"


def test_1x1_multiply():
    """1-bit multiply should still map to MULT18X18D (no reason not to)."""
    mod = _mul_module(1, 1)
    tagged = infer_dsps(mod)
    assert tagged == 1
    assert mod.cells["mul0"].params["dsp_config"] == "MULT18X18D"


def test_asymmetric_widths():
    """Asymmetric widths (4x16) should fit."""
    mod = _mul_module(4, 16)
    tagged = infer_dsps(mod)
    assert tagged == 1
    assert mod.cells["mul0"].params["dsp_config"] == "MULT18X18D"
    assert mod.cells["mul0"].params["dsp_a_width"] == 4
    assert mod.cells["mul0"].params["dsp_b_width"] == 16


def test_exactly_36x36_decomposed():
    mod = _mul_module(36, 36)
    tagged = infer_dsps(mod)
    assert tagged == 1
    assert mod.cells["mul0"].params["dsp_config"] == "MULT18X18D_DECOMPOSED"


def test_multiple_multipliers():
    """Multiple MUL cells should all be tagged."""
    mod = Module(name="test")
    for i in range(4):
        a = mod.add_net(f"a{i}", 8)
        b = mod.add_net(f"b{i}", 8)
        y = mod.add_net(f"y{i}", 16)
        cell = mod.add_cell(f"mul{i}", PrimOp.MUL)
        mod.connect(cell, "A", a)
        mod.connect(cell, "B", b)
        mod.connect(cell, "Y", y, direction="output")
    tagged = infer_dsps(mod)
    assert tagged == 4
