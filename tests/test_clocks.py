"""Tests for nosis.clocks — clock domain analysis."""

from nosis.ir import Module, PrimOp
from nosis.clocks import analyze_clock_domains, ClockDomain, ClockCrossing


def test_single_domain():
    mod = Module(name="test")
    clk = mod.add_net("clk", 1)
    clk_cell = mod.add_cell("clk_p", PrimOp.INPUT, port_name="clk")
    mod.connect(clk_cell, "Y", clk, direction="output")
    mod.ports["clk"] = clk

    d = mod.add_net("d", 1)
    q = mod.add_net("q", 1)
    ff = mod.add_cell("ff0", PrimOp.FF)
    mod.connect(ff, "CLK", clk)
    mod.connect(ff, "D", d)
    mod.connect(ff, "Q", q, direction="output")

    domains, crossings = analyze_clock_domains(mod)
    assert len(domains) == 1
    assert domains[0].clock_net == "clk"
    assert len(crossings) == 0


def test_two_domains_no_crossing():
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

    domains, crossings = analyze_clock_domains(mod)
    assert len(domains) == 2
    assert len(crossings) == 0


def test_crossing_detected():
    mod = Module(name="test")
    clk_a = mod.add_net("clk_a", 1)
    clk_b = mod.add_net("clk_b", 1)
    d1 = mod.add_net("d1", 1)
    q1 = mod.add_net("q1", 1)
    q2 = mod.add_net("q2", 1)

    # FF1 in domain A
    ff1 = mod.add_cell("ff1", PrimOp.FF)
    mod.connect(ff1, "CLK", clk_a)
    mod.connect(ff1, "D", d1)
    mod.connect(ff1, "Q", q1, direction="output")

    # FF2 in domain B, D input driven by FF1's output
    ff2 = mod.add_cell("ff2", PrimOp.FF)
    mod.connect(ff2, "CLK", clk_b)
    mod.connect(ff2, "D", q1)  # crosses from A to B
    mod.connect(ff2, "Q", q2, direction="output")

    domains, crossings = analyze_clock_domains(mod)
    assert len(domains) == 2
    assert len(crossings) == 1
    assert crossings[0].source_domain == "clk_a"
    assert crossings[0].dest_domain == "clk_b"


def test_crossing_through_logic():
    """CDC through combinational logic should still be detected."""
    mod = Module(name="test")
    clk_a = mod.add_net("clk_a", 1)
    clk_b = mod.add_net("clk_b", 1)
    d1 = mod.add_net("d1", 1)
    q1 = mod.add_net("q1", 1)
    mid = mod.add_net("mid", 1)
    q2 = mod.add_net("q2", 1)
    const1 = mod.add_net("c1", 1)
    c1_cell = mod.add_cell("c1", PrimOp.CONST, value=1, width=1)
    mod.connect(c1_cell, "Y", const1, direction="output")

    ff1 = mod.add_cell("ff1", PrimOp.FF)
    mod.connect(ff1, "CLK", clk_a)
    mod.connect(ff1, "D", d1)
    mod.connect(ff1, "Q", q1, direction="output")

    # AND gate between domains
    and_cell = mod.add_cell("and0", PrimOp.AND)
    mod.connect(and_cell, "A", q1)
    mod.connect(and_cell, "B", const1)
    mod.connect(and_cell, "Y", mid, direction="output")

    ff2 = mod.add_cell("ff2", PrimOp.FF)
    mod.connect(ff2, "CLK", clk_b)
    mod.connect(ff2, "D", mid)
    mod.connect(ff2, "Q", q2, direction="output")

    domains, crossings = analyze_clock_domains(mod)
    assert len(crossings) == 1
    assert crossings[0].source_domain == "clk_a"
    assert crossings[0].dest_domain == "clk_b"
