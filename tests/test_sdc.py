"""Tests for nosis.sdc — SDC constraint parsing and timing arcs."""

import tempfile
from pathlib import Path

from nosis.sdc import parse_sdc, parse_specify_block, apply_sdc_to_timing


def _write_sdc(content: str) -> str:
    f = tempfile.NamedTemporaryFile(suffix=".sdc", mode="w", delete=False, encoding="utf-8")
    f.write(content)
    f.close()
    return f.name


def test_create_clock():
    path = _write_sdc('create_clock -name sys_clk -period 40.0 [get_ports {clk}]\n')
    try:
        c = parse_sdc(path)
        assert len(c.clocks) == 1
        assert c.clocks[0].name == "sys_clk"
        assert c.clocks[0].period_ns == 40.0
        assert c.clocks[0].port == "clk"
        assert abs(c.clocks[0].frequency_mhz - 25.0) < 0.1
    finally:
        Path(path).unlink()


def test_set_input_delay():
    path = _write_sdc('set_input_delay -clock clk 2.0 [get_ports {data}]\n')
    try:
        c = parse_sdc(path)
        assert len(c.delays) == 1
        assert c.delays[0].port == "data"
        assert c.delays[0].is_input
    finally:
        Path(path).unlink()


def test_set_false_path():
    path = _write_sdc('set_false_path -from [get_ports {rst}] -to [get_ports {led}]\n')
    try:
        c = parse_sdc(path)
        assert len(c.false_paths) == 1
        assert c.false_paths[0].from_port == "rst"
        assert c.false_paths[0].to_port == "led"
    finally:
        Path(path).unlink()


def test_comments_and_empty():
    path = _write_sdc('# comment\n\ncreate_clock -period 10.0 [get_ports {clk}]\n')
    try:
        c = parse_sdc(path)
        assert len(c.clocks) == 1
    finally:
        Path(path).unlink()


def test_summary_lines():
    path = _write_sdc('create_clock -name clk -period 20.0 [get_ports {clk}]\n')
    try:
        c = parse_sdc(path)
        lines = c.summary_lines()
        assert any("Clocks" in l for l in lines)
        assert any("50.0 MHz" in l for l in lines)
    finally:
        Path(path).unlink()
