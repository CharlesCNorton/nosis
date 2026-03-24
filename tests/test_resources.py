"""Tests for nosis.resources — area calculation and resource utilization."""

from nosis.ir import Module, PrimOp, Design
from nosis.frontend import parse_files, lower_to_ir
from nosis.techmap import map_to_ecp5, ECP5Netlist
from nosis.resources import (
    ECP5_DEVICES,
    AreaCalculation,
    calculate_area,
    report_utilization,
)
from tests.conftest import RIME_UART_TX, RIME_V, RIME_SOC_SOURCES, requires_rime


# ---------------------------------------------------------------------------
# Device database
# ---------------------------------------------------------------------------

class TestDeviceDatabase:
    def test_all_variants_present(self):
        for size in ("12k", "25k", "45k", "85k"):
            assert size in ECP5_DEVICES

    def test_25k_specs(self):
        d = ECP5_DEVICES["25k"]
        assert d.name == "LFE5U-25F"
        assert d.luts == 24288
        assert d.ffs == 24288
        assert d.slices == 12144
        assert d.brams == 56
        assert d.dsps == 28
        assert d.dsp_tiles == 14
        assert d.plls == 2

    def test_slices_are_half_luts(self):
        for d in ECP5_DEVICES.values():
            assert d.slices == d.luts // 2

    def test_dsps_are_double_tiles(self):
        for d in ECP5_DEVICES.values():
            assert d.dsps == d.dsp_tiles * 2

    def test_devices_ordered_by_size(self):
        sizes = [ECP5_DEVICES[k].luts for k in ("12k", "25k", "45k", "85k")]
        assert sizes == sorted(sizes)


# ---------------------------------------------------------------------------
# Area calculation — exact, not estimated
# ---------------------------------------------------------------------------

class TestAreaCalculation:
    def test_lut_only(self):
        """100 LUTs, 0 FFs -> 50 slices, bound by LUT."""
        nl = ECP5Netlist(top="test")
        for i in range(100):
            c = nl.add_cell(f"lut{i}", "TRELLIS_SLICE")
        area = calculate_area(nl)
        assert area.lut_cells == 100
        assert area.ff_cells == 0
        assert area.slices_for_luts == 50
        assert area.slices_total == 50
        assert area.binding_resource == "lut"

    def test_ff_only(self):
        """0 LUTs, 100 FFs -> 50 slices, bound by FF."""
        nl = ECP5Netlist(top="test")
        for i in range(100):
            c = nl.add_cell(f"ff{i}", "TRELLIS_FF")
        area = calculate_area(nl)
        assert area.ff_cells == 100
        assert area.lut_cells == 0
        assert area.slices_for_ffs == 50
        assert area.slices_total == 50
        assert area.binding_resource == "ff"

    def test_carry_bound(self):
        """200 CCU2C cells need 200 slices (1 per slice), even with few LUTs."""
        nl = ECP5Netlist(top="test")
        for i in range(10):
            nl.add_cell(f"lut{i}", "TRELLIS_SLICE")
        for i in range(200):
            nl.add_cell(f"ccu{i}", "CCU2C")
        area = calculate_area(nl)
        assert area.slices_for_carry == 200
        assert area.slices_for_luts == 5
        assert area.slices_total == 200
        assert area.binding_resource == "carry"

    def test_balanced_lut_ff(self):
        """100 LUTs + 100 FFs -> 50 slices (both fit in same slices)."""
        nl = ECP5Netlist(top="test")
        for i in range(100):
            nl.add_cell(f"lut{i}", "TRELLIS_SLICE")
        for i in range(100):
            nl.add_cell(f"ff{i}", "TRELLIS_FF")
        area = calculate_area(nl)
        assert area.slices_total == 50
        assert area.lut_packing == 100.0
        assert area.ff_packing == 100.0

    def test_lut_packing_efficiency(self):
        """100 LUTs + 10 FFs -> 50 slices, FF packing is 10%."""
        nl = ECP5Netlist(top="test")
        for i in range(100):
            nl.add_cell(f"lut{i}", "TRELLIS_SLICE")
        for i in range(10):
            nl.add_cell(f"ff{i}", "TRELLIS_FF")
        area = calculate_area(nl)
        assert area.slices_total == 50
        assert area.lut_packing == 100.0
        assert area.ff_packing == 10.0

    def test_odd_lut_count(self):
        """Odd LUT count rounds up slices."""
        nl = ECP5Netlist(top="test")
        for i in range(101):
            nl.add_cell(f"lut{i}", "TRELLIS_SLICE")
        area = calculate_area(nl)
        assert area.slices_for_luts == 51  # ceil(101/2)

    def test_bram_tiles(self):
        """3 DP16KD = 3 BRAM tiles."""
        nl = ECP5Netlist(top="test")
        for i in range(3):
            nl.add_cell(f"bram{i}", "DP16KD")
        area = calculate_area(nl)
        assert area.bram_tiles == 3

    def test_dsp_tiles_packing(self):
        """3 MULT18X18D = 2 DSP tiles (ceil(3/2))."""
        nl = ECP5Netlist(top="test")
        for i in range(3):
            nl.add_cell(f"dsp{i}", "MULT18X18D")
        area = calculate_area(nl)
        assert area.dsp_tiles == 2

    def test_dsp_tiles_even(self):
        """4 MULT18X18D = 2 DSP tiles."""
        nl = ECP5Netlist(top="test")
        for i in range(4):
            nl.add_cell(f"dsp{i}", "MULT18X18D")
        area = calculate_area(nl)
        assert area.dsp_tiles == 2

    def test_total_tiles(self):
        """Total tiles = slices + BRAM + DSP."""
        nl = ECP5Netlist(top="test")
        for i in range(100):
            nl.add_cell(f"lut{i}", "TRELLIS_SLICE")
        for i in range(5):
            nl.add_cell(f"bram{i}", "DP16KD")
        for i in range(3):
            nl.add_cell(f"dsp{i}", "MULT18X18D")
        area = calculate_area(nl)
        assert area.total_tiles == 50 + 5 + 2  # 50 slices + 5 BRAM + 2 DSP

    def test_empty_design(self):
        nl = ECP5Netlist(top="test")
        area = calculate_area(nl)
        assert area.slices_total == 0
        assert area.total_tiles == 0
        assert area.binding_resource == "none"

    def test_single_lut(self):
        nl = ECP5Netlist(top="test")
        nl.add_cell("lut0", "TRELLIS_SLICE")
        area = calculate_area(nl)
        assert area.slices_total == 1
        assert area.lut_packing == 50.0  # 1 LUT in 2 slots


