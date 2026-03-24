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
    assert tagged == 0  # single bit, LUT is fine


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
