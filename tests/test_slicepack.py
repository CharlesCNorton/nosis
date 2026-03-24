"""Tests for nosis.slicepack — PFUMX, L6MUX21, dual-LUT4, and constant simplification."""

from nosis.techmap import ECP5Netlist
from nosis.slicepack import pack_pfumx, pack_l6mux21, pack_slices, pack_dual_lut4, simplify_constant_luts


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
        c.parameters["MODE"] = "LOGIC"
        c.ports["A0"] = [2]
        c.ports["B0"] = [3]
        c.ports["C0"] = ["0"]
        c.ports["D0"] = ["0"]
        c.ports["F0"] = [10 + i]
    result = pack_slices(nl)
    # dual_lut4 should pack pairs, pfumx should pack shared-input pairs
    assert result["dual_lut4"] >= 1 or result["pfumx"] >= 1


# ---------------------------------------------------------------------------
# Dual-LUT4 packing — independent signals in same slice
# ---------------------------------------------------------------------------

def test_dual_lut4_packs_two_independent():
    """Two independent LUT4 cells should pack into one dual-LUT slice."""
    nl = ECP5Netlist(top="test")
    c1 = nl.add_cell("lut0", "TRELLIS_SLICE")
    c1.parameters["LUT0_INITVAL"] = "0x8888"
    c1.parameters["MODE"] = "LOGIC"
    c1.ports["A0"] = [2]
    c1.ports["B0"] = [3]
    c1.ports["C0"] = ["0"]
    c1.ports["D0"] = ["0"]
    c1.ports["F0"] = [10]

    c2 = nl.add_cell("lut1", "TRELLIS_SLICE")
    c2.parameters["LUT0_INITVAL"] = "0x6666"
    c2.parameters["MODE"] = "LOGIC"
    c2.ports["A0"] = [4]
    c2.ports["B0"] = [5]
    c2.ports["C0"] = ["0"]
    c2.ports["D0"] = ["0"]
    c2.ports["F0"] = [11]

    before = len(nl.cells)
    packed = pack_dual_lut4(nl)
    after = len(nl.cells)

    assert packed == 1
    assert after == before - 1  # one cell eliminated
    # The surviving cell should have both LUT0 and LUT1
    surviving = list(nl.cells.values())[0]
    assert "LUT1_INITVAL" in surviving.parameters
    assert surviving.parameters["LUT1_INITVAL"] == "0x6666"
    assert "F1" in surviving.ports


def test_dual_lut4_no_pack_single():
    """A single LUT4 cell should not be packed."""
    nl = ECP5Netlist(top="test")
    c1 = nl.add_cell("lut0", "TRELLIS_SLICE")
    c1.parameters["LUT0_INITVAL"] = "0x8888"
    c1.parameters["MODE"] = "LOGIC"
    c1.ports["A0"] = [2]
    c1.ports["B0"] = [3]
    c1.ports["F0"] = [10]

    packed = pack_dual_lut4(nl)
    assert packed == 0


def test_dual_lut4_reduces_slice_count():
    """Packing 10 independent LUT4s should eliminate ~5 cells."""
    nl = ECP5Netlist(top="test")
    for i in range(10):
        c = nl.add_cell(f"lut{i}", "TRELLIS_SLICE")
        c.parameters["LUT0_INITVAL"] = f"0x{(0x8888 + i):04X}"
        c.parameters["MODE"] = "LOGIC"
        c.ports["A0"] = [nl.alloc_bit()]
        c.ports["B0"] = [nl.alloc_bit()]
        c.ports["C0"] = ["0"]
        c.ports["D0"] = ["0"]
        c.ports["F0"] = [nl.alloc_bit()]

    packed = pack_dual_lut4(nl)
    assert packed == 5  # 10 cells -> 5 dual-LUT slices
    assert len(nl.cells) == 5