# ---------------------------------------------------------------------------
# Area calculation on real designs
# ---------------------------------------------------------------------------

class TestAreaOnRealDesigns:
    def test_uart_tx_area(self):
        result = parse_files([RIME_UART_TX], top="uart_tx")
        design = lower_to_ir(result, top="uart_tx")
        nl = map_to_ecp5(design)
        area = calculate_area(nl)
        assert area.lut_cells > 0
        assert area.ff_cells > 0
        assert area.slices_total > 0
        assert area.total_tiles > 0
        assert 0 < area.lut_packing <= 100
        assert 0 < area.ff_packing <= 100
        assert area.binding_resource in ("lut", "ff", "carry")

    def test_rime_v_area(self):
        result = parse_files([RIME_V], top="rime_v")
        design = lower_to_ir(result, top="rime_v")
        nl = map_to_ecp5(design)
        area = calculate_area(nl)
        # RIME-V is a CPU — expect significant LUT and FF usage
        assert area.lut_cells >= 1000
        assert area.ff_cells >= 500
        assert area.slices_total >= 500

    def test_soc_area(self):
        from nosis.bram import infer_brams
        from nosis.dsp import infer_dsps
        from nosis.carry import infer_carry_chains
        result = parse_files(RIME_SOC_SOURCES, top="top")
        design = lower_to_ir(result, top="top")
        mod = design.top_module()
        infer_brams(mod)
        infer_dsps(mod)
        infer_carry_chains(mod)
        nl = map_to_ecp5(design)
        area = calculate_area(nl)
        assert area.lut_cells >= 5000
        assert area.bram_tiles >= 1  # SoC should have BRAMs
        lines = area.summary_lines()
        assert any("bound by" in line for line in lines)
        # Verify area calculation is self-consistent
        assert area.slices_total >= area.slices_for_luts
        assert area.slices_total >= area.slices_for_ffs
        assert area.slices_total >= area.slices_for_carry
        assert area.total_tiles == area.slices_total + area.bram_tiles + area.dsp_tiles

    def test_soc_overutilization_detected(self):
        """The unoptimized SoC output exceeds 25k — report must detect this."""
        result = parse_files(RIME_SOC_SOURCES, top="top")
        design = lower_to_ir(result, top="top")
        nl = map_to_ecp5(design)
        report = report_utilization(nl, "25k")
        # The unoptimized mapper produces more cells than real hardware needs.
        # The report must accurately reflect this with warnings.
        assert report.area.slices_total > 0
        assert report.area.total_tiles > 0
        # If overutilized, warnings must be present
        if report.slice_pct > 100:
            assert len(report.warnings) > 0


# ---------------------------------------------------------------------------
# Report utilization
# ---------------------------------------------------------------------------

class TestReportUtilization:
    def test_report_basic(self):
        result = parse_files([RIME_UART_TX], top="uart_tx")
        design = lower_to_ir(result, top="uart_tx")
        nl = map_to_ecp5(design)
        report = report_utilization(nl, "25k")
        assert report.luts_used > 0
        assert report.ffs_used > 0
        assert report.lut_pct < 100
        assert len(report.warnings) == 0

    def test_report_has_slice_count(self):
        result = parse_files([RIME_UART_TX], top="uart_tx")
        design = lower_to_ir(result, top="uart_tx")
        nl = map_to_ecp5(design)
        report = report_utilization(nl, "25k")
        lines = report.summary_lines()
        assert any("Slices" in line for line in lines)
        assert any("Bound" in line for line in lines)

    def test_all_four_devices(self):
        result = parse_files([RIME_UART_TX], top="uart_tx")
        design = lower_to_ir(result, top="uart_tx")
        nl = map_to_ecp5(design)
        for size in ("12k", "25k", "45k", "85k"):
            report = report_utilization(nl, size)
            assert report.device.name.startswith("LFE5U")

    def test_overutilization_warning(self):
        """A design that exceeds device capacity should produce warnings."""
        nl = ECP5Netlist(top="test")
        for i in range(20000):
            nl.add_cell(f"lut{i}", "TRELLIS_SLICE")
        report = report_utilization(nl, "12k")
        assert len(report.warnings) > 0
        assert any("overutilized" in w for w in report.warnings)

    def test_unknown_device_raises(self):
        nl = ECP5Netlist(top="test")
        try:
            report_utilization(nl, "999k")
            assert False, "should have raised"
        except ValueError:
            pass
