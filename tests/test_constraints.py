"""Tests for nosis.constraints — LPF pin constraint parsing."""

import tempfile
from pathlib import Path

from nosis.constraints import parse_lpf


def _write_lpf(content: str) -> str:
    f = tempfile.NamedTemporaryFile(suffix=".lpf", mode="w", delete=False, encoding="utf-8")
    f.write(content)
    f.close()
    return f.name


def test_parse_locate():
    path = _write_lpf('LOCATE COMP "clk" SITE "A4";\n')
    try:
        c = parse_lpf(path)
        assert len(c.pins) == 1
        assert c.pins[0].comp == "clk"
        assert c.pins[0].pin == "A4"
    finally:
        Path(path).unlink()


def test_parse_iobuf():
    path = _write_lpf('IOBUF PORT "led[0]" IO_TYPE=LVCMOS33 DRIVE=8;\n')
    try:
        c = parse_lpf(path)
        assert len(c.io_standards) == 1
        assert c.io_standards[0].port == "led[0]"
        assert c.io_standards[0].standard == "LVCMOS33"
        assert c.io_standards[0].drive == "8"
    finally:
        Path(path).unlink()


def test_parse_frequency():
    path = _write_lpf('FREQUENCY PORT "clk" 25.0 MHz;\n')
    try:
        c = parse_lpf(path)
        assert len(c.frequencies) == 1
        assert c.frequencies[0].net == "clk"
        assert c.frequencies[0].frequency_mhz == 25.0
    finally:
        Path(path).unlink()


def test_parse_sysconfig():
    path = _write_lpf('SYSCONFIG MASTER_SPI_PORT=ENABLE COMPRESS_CONFIG=ON;\n')
    try:
        c = parse_lpf(path)
        assert c.sysconfig["MASTER_SPI_PORT"] == "ENABLE"
        assert c.sysconfig["COMPRESS_CONFIG"] == "ON"
    finally:
        Path(path).unlink()


def test_parse_comments():
    path = _write_lpf('# comment\n// also comment\nLOCATE COMP "a" SITE "B2";\n')
    try:
        c = parse_lpf(path)
        assert len(c.pins) == 1
    finally:
        Path(path).unlink()


def test_validate_ports():
    path = _write_lpf('LOCATE COMP "clk" SITE "A4";\nLOCATE COMP "missing" SITE "B2";\n')
    try:
        c = parse_lpf(path)
        warnings = c.validate_against_ports({"clk", "data"})
        assert any("missing" in w for w in warnings)
        assert not any("clk" in w for w in warnings)
    finally:
        Path(path).unlink()


def test_real_lpf():
    """Parse the actual RIME board LPF if available."""
    lpf = Path("D:/rime/firmware/core/v1.3/icepi-zero-v1_3.lpf")
    if not lpf.exists():
        return
    c = parse_lpf(str(lpf))
    assert len(c.pins) > 0
    assert c.raw_lines > 0
