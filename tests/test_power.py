"""Tests for nosis.power — power estimation and toggle rate analysis."""

from nosis.ir import Module, PrimOp
from nosis.techmap import ECP5Netlist
from nosis.power import estimate_power, estimate_clock_tree_power, estimate_toggle_rates


def test_empty_design():
    nl = ECP5Netlist(top="test")
    r = estimate_power(nl)
    assert r.total_power_mw == 0.0
    assert r.static_power_mw == 0.0
    assert r.dynamic_power_mw == 0.0


def test_luts_only():
    nl = ECP5Netlist(top="test")
    for i in range(100):
        nl.add_cell(f"lut{i}", "TRELLIS_SLICE")
    r = estimate_power(nl, frequency_mhz=25.0)
    assert r.static_power_mw > 0
    assert r.dynamic_power_mw > 0
    assert r.total_power_mw == r.static_power_mw + r.dynamic_power_mw


def test_frequency_scaling():
    nl = ECP5Netlist(top="test")
    for i in range(100):
        nl.add_cell(f"lut{i}", "TRELLIS_SLICE")
    r25 = estimate_power(nl, frequency_mhz=25.0)
    r50 = estimate_power(nl, frequency_mhz=50.0)
    assert abs(r50.dynamic_power_mw - r25.dynamic_power_mw * 2) < 0.01
    assert r50.static_power_mw == r25.static_power_mw


def test_breakdown_present():
    nl = ECP5Netlist(top="test")
    for i in range(10):
        nl.add_cell(f"lut{i}", "TRELLIS_SLICE")
    for i in range(5):
        nl.add_cell(f"ff{i}", "TRELLIS_FF")
    r = estimate_power(nl)
    assert "TRELLIS_SLICE" in r.breakdown
    assert "TRELLIS_FF" in r.breakdown


def test_summary_lines():
    nl = ECP5Netlist(top="test")
    nl.add_cell("lut0", "TRELLIS_SLICE")
    r = estimate_power(nl)
    lines = r.summary_lines()
    assert any("Total power" in l for l in lines)
    assert any("MHz" in l for l in lines)


def test_bram_power():
    """DP16KD cells must contribute to power."""
    nl = ECP5Netlist(top="test")
    nl.add_cell("bram0", "DP16KD")
    r = estimate_power(nl, frequency_mhz=25.0)
    assert r.static_power_mw > 0
    assert r.dynamic_power_mw > 0
    assert "DP16KD" in r.breakdown


def test_dsp_power():
    """MULT18X18D cells must contribute to power."""
    nl = ECP5Netlist(top="test")
    nl.add_cell("mult0", "MULT18X18D")
    r = estimate_power(nl, frequency_mhz=25.0)
    assert r.static_power_mw > 0
    assert "MULT18X18D" in r.breakdown


def test_clock_tree_power_zero_ffs():
    nl = ECP5Netlist(top="test")
    nl.add_cell("lut0", "TRELLIS_SLICE")
    p = estimate_clock_tree_power(nl, frequency_mhz=25.0)
    assert p == 0.0


def test_clock_tree_power_scales_with_ffs():
    nl = ECP5Netlist(top="test")
    for i in range(100):
        nl.add_cell(f"ff{i}", "TRELLIS_FF")
    p25 = estimate_clock_tree_power(nl, frequency_mhz=25.0)
    p50 = estimate_clock_tree_power(nl, frequency_mhz=50.0)
    assert p25 > 0
    assert abs(p50 - p25 * 2) < 0.001  # linear in frequency


def test_toggle_rate_estimation():
    """Toggle rates must be in [0, 1] for a simple combinational module."""
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
    oc = mod.add_cell("y_p", PrimOp.OUTPUT, port_name="y")
    mod.connect(oc, "A", y)
    mod.ports["y"] = y

    rates = estimate_toggle_rates(mod, num_vectors=200)
    assert len(rates) > 0
    for name, rate in rates.items():
        assert 0.0 <= rate <= 1.0, f"toggle rate {name}={rate} out of bounds"


def test_toggle_rate_empty_module():
    mod = Module(name="empty")
    rates = estimate_toggle_rates(mod)
    assert rates == {}
