"""Tests for nosis.congestion — logic density analysis."""

from nosis.ir import Module, PrimOp
from nosis.congestion import analyze_congestion


def test_empty_module():
    mod = Module(name="empty")
    r = analyze_congestion(mod)
    assert r.total_nets == 0
    assert r.density_score == 0.0


def test_single_gate():
    mod = Module(name="test")
    a = mod.add_net("a", 1)
    b = mod.add_net("b", 1)
    y = mod.add_net("y", 1)
    cell = mod.add_cell("and0", PrimOp.AND)
    mod.connect(cell, "A", a)
    mod.connect(cell, "B", b)
    mod.connect(cell, "Y", y, direction="output")
    r = analyze_congestion(mod)
    assert r.total_cells == 1
    assert r.max_fanout >= 1


def test_high_fanout():
    mod = Module(name="fanout")
    a = mod.add_net("a", 1)
    b = mod.add_net("b", 1)
    for i in range(100):
        y = mod.add_net(f"y{i}", 1)
        cell = mod.add_cell(f"and{i}", PrimOp.AND)
        mod.connect(cell, "A", a)
        mod.connect(cell, "B", b)
        mod.connect(cell, "Y", y, direction="output")
    r = analyze_congestion(mod)
    assert r.max_fanout >= 100
    assert r.high_fanout_nets >= 1
    assert r.density_score > 0
