"""Tests for nosis.congestion — logic density and routing pressure analysis."""

from nosis.ir import Module, PrimOp
from nosis.congestion import analyze_congestion, estimate_routing_metric


def test_empty_module():
    mod = Module(name="empty")
    r = analyze_congestion(mod)
    assert r.total_nets == 0
    assert r.total_cells == 0
    assert r.max_fanout == 0
    assert r.avg_fanout == 0.0
    assert r.high_fanout_nets == 0
    assert r.very_high_fanout_nets == 0
    assert r.density_score == 0.0


def test_single_gate_exact():
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
    assert r.total_nets == 3
    # a and b each have fanout 1
    assert r.max_fanout == 1
    assert r.avg_fanout == 1.0
    assert r.high_fanout_nets == 0


def test_high_fanout_exact():
    mod = Module(name="fanout")
    a = mod.add_net("a", 1)
    for i in range(100):
        y = mod.add_net(f"y{i}", 1)
        cell = mod.add_cell(f"not{i}", PrimOp.NOT)
        mod.connect(cell, "A", a)
        mod.connect(cell, "Y", y, direction="output")
    r = analyze_congestion(mod)
    assert r.max_fanout == 100
    assert r.high_fanout_nets >= 1
    assert r.very_high_fanout_nets >= 1
    assert r.density_score > 0


def test_fanout_histogram_buckets():
    """Verify the histogram classifies fanout counts into correct buckets."""
    mod = Module(name="hist")
    a = mod.add_net("a", 1)
    b = mod.add_net("b", 1)
    c = mod.add_net("c", 1)
    # a feeds 1 consumer, b feeds 3 consumers, c feeds 20 consumers
    y = mod.add_net("y0", 1)
    cell = mod.add_cell("g0", PrimOp.AND)
    mod.connect(cell, "A", a)
    mod.connect(cell, "B", b)
    mod.connect(cell, "Y", y, direction="output")
    for i in range(2):
        yi = mod.add_net(f"y1_{i}", 1)
        ci = mod.add_cell(f"g1_{i}", PrimOp.NOT)
        mod.connect(ci, "A", b)
        mod.connect(ci, "Y", yi, direction="output")
    for i in range(20):
        yi = mod.add_net(f"y2_{i}", 1)
        ci = mod.add_cell(f"g2_{i}", PrimOp.NOT)
        mod.connect(ci, "A", c)
        mod.connect(ci, "Y", yi, direction="output")
    r = analyze_congestion(mod)
    assert r.fanout_histogram["1"] >= 1      # a has fanout 1
    assert r.fanout_histogram["2-4"] >= 1    # b has fanout 3
    assert r.fanout_histogram["17-64"] >= 1  # c has fanout 20


def test_density_score_bounded():
    """Density score must be in [0, 100]."""
    mod = Module(name="test")
    a = mod.add_net("a", 1)
    for i in range(200):
        y = mod.add_net(f"y{i}", 1)
        c = mod.add_cell(f"not{i}", PrimOp.NOT)
        mod.connect(c, "A", a)
        mod.connect(c, "Y", y, direction="output")
    r = analyze_congestion(mod)
    assert 0 <= r.density_score <= 100


def test_routing_metric_empty():
    mod = Module(name="empty")
    assert estimate_routing_metric(mod) == 0.0


def test_routing_metric_grows_with_size():
    """Larger designs should have higher routing metric."""
    small = Module(name="small")
    a = small.add_net("a", 1)
    b = small.add_net("b", 1)
    y = small.add_net("y", 1)
    c = small.add_cell("g", PrimOp.AND)
    small.connect(c, "A", a)
    small.connect(c, "B", b)
    small.connect(c, "Y", y, direction="output")

    big = Module(name="big")
    x = big.add_net("x", 1)
    for i in range(100):
        yi = big.add_net(f"y{i}", 1)
        ci = big.add_cell(f"not{i}", PrimOp.NOT)
        big.connect(ci, "A", x)
        big.connect(ci, "Y", yi, direction="output")

    assert estimate_routing_metric(big) > estimate_routing_metric(small)


def test_summary_lines_present():
    mod = Module(name="test")
    a = mod.add_net("a", 1)
    y = mod.add_net("y", 1)
    c = mod.add_cell("not0", PrimOp.NOT)
    mod.connect(c, "A", a)
    mod.connect(c, "Y", y, direction="output")
    r = analyze_congestion(mod)
    lines = r.summary_lines()
    assert any("Max fanout" in ln for ln in lines)
    assert any("Density score" in ln for ln in lines)