def test_simplify_constant_lut_all_zero():
    """A LUT4 with all-zero inputs has a constant-0 output — should be eliminated."""
    nl = ECP5Netlist(top="test")
    c = nl.add_cell("lut0", "TRELLIS_SLICE")
    c.parameters["LUT0_INITVAL"] = "0x8888"  # AND gate
    c.ports["A0"] = ["0"]
    c.ports["B0"] = ["0"]
    c.ports["C0"] = ["0"]
    c.ports["D0"] = ["0"]
    c.ports["F0"] = [2]

    simplified = simplify_constant_luts(nl)
    assert simplified >= 1


def test_simplify_constant_lut_partial():
    """A LUT4 with one constant input should have its INIT reduced."""
    nl = ECP5Netlist(top="test")
    c = nl.add_cell("lut0", "TRELLIS_SLICE")
    c.parameters["LUT0_INITVAL"] = "0x8888"  # AND(A,B)
    c.ports["A0"] = [2]   # variable
    c.ports["B0"] = ["1"]  # constant 1
    c.ports["C0"] = ["0"]
    c.ports["D0"] = ["0"]
    c.ports["F0"] = [3]

    simplified = simplify_constant_luts(nl)
    assert simplified >= 1
    # With B=1, AND(A,1) = A — INIT should change from 0x8888
    new_init = int(c.parameters["LUT0_INITVAL"], 16)
    assert new_init != 0x8888


def test_dual_lut4_no_repack_already_packed():
    """A slice that already has LUT1 populated must not be packed again."""
    nl = ECP5Netlist(top="test")
    c1 = nl.add_cell("lut0", "TRELLIS_SLICE")
    c1.parameters["LUT0_INITVAL"] = "0x8888"
    c1.parameters["LUT1_INITVAL"] = "0x6666"  # already dual-packed
    c1.parameters["MODE"] = "LOGIC"
    c1.ports["A0"] = [2]
    c1.ports["B0"] = [3]
    c1.ports["F0"] = [10]
    c1.ports["A1"] = [4]
    c1.ports["B1"] = [5]
    c1.ports["F1"] = [11]

    c2 = nl.add_cell("lut1", "TRELLIS_SLICE")
    c2.parameters["LUT0_INITVAL"] = "0xEEEE"
    c2.parameters["MODE"] = "LOGIC"
    c2.ports["A0"] = [6]
    c2.ports["B0"] = [7]
    c2.ports["F0"] = [12]

    packed = pack_dual_lut4(nl)
    # c1 already has LUT1 — must not absorb c2
    assert packed == 0
    assert len(nl.cells) == 2


def test_dual_lut4_idempotent():
    """Running pack_dual_lut4 twice must produce the same result as once."""
    nl = ECP5Netlist(top="test")
    for i in range(6):
        c = nl.add_cell(f"lut{i}", "TRELLIS_SLICE")
        c.parameters["LUT0_INITVAL"] = f"0x{(0x8888 + i):04X}"
        c.parameters["MODE"] = "LOGIC"
        c.ports["A0"] = [nl.alloc_bit()]
        c.ports["B0"] = [nl.alloc_bit()]
        c.ports["C0"] = ["0"]
        c.ports["D0"] = ["0"]
        c.ports["F0"] = [nl.alloc_bit()]

    pack_dual_lut4(nl)
    count_after_first = len(nl.cells)
    pack_dual_lut4(nl)
    count_after_second = len(nl.cells)
    assert count_after_second == count_after_first


def test_simplify_constant_lut_no_constants():
    """A LUT4 with no constant inputs should not be modified."""
    nl = ECP5Netlist(top="test")
    c = nl.add_cell("lut0", "TRELLIS_SLICE")
    c.parameters["LUT0_INITVAL"] = "0x8888"
    c.ports["A0"] = [2]
    c.ports["B0"] = [3]
    c.ports["C0"] = [4]
    c.ports["D0"] = [5]
    c.ports["F0"] = [6]

    simplified = simplify_constant_luts(nl)
    assert simplified == 0
    assert c.parameters["LUT0_INITVAL"] == "0x8888"
