"""Tests for nosis.timing — critical path analysis."""

import os

os.environ.setdefault("NOSIS_PYSLANG_PATH", "D:/slang/build/lib")

from nosis.ir import Module, PrimOp
from nosis.timing import analyze_timing
from nosis.frontend import parse_files, lower_to_ir
from tests.conftest import RIME_UART_TX, RIME_V, requires_rime_soc


def test_empty_module():
    mod = Module(name="empty")
    report = analyze_timing(mod)
    assert report.max_delay_ns == 0.0
    assert report.critical_path is None


def test_single_gate():
    mod = Module(name="test")
    a = mod.add_net("a", 1)
    b = mod.add_net("b", 1)
    y = mod.add_net("y", 1)
    ac = mod.add_cell("a_p", PrimOp.INPUT, port_name="a")
    mod.connect(ac, "Y", a, direction="output")
    mod.ports["a"] = a
    bc = mod.add_cell("b_p", PrimOp.INPUT, port_name="b")
    mod.connect(bc, "Y", b, direction="output")
    mod.ports["b"] = b
    gc = mod.add_cell("and0", PrimOp.AND)
    mod.connect(gc, "A", a)
    mod.connect(gc, "B", b)
    mod.connect(gc, "Y", y, direction="output")
    oc = mod.add_cell("out_p", PrimOp.OUTPUT, port_name="y")
    mod.connect(oc, "A", y)
    mod.ports["y"] = y

    report = analyze_timing(mod)
    assert report.max_delay_ns > 0
    assert report.max_frequency_mhz > 0


def test_chain_delay_accumulates():
    """A chain of 5 AND gates should have ~5x the single-gate delay."""
    mod = Module(name="chain")
    a = mod.add_net("a", 1)
    ac = mod.add_cell("a_p", PrimOp.INPUT, port_name="a")
    mod.connect(ac, "Y", a, direction="output")
    mod.ports["a"] = a
    b = mod.add_net("b", 1)
    bc = mod.add_cell("b_p", PrimOp.INPUT, port_name="b")
    mod.connect(bc, "Y", b, direction="output")
    mod.ports["b"] = b

    prev = a
    for i in range(5):
        out = mod.add_net(f"g{i}", 1)
        cell = mod.add_cell(f"and{i}", PrimOp.AND)
        mod.connect(cell, "A", prev)
        mod.connect(cell, "B", b)
        mod.connect(cell, "Y", out, direction="output")
        prev = out

    oc = mod.add_cell("out_p", PrimOp.OUTPUT, port_name="y")
    mod.connect(oc, "A", prev)
    mod.ports["y"] = prev

    report = analyze_timing(mod)
    # 5 AND gates * 0.4 ns each = 2.0 ns
    assert 1.5 <= report.max_delay_ns <= 3.0
    assert report.critical_path is not None
    assert len(report.critical_path.cells) == 5


def test_ff_breaks_path():
    """FF should break the timing path — delay restarts after FF."""
    mod = Module(name="ff_break")
    clk = mod.add_net("clk", 1)
    cc = mod.add_cell("clk_p", PrimOp.INPUT, port_name="clk")
    mod.connect(cc, "Y", clk, direction="output")
    mod.ports["clk"] = clk

    a = mod.add_net("a", 1)
    ac = mod.add_cell("a_p", PrimOp.INPUT, port_name="a")
    mod.connect(ac, "Y", a, direction="output")
    mod.ports["a"] = a

    q = mod.add_net("q", 1)
    ff = mod.add_cell("ff0", PrimOp.FF)
    mod.connect(ff, "CLK", clk)
    mod.connect(ff, "D", a)
    mod.connect(ff, "Q", q, direction="output")

    y = mod.add_net("y", 1)
    gc = mod.add_cell("and0", PrimOp.AND)
    mod.connect(gc, "A", q)
    mod.connect(gc, "B", q)
    mod.connect(gc, "Y", y, direction="output")

    oc = mod.add_cell("out_p", PrimOp.OUTPUT, port_name="y")
    mod.connect(oc, "A", y)
    mod.ports["y"] = y

    report = analyze_timing(mod)
    # Path: FF(0.2ns) -> AND(0.4ns) -> output = 0.6ns
    assert report.max_delay_ns < 1.0


def test_uart_tx_timing():
    result = parse_files([RIME_UART_TX], top="uart_tx")
    design = lower_to_ir(result, top="uart_tx")
    report = analyze_timing(design.top_module())
    assert report.max_delay_ns > 0
    assert report.max_frequency_mhz > 0
    assert report.total_paths_analyzed > 0
    lines = report.summary_lines()
    assert any("Critical path" in line for line in lines)


@requires_rime_soc
def test_rime_v_timing():
    result = parse_files([RIME_V], top="rime_v")
    design = lower_to_ir(result, top="rime_v")
    report = analyze_timing(design.top_module())
    assert report.max_delay_ns > 0
    assert report.critical_path is not None
    assert len(report.critical_path.cells) >= 1
