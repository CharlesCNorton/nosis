"""Tests for nosis.power — power estimation."""

from nosis.techmap import ECP5Netlist
from nosis.power import estimate_power


def test_empty_design():
    nl = ECP5Netlist(top="test")
    r = estimate_power(nl)
    assert r.total_power_mw == 0.0


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
    # Dynamic power should double with frequency
    assert abs(r50.dynamic_power_mw - r25.dynamic_power_mw * 2) < 0.01
    # Static power should be the same
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
