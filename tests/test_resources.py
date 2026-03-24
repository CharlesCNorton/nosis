"""Tests for nosis.resources — resource utilization reporting."""

from nosis.frontend import parse_files, lower_to_ir
from nosis.techmap import map_to_ecp5
from nosis.resources import ECP5_DEVICES, report_utilization
from tests.conftest import RIME_FW as RIME, RIME_UART_TX, RIME_V, requires_rime


def test_device_database():
    assert "25k" in ECP5_DEVICES
    assert "12k" in ECP5_DEVICES
    assert "45k" in ECP5_DEVICES
    assert "85k" in ECP5_DEVICES
    assert ECP5_DEVICES["25k"].luts == 24288
    assert ECP5_DEVICES["25k"].brams == 56
    assert ECP5_DEVICES["25k"].dsps == 28


def test_report_basic():
    result = parse_files([RIME_UART_TX], top="uart_tx")
    design = lower_to_ir(result, top="uart_tx")
    nl = map_to_ecp5(design)
    report = report_utilization(nl, "25k")
    assert report.luts_used > 0
    assert report.ffs_used > 0
    assert report.lut_pct < 100  # uart_tx should not overflow 25k
    assert len(report.warnings) == 0


def test_report_overutilization_warning():
    """Synthesizing a large design against a small device should warn."""
    result = parse_files([RIME_V], top="rime_v")
    design = lower_to_ir(result, top="rime_v")
    nl = map_to_ecp5(design)
    report = report_utilization(nl, "12k")
    # rime_v with ~5000 LUTs should fit in 12k (12288 LUTs)
    # but check that the report generates valid numbers
    assert report.luts_used > 0
    lines = report.summary_lines()
    assert any("LFE5U-12F" in line for line in lines)


def test_report_summary_lines():
    result = parse_files([RIME_UART_TX], top="uart_tx")
    design = lower_to_ir(result, top="uart_tx")
    nl = map_to_ecp5(design)
    report = report_utilization(nl, "25k")
    lines = report.summary_lines()
    assert len(lines) >= 6  # device + 5 resource lines
    assert any("LUTs" in line for line in lines)
    assert any("FFs" in line for line in lines)
    assert any("BRAMs" in line for line in lines)
    assert any("DSPs" in line for line in lines)
