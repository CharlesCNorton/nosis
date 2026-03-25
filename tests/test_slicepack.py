"""Tests for nosis.slicepack — LUT4 post-mapping optimization."""

from nosis.techmap import ECP5Netlist
from nosis.slicepack import pack_slices, simplify_constant_luts, deduplicate_luts, absorb_buffers, merge_lut_chains


def _make_lut4(nl, name, init_bin, a, b, c="0", d="0"):
    """Helper to create a LUT4 cell with given ports."""
    cell = nl.add_cell(name, "LUT4")
    cell.parameters["INIT"] = init_bin
    cell.ports["A"] = [a]
    cell.ports["B"] = [b]
    cell.ports["C"] = [c]
    cell.ports["D"] = [d]
    cell.ports["Z"] = [nl.alloc_bit()]
    return cell


def test_simplify_constant_all_zero():
    nl = ECP5Netlist(top="test")
    c = nl.add_cell("lut0", "LUT4")
    c.parameters["INIT"] = "0000000000000000"
    c.ports["A"] = ["0"]
    c.ports["B"] = ["0"]
    c.ports["C"] = ["0"]
    c.ports["D"] = ["0"]
    c.ports["Z"] = [10]
    simplify_constant_luts(nl)
    # All-zero INIT with all-constant inputs -> cell eliminated
    assert "lut0" not in nl.cells


def test_simplify_constant_reduces_init():
    nl = ECP5Netlist(top="test")
    c = nl.add_cell("lut0", "LUT4")
    c.parameters["INIT"] = format(0x8888, "016b")
    c.ports["A"] = [2]
    c.ports["B"] = ["0"]
    c.ports["C"] = ["0"]
    c.ports["D"] = ["0"]
    c.ports["Z"] = [10]
    s = simplify_constant_luts(nl)
    assert s >= 1


def test_dedup_identical_luts():
    nl = ECP5Netlist(top="test")
    _make_lut4(nl, "lut0", format(0x8888, "016b"), 2, 3)
    _make_lut4(nl, "lut1", format(0x8888, "016b"), 2, 3)
    before = len(nl.cells)
    dd = deduplicate_luts(nl)
    assert dd == 1
    assert len(nl.cells) == before - 1


def test_dedup_different_init_no_merge():
    nl = ECP5Netlist(top="test")
    _make_lut4(nl, "lut0", format(0x8888, "016b"), 2, 3)
    _make_lut4(nl, "lut1", format(0x6666, "016b"), 2, 3)
    dd = deduplicate_luts(nl)
    assert dd == 0


def test_absorb_buffer():
    nl = ECP5Netlist(top="test")
    buf = nl.add_cell("buf0", "LUT4")
    buf.parameters["INIT"] = format(0xAAAA, "016b")
    buf.ports["A"] = [2]
    buf.ports["B"] = ["0"]
    buf.ports["C"] = ["0"]
    buf.ports["D"] = ["0"]
    buf.ports["Z"] = [10]
    cons = nl.add_cell("cons0", "LUT4")
    cons.parameters["INIT"] = format(0x8888, "016b")
    cons.ports["A"] = [10]
    cons.ports["B"] = [3]
    cons.ports["C"] = ["0"]
    cons.ports["D"] = ["0"]
    cons.ports["Z"] = [11]
    assert (0xAAAA & 0x3) == 0x2
    ab = absorb_buffers(nl)
    assert ab == 1
    assert "buf0" not in nl.cells
    assert cons.ports["A"] == [2]


def test_chain_merge_two_luts():
    nl = ECP5Netlist(top="test")
    a = nl.add_cell("lutA", "LUT4")
    a.parameters["INIT"] = format(0x8888, "016b")
    a.ports["A"] = [2]
    a.ports["B"] = [3]
    a.ports["C"] = ["0"]
    a.ports["D"] = ["0"]
    a.ports["Z"] = [10]
    b = nl.add_cell("lutB", "LUT4")
    b.parameters["INIT"] = format(0xEEEE, "016b")
    b.ports["A"] = [10]
    b.ports["B"] = [4]
    b.ports["C"] = ["0"]
    b.ports["D"] = ["0"]
    b.ports["Z"] = [11]
    nl.ports["out"] = {"direction": "output", "bits": [11]}
    mc = merge_lut_chains(nl)
    assert mc >= 1


def test_pack_slices_combined():
    nl = ECP5Netlist(top="test")
    for i in range(4):
        _make_lut4(nl, f"lut{i}", format(0x8888, "016b"), 2, 3)
    result = pack_slices(nl)
    assert isinstance(result, dict)
    assert result["lut_dedup"] >= 1


def test_pack_slices_never_increases_cells():
    nl = ECP5Netlist(top="test")
    for i in range(10):
        _make_lut4(nl, f"lut{i}", format(0x8888, "016b"), 2 + i, 3 + i)
    before = len(nl.cells)
    pack_slices(nl)
    assert len(nl.cells) <= before
