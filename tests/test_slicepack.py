"""Tests for nosis.slicepack — PFUMX and L6MUX21 packing."""

from nosis.techmap import ECP5Netlist
from nosis.slicepack import pack_pfumx, pack_l6mux21, pack_slices


def test_pfumx_basic():
    """Two 2-input LUT4 cells sharing A0/B0 should produce a PFUMX."""
    nl = ECP5Netlist(top="test")
    c1 = nl.add_cell("lut0", "TRELLIS_SLICE")
    c1.parameters["LUT0_INITVAL"] = "0x8888"
    c1.ports["A0"] = [2]
    c1.ports["B0"] = [3]
    c1.ports["C0"] = ["0"]
    c1.ports["D0"] = ["0"]
    c1.ports["F0"] = [10]

    c2 = nl.add_cell("lut1", "TRELLIS_SLICE")
    c2.parameters["LUT0_INITVAL"] = "0xEEEE"
    c2.ports["A0"] = [2]  # same as lut0
    c2.ports["B0"] = [3]  # same as lut0
    c2.ports["C0"] = ["0"]
    c2.ports["D0"] = ["0"]
    c2.ports["F0"] = [11]

    packed = pack_pfumx(nl)
    assert packed == 1
    pfumx_cells = [c for c in nl.cells.values() if c.cell_type == "PFUMX"]
    assert len(pfumx_cells) == 1


def test_no_pfumx_different_inputs():
    nl = ECP5Netlist(top="test")
    c1 = nl.add_cell("lut0", "TRELLIS_SLICE")
    c1.ports["A0"] = [2]
    c1.ports["B0"] = [3]
    c1.ports["C0"] = ["0"]
    c1.ports["D0"] = ["0"]
    c1.ports["F0"] = [10]

    c2 = nl.add_cell("lut1", "TRELLIS_SLICE")
    c2.ports["A0"] = [4]  # different
    c2.ports["B0"] = [5]  # different
    c2.ports["C0"] = ["0"]
    c2.ports["D0"] = ["0"]
    c2.ports["F0"] = [11]

    packed = pack_pfumx(nl)
    assert packed == 0


def test_pack_slices_combined():
    nl = ECP5Netlist(top="test")
    for i in range(4):
        c = nl.add_cell(f"lut{i}", "TRELLIS_SLICE")
        c.parameters["LUT0_INITVAL"] = f"0x{'8' * 4}"
        c.ports["A0"] = [2]
        c.ports["B0"] = [3]
        c.ports["C0"] = ["0"]
        c.ports["D0"] = ["0"]
        c.ports["F0"] = [10 + i]
    result = pack_slices(nl)
    assert result["pfumx"] >= 1
