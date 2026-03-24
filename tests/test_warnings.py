"""Tests for nosis.warnings — design issue detection."""

from nosis.ir import Module, PrimOp
from nosis.warnings import check_warnings


def test_empty_module_no_warnings():
    mod = Module(name="empty")
    w = check_warnings(mod)
    assert len(w) == 0


def test_multi_clock_warning():
    mod = Module(name="test")
    clk_a = mod.add_net("clk_a", 1)
    clk_b = mod.add_net("clk_b", 1)
    d1 = mod.add_net("d1", 1)
    d2 = mod.add_net("d2", 1)
    q1 = mod.add_net("q1", 1)
    q2 = mod.add_net("q2", 1)

    ff1 = mod.add_cell("ff1", PrimOp.FF)
    mod.connect(ff1, "CLK", clk_a)
    mod.connect(ff1, "D", d1)
    mod.connect(ff1, "Q", q1, direction="output")

    ff2 = mod.add_cell("ff2", PrimOp.FF)
    mod.connect(ff2, "CLK", clk_b)
    mod.connect(ff2, "D", d2)
    mod.connect(ff2, "Q", q2, direction="output")

    w = check_warnings(mod)
    multi = [x for x in w if x.category == "multi_clock"]
    assert len(multi) == 1
    assert "2 clock domains" in multi[0].message


def test_high_fanout_warning():
    mod = Module(name="test")
    a = mod.add_net("a", 1)
    for i in range(100):
        y = mod.add_net(f"y{i}", 1)
        c = mod.add_cell(f"not{i}", PrimOp.NOT)
        mod.connect(c, "A", a)
        mod.connect(c, "Y", y, direction="output")

    w = check_warnings(mod, fanout_threshold=50)
    high = [x for x in w if x.category == "high_fanout"]
    assert len(high) >= 1


def test_no_high_fanout_below_threshold():
    mod = Module(name="test")
    a = mod.add_net("a", 1)
    y = mod.add_net("y", 1)
    c = mod.add_cell("not0", PrimOp.NOT)
    mod.connect(c, "A", a)
    mod.connect(c, "Y", y, direction="output")

    w = check_warnings(mod, fanout_threshold=64)
    high = [x for x in w if x.category == "high_fanout"]
    assert len(high) == 0


def test_undriven_net_warning():
    mod = Module(name="test")
    mod.add_net("floating", 8)  # no driver, not a port
    w = check_warnings(mod)
    undriven = [x for x in w if x.category == "undriven_net"]
    assert len(undriven) >= 1
    assert "floating" in undriven[0].message
