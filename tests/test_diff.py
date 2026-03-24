"""Tests for nosis.diff — netlist comparison."""

from nosis.techmap import ECP5Netlist
from nosis.diff import diff_netlists


def test_identical_netlists():
    a = ECP5Netlist(top="test")
    b = ECP5Netlist(top="test")
    for i in range(10):
        a.add_cell(f"c{i}", "TRELLIS_SLICE")
        b.add_cell(f"c{i}", "TRELLIS_SLICE")
    d = diff_netlists(a, b)
    assert d.identical


def test_cells_added():
    a = ECP5Netlist(top="test")
    b = ECP5Netlist(top="test")
    a.add_cell("c0", "TRELLIS_SLICE")
    b.add_cell("c0", "TRELLIS_SLICE")
    b.add_cell("c1", "TRELLIS_SLICE")
    d = diff_netlists(a, b)
    assert not d.identical
    assert "c1" in d.cells_added


def test_cells_removed():
    a = ECP5Netlist(top="test")
    b = ECP5Netlist(top="test")
    a.add_cell("c0", "TRELLIS_SLICE")
    a.add_cell("c1", "TRELLIS_FF")
    b.add_cell("c0", "TRELLIS_SLICE")
    d = diff_netlists(a, b)
    assert "c1" in d.cells_removed


def test_type_changes():
    a = ECP5Netlist(top="test")
    b = ECP5Netlist(top="test")
    for i in range(10):
        a.add_cell(f"c{i}", "TRELLIS_SLICE")
    for i in range(15):
        b.add_cell(f"c{i}", "TRELLIS_SLICE")
    d = diff_netlists(a, b)
    assert "TRELLIS_SLICE" in d.cell_type_changes
    assert d.cell_type_changes["TRELLIS_SLICE"] == (10, 15)


def test_ports_changed():
    a = ECP5Netlist(top="test")
    b = ECP5Netlist(top="test")
    a.ports["clk"] = {"direction": "input", "bits": [2]}
    b.ports["clk"] = {"direction": "input", "bits": [2]}
    b.ports["rst"] = {"direction": "input", "bits": [3]}
    d = diff_netlists(a, b)
    assert "rst" in d.ports_added


def test_summary_lines():
    a = ECP5Netlist(top="test")
    b = ECP5Netlist(top="test")
    a.add_cell("c0", "TRELLIS_SLICE")
    d = diff_netlists(a, b)
    lines = d.summary_lines()
    assert any("removed" in line.lower() for line in lines)


def test_empty_netlists():
    a = ECP5Netlist(top="test")
    b = ECP5Netlist(top="test")
    d = diff_netlists(a, b)
    assert d.identical
