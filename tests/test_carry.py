"""Tests for nosis.carry — carry chain inference."""

from nosis.ir import Module, PrimOp
from nosis.carry import infer_carry_chains


def _arith_module(op: PrimOp, width: int) -> Module:
    mod = Module(name="test")
    a = mod.add_net("a", width)
    b = mod.add_net("b", width)
    y = mod.add_net("y", width)
    cell = mod.add_cell("arith0", op)
    mod.connect(cell, "A", a)
    mod.connect(cell, "B", b)
    mod.connect(cell, "Y", y, direction="output")
    return mod


def test_infer_add_16bit():
    mod = _arith_module(PrimOp.ADD, 16)
    tagged = infer_carry_chains(mod)
    assert tagged == 1
    cell = mod.cells["arith0"]
    assert cell.params["carry_config"] == "CCU2C"
    assert cell.params["carry_width"] == 16
    assert cell.params["carry_ccu2c_count"] == 8
    assert cell.params["carry_is_sub"] is False


def test_infer_sub_8bit():
    mod = _arith_module(PrimOp.SUB, 8)
    tagged = infer_carry_chains(mod)
    assert tagged == 1
    assert mod.cells["arith0"].params["carry_is_sub"] is True
    assert mod.cells["arith0"].params["carry_ccu2c_count"] == 4


def test_skip_1bit():
    mod = _arith_module(PrimOp.ADD, 1)
    tagged = infer_carry_chains(mod)
    assert tagged == 0


def test_infer_32bit():
    mod = _arith_module(PrimOp.ADD, 32)
    tagged = infer_carry_chains(mod)
    assert tagged == 1
    assert mod.cells["arith0"].params["carry_ccu2c_count"] == 16


def test_no_carry_for_and():
    mod = Module(name="test")
    a = mod.add_net("a", 16)
    b = mod.add_net("b", 16)
    y = mod.add_net("y", 16)
    cell = mod.add_cell("and0", PrimOp.AND)
    mod.connect(cell, "A", a)
    mod.connect(cell, "B", b)
    mod.connect(cell, "Y", y, direction="output")
    tagged = infer_carry_chains(mod)
    assert tagged == 0


def test_odd_width_3bit():
    """3-bit ADD needs ceil(3/2) = 2 CCU2C cells."""
    mod = _arith_module(PrimOp.ADD, 3)
    tagged = infer_carry_chains(mod)
    assert tagged == 1
    assert mod.cells["arith0"].params["carry_ccu2c_count"] == 2


def test_odd_width_7bit():
    """7-bit SUB needs ceil(7/2) = 4 CCU2C cells."""
    mod = _arith_module(PrimOp.SUB, 7)
    tagged = infer_carry_chains(mod)
    assert tagged == 1
    assert mod.cells["arith0"].params["carry_ccu2c_count"] == 4
    assert mod.cells["arith0"].params["carry_is_sub"] is True


def test_2bit_minimum():
    """2-bit ADD needs exactly 1 CCU2C cell."""
    mod = _arith_module(PrimOp.ADD, 2)
    tagged = infer_carry_chains(mod)
    assert tagged == 1
    assert mod.cells["arith0"].params["carry_ccu2c_count"] == 1


def test_ccu2c_count_formula():
    """Verify ceil(N/2) formula for multiple widths."""
    for width in range(2, 65):
        mod = _arith_module(PrimOp.ADD, width)
        infer_carry_chains(mod)
        expected = (width + 1) // 2
        actual = mod.cells["arith0"].params["carry_ccu2c_count"]
        assert actual == expected, f"width {width}: expected {expected} CCU2C, got {actual}"


def test_multiple_adders():
    """Multiple ADD/SUB cells in the same module should all be tagged."""
    mod = Module(name="test")
    for i, op in enumerate([PrimOp.ADD, PrimOp.SUB, PrimOp.ADD]):
        a = mod.add_net(f"a{i}", 8)
        b = mod.add_net(f"b{i}", 8)
        y = mod.add_net(f"y{i}", 8)
        cell = mod.add_cell(f"arith{i}", op)
        mod.connect(cell, "A", a)
        mod.connect(cell, "B", b)
        mod.connect(cell, "Y", y, direction="output")
    tagged = infer_carry_chains(mod)
    assert tagged == 3
