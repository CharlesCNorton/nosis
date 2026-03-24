"""Tests for nosis.wirelength — routing delay estimation."""

from nosis.ir import Module, PrimOp
from nosis.wirelength import estimate_routing


def test_empty_module():
    mod = Module(name="empty")
    r = estimate_routing(mod, logic_delay_ns=0)
    assert r.total_nets == 0
    assert r.estimated_total_delay_ns == 0.0


def test_single_gate():
    mod = Module(name="test")
    a = mod.add_net("a", 1)
    b = mod.add_net("b", 1)
    y = mod.add_net("y", 1)
    cell = mod.add_cell("and0", PrimOp.AND)
    mod.connect(cell, "A", a)
    mod.connect(cell, "B", b)
    mod.connect(cell, "Y", y, direction="output")

    r = estimate_routing(mod, logic_delay_ns=0.4)
    assert r.total_nets > 0
    assert r.avg_routing_delay_ns > 0
    assert r.estimated_total_delay_ns > 0.4  # routing adds to logic


def test_high_fanout_adds_delay():
    mod = Module(name="fanout")
    a = mod.add_net("a", 1)
    for i in range(100):
        y = mod.add_net(f"y{i}", 1)
        c = mod.add_cell(f"not{i}", PrimOp.NOT)
        mod.connect(c, "A", a)
        mod.connect(c, "Y", y, direction="output")

    r = estimate_routing(mod, logic_delay_ns=0.4)
    assert r.max_routing_delay_ns >= r.avg_routing_delay_ns
    assert r.max_routing_delay_ns > 0.5  # high fanout should have significant delay


def test_summary_lines():
    mod = Module(name="test")
    a = mod.add_net("a", 1)
    y = mod.add_net("y", 1)
    c = mod.add_cell("not0", PrimOp.NOT)
    mod.connect(c, "A", a)
    mod.connect(c, "Y", y, direction="output")
    r = estimate_routing(mod, logic_delay_ns=1.0)
    lines = r.summary_lines()
    assert any("Routing" in l for l in lines)
    assert any("Fmax" in l for l in lines)
