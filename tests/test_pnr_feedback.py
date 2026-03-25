"""Tests for nosis.pnr_feedback — nextpnr log parsing."""

from nosis.pnr_feedback import parse_nextpnr_log, extract_critical_nets, PnRResult


SAMPLE_LOG = """
Info: Logic utilisation before packing:
Info:     Total LUT4s:        42/24288     0%
Info:      Total DFFs:        16/24288     0%
Info: Max frequency for clock '$glbnet$clk$TRELLIS_IO_IN': 379.22 MHz (PASS at 12.00 MHz)
Info: Program finished normally.
"""

SAMPLE_ERROR_LOG = """
ERROR: cell type 'TRELLIS_SLICE' is unsupported (instantiated as '$lut_0')
0 warnings, 1 error
"""


def test_parse_success():
    result = parse_nextpnr_log(SAMPLE_LOG)
    assert result.success
    assert result.max_freq_mhz > 300
    assert result.total_luts == 42
    assert result.total_ffs == 16
    assert len(result.errors) == 0


def test_parse_error():
    result = parse_nextpnr_log(SAMPLE_ERROR_LOG)
    assert not result.success
    assert len(result.errors) >= 1
    assert "unsupported" in result.errors[0]


def test_parse_empty():
    result = parse_nextpnr_log("")
    assert result.success
    assert result.max_freq_mhz == 0.0


def test_extract_critical_nets():
    result = PnRResult(success=True, critical_nets={"clk", "state"})
    nets = extract_critical_nets(result)
    assert "clk" in nets
    assert "state" in nets


def test_clock_name_extracted():
    result = parse_nextpnr_log(SAMPLE_LOG)
    assert "clk" in result.clock_name.lower() or "TRELLIS" in result.clock_name
