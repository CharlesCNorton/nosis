"""Consolidated SoC tests — requires full RIME source tree.

Tests rime_v (CPU), thaw (flash service image), picorv32 SoC, and
language features exercised by these designs. All marked requires_rime_soc.
"""


from nosis.ir import PrimOp
from nosis.passes import run_default_passes
from nosis.techmap import map_to_ecp5
from nosis.fsm import extract_fsms
from nosis.timing import analyze_timing
from nosis.resources import calculate_area, report_utilization
from tests.conftest import (
    get_design, requires_rime_soc,
    RIME_SOC_SOURCES,
)


# ---------------------------------------------------------------------------
# rime_v — RV32IMC CPU core
# ---------------------------------------------------------------------------

@requires_rime_soc
class TestRimeV:

    def _d(self):
        return get_design("rime_v")

    def test_parse_zero_errors(self):
        assert len(self._d().parsed.errors) == 0

    def test_ir_cell_count(self):
        s = self._d().mod.stats()
        assert s["cells"] >= 500
        assert s["nets"] >= 500

    def test_has_ffs(self):
        ffs = [c for c in self._d().mod.cells.values() if c.op == PrimOp.FF]
        assert len(ffs) >= 30

    def test_techmap(self):
        s = self._d().netlist.stats()
        assert s.get("LUT4", 0) >= 500
        assert s.get("TRELLIS_FF", 0) >= 500

    def test_locked_counts(self):
        s = self._d().netlist.stats()
        assert 5500 <= s["LUT4"] <= 14500, f"LUT: {s['LUT4']}"
        assert 1650 <= s["TRELLIS_FF"] <= 1750, f"FF: {s['TRELLIS_FF']}"
        assert 240 <= s["CCU2C"] <= 400, f"CCU2C: {s['CCU2C']}"

    def test_json_roundtrip(self):
        data = self._d().json_data
        cells = data["modules"]["rime_v"]["cells"]
        assert len(cells) > 0
        for name, cell in cells.items():
            assert "type" in cell
            assert "connections" in cell

    def test_optimization_reduces_cells(self):
        mod = self._d().mod
        before = mod.stats()["cells"]
        run_default_passes(mod)
        assert mod.stats()["cells"] < before

    def test_fsm_detected(self):
        assert len(extract_fsms(self._d().mod)) >= 1

    def test_timing(self):
        report = analyze_timing(self._d().design.top_module())
        assert report.max_delay_ns > 0

    def test_area(self):
        area = calculate_area(self._d().netlist)
        assert area.lut_cells >= 1000
        assert area.ff_cells >= 500
        assert area.slices_total >= 500

    def test_ir_connectivity(self):
        mod = self._d().mod
        for cell in mod.cells.values():
            for pn, net in cell.inputs.items():
                assert net.name in mod.nets
            for pn, net in cell.outputs.items():
                assert net.name in mod.nets

    def test_json_invariants(self):
        data = self._d().json_data
        for cn, cell in data["modules"]["rime_v"]["cells"].items():
            for port, bits in cell["connections"].items():
                for bit in bits:
                    assert isinstance(bit, int) or (isinstance(bit, str) and bit in ("0", "1", "x"))

    def test_optimization_reduces(self):
        mod = self._d().mod
        before = mod.stats()["cells"]
        run_default_passes(mod)
        after = mod.stats()["cells"]
        assert after < before


# ---------------------------------------------------------------------------
# thaw — flash service image (multi-file hierarchy)
# ---------------------------------------------------------------------------

@requires_rime_soc
class TestThaw:

    def _d(self):
        return get_design("thaw")

    def test_parse_zero_errors(self):
        assert len(self._d().parsed.errors) == 0

    def test_ir_cell_count(self):
        assert self._d().mod.stats()["cells"] >= 1000

    def test_port_count(self):
        assert len(self._d().mod.ports) >= 15

    def test_techmap(self):
        s = self._d().netlist.stats()
        assert s.get("LUT4", 0) >= 1000
        assert s.get("TRELLIS_FF", 0) >= 500

    def test_locked_counts(self):
        s = self._d().netlist.stats()
        assert 20000 <= s["LUT4"] <= 35000, f"LUT: {s['LUT4']}"
        assert 4000 <= s["TRELLIS_FF"] <= 5700, f"FF: {s['TRELLIS_FF']}"
        assert 900 <= s["CCU2C"] <= 1100, f"CCU2C: {s['CCU2C']}"

    def test_json_valid_and_complete(self):
        data = self._d().json_data
        mod = data["modules"]["top"]
        assert len(mod["ports"]) >= 15
        assert len(mod["cells"]) >= 1000
        for cn, cell in mod["cells"].items():
            for port, bits in cell["connections"].items():
                for bit in bits:
                    assert isinstance(bit, int) or (isinstance(bit, str) and bit in ("0", "1", "x"))

    def test_hierarchy_nets_prefixed(self):
        mod = self._d().mod
        prefixed = [n for n in mod.nets if n.startswith("RX.") or n.startswith("TX.") or n.startswith("SPI.")]
        assert len(prefixed) > 0

    def test_ir_connectivity(self):
        mod = self._d().mod
        for cell in mod.cells.values():
            for pn, net in cell.inputs.items():
                assert net.name in mod.nets

    def test_json_invariants(self):
        data = self._d().json_data
        for cn, cell in data["modules"]["top"]["cells"].items():
            for port, bits in cell["connections"].items():
                for bit in bits:
                    assert isinstance(bit, int) or (isinstance(bit, str) and bit in ("0", "1", "x"))


# ---------------------------------------------------------------------------
# Frost — BRAM-backed configurable grid
# ---------------------------------------------------------------------------

@requires_rime_soc
class TestFrost:

    def _d(self):
        return get_design("frost")

    def test_parse_zero_errors(self):
        assert len(self._d().parsed.errors) == 0

    def test_ir_cell_count(self):
        assert self._d().mod.stats()["cells"] >= 500

    def test_techmap(self):
        s = self._d().netlist.stats()
        assert s.get("LUT4", 0) >= 1000
        assert s.get("TRELLIS_FF", 0) >= 500


# ---------------------------------------------------------------------------
# Slush — register-backed configurable grid
# ---------------------------------------------------------------------------

@requires_rime_soc
class TestSlush:

    def _d(self):
        return get_design("slush")

    def test_parse_zero_errors(self):
        assert len(self._d().parsed.errors) == 0

    def test_ir_cell_count(self):
        assert self._d().mod.stats()["cells"] >= 500

    def test_techmap(self):
        s = self._d().netlist.stats()
        assert s.get("LUT4", 0) >= 1000
        assert s.get("TRELLIS_FF", 0) >= 500


# ---------------------------------------------------------------------------
# Ember — hardware TRNG with ring oscillators and AES-128-CBC
# ---------------------------------------------------------------------------

@requires_rime_soc
class TestEmber:

    def _d(self):
        return get_design("ember")

    def test_parse_zero_errors(self):
        assert len(self._d().parsed.errors) == 0

    def test_ir_cell_count(self):
        assert self._d().mod.stats()["cells"] >= 200

    def test_techmap(self):
        s = self._d().netlist.stats()
        assert s.get("LUT4", 0) >= 500
        assert s.get("TRELLIS_FF", 0) >= 500


# ---------------------------------------------------------------------------
# PicoRV32 SoC — full board image
# ---------------------------------------------------------------------------

@requires_rime_soc
class TestSoC:

    def _d(self):
        return get_design("soc")

    def test_parse_zero_errors(self):
        assert len(self._d().parsed.errors) == 0

    def test_ir_cell_count(self):
        assert self._d().mod.stats()["cells"] >= 4000

    def test_port_count(self):
        assert len(self._d().mod.ports) >= 20

    def test_memory_cells(self):
        mod = self._d().mod
        mem = [c for c in mod.cells.values() if c.op == PrimOp.MEMORY]
        assert len(mem) >= 3
        for cell in mem:
            assert "depth" in cell.params
            assert "width" in cell.params
            assert cell.params["depth"] > 0

    def test_techmap(self):
        s = self._d().netlist.stats()
        assert s.get("LUT4", 0) >= 5000
        assert s.get("TRELLIS_FF", 0) >= 3000

    def test_locked_counts(self):
        s = self._d().netlist.stats()
        # Unoptimized counts — MEMORY write ports add cells, DPR16X4 disabled
        assert 55000 <= s["LUT4"] <= 120000, f"LUT: {s['LUT4']}"
        assert 7500 <= s["TRELLIS_FF"] <= 18000, f"FF: {s['TRELLIS_FF']}"
        assert 2200 <= s["CCU2C"] <= 4500, f"CCU2C: {s['CCU2C']}"

    def test_json_structural(self):
        data = self._d().json_data
        cells = data["modules"]["top"]["cells"]
        assert len(cells) >= 5000
        valid_types = {"LUT4", "TRELLIS_FF", "CCU2C", "MULT18X18D", "DP16KD", "TRELLIS_DPR16X4", "ALU54B", "BB"}
        for name, cell in cells.items():
            assert cell["type"] in valid_types, f"unexpected: {cell['type']}"

    def test_all_output_ports_driven(self):
        mod = self._d().mod
        undriven = []
        for pname, pnet in mod.ports.items():
            is_out = any(c.op == PrimOp.OUTPUT and any(i.name == pname for i in c.inputs.values()) for c in mod.cells.values())
            if is_out and pnet.driver is None:
                undriven.append(pname)
        assert len(undriven) == 0, f"undriven: {undriven}"

    def test_optimization_ff_count(self):
        mod = self._d().mod
        run_default_passes(mod)
        ffs = sum(1 for c in mod.cells.values() if c.op == PrimOp.FF)
        assert ffs >= 500

    def test_optimized_lut_regression(self):
        from nosis.frontend import parse_files, lower_to_ir
        from nosis.slicepack import pack_slices
        r = parse_files(RIME_SOC_SOURCES, top="top")
        d = lower_to_ir(r, top="top")
        m = d.top_module()
        run_default_passes(m)
        nl = map_to_ecp5(d)
        pack_slices(nl)
        assert nl.stats().get("LUT4", 0) < 32000

    def test_area(self):
        from nosis.bram import infer_brams
        from nosis.dsp import infer_dsps
        from nosis.carry import infer_carry_chains
        from nosis.frontend import parse_files, lower_to_ir
        r = parse_files(RIME_SOC_SOURCES, top="top")
        d = lower_to_ir(r, top="top")
        mod = d.top_module()
        infer_brams(mod)
        infer_dsps(mod)
        infer_carry_chains(mod)
        nl = map_to_ecp5(d)
        area = calculate_area(nl)
        assert area.lut_cells >= 5000
        assert area.bram_tiles >= 1

    def test_overutilization_detected(self):
        nl = self._d().netlist
        report = report_utilization(nl, "25k")
        assert report.area.slices_total > 0

    def test_clock_domain(self):
        from nosis.clocks import analyze_clock_domains
        domains, _ = analyze_clock_domains(self._d().mod)
        assert len(domains) >= 1

    def test_bram_inference(self):
        from nosis.bram import infer_brams
        mod = self._d().mod
        infer_brams(mod)
        # DPR16X4 is disabled; large arrays should infer DP16KD
        dp16kd = [c for c in mod.cells.values()
                  if c.op == PrimOp.MEMORY and c.params.get("bram_config") == "DP16KD"]
        assert len(dp16kd) >= 1

    def test_lut_packing(self):
        from nosis.lutpack import pack_luts_ir
        mod = self._d().mod
        before = mod.stats()["cells"]
        pack_luts_ir(mod)
        assert mod.stats()["cells"] <= before

    def test_memory_write_ports_wired(self):
        """MEMORY cells for writable arrays must have WADDR, WDATA, WE, CLK."""
        from nosis.frontend import parse_files, lower_to_ir
        r = parse_files(RIME_SOC_SOURCES, top="top")
        d = lower_to_ir(r, top="top")
        mod = d.top_module()
        writable_names = {"uart_rx_fifo", "uart_tx_fifo", "progmem", "sd_wbuf"}
        for cell in mod.cells.values():
            if cell.op != PrimOp.MEMORY:
                continue
            mem_name = cell.params.get("mem_name", "")
            base = mem_name.rsplit(".", 1)[-1] if "." in mem_name else mem_name
            if base not in writable_names:
                continue
            assert "WADDR" in cell.inputs, f"{mem_name} missing WADDR"
            assert "WDATA" in cell.inputs, f"{mem_name} missing WDATA"
            assert "WE" in cell.inputs, f"{mem_name} missing WE"
            assert "CLK" in cell.inputs, f"{mem_name} missing CLK"

    def test_no_comb_loops_after_slicepack(self):
        """Post-slicepack netlist must have no LUT4 self-loops."""
        from nosis.frontend import parse_files, lower_to_ir
        from nosis.slicepack import pack_slices
        r = parse_files(RIME_SOC_SOURCES, top="top")
        d = lower_to_ir(r, top="top")
        m = d.top_module()
        run_default_passes(m)
        from nosis.bram import infer_brams
        from nosis.carry import infer_carry_chains
        from nosis.fsm import extract_fsms, annotate_fsm_cells
        infer_brams(m)
        infer_carry_chains(m)
        fsms = extract_fsms(m)
        annotate_fsm_cells(m, fsms)
        nl = map_to_ecp5(d)
        sp = pack_slices(nl)
        assert sp["loops_broken"] >= 0
        # Verify no remaining self-loops
        for cell in nl.cells.values():
            if cell.cell_type != "LUT4":
                continue
            z = cell.ports.get("Z", [None])[0]
            if not isinstance(z, int):
                continue
            for pin in ("A", "B", "C", "D"):
                bits = cell.ports.get(pin, [])
                if bits and isinstance(bits[0], int) and bits[0] == z:
                    assert False, f"Self-loop in {cell.name}: {pin}==Z (bit {z})"

    def test_port_netname_consistency(self):
        """Port bits and netname bits must agree in the JSON output."""
        from nosis.frontend import parse_files, lower_to_ir
        from nosis.slicepack import pack_slices
        from nosis.json_backend import emit_json_str
        import json
        r = parse_files(RIME_SOC_SOURCES, top="top")
        d = lower_to_ir(r, top="top")
        m = d.top_module()
        run_default_passes(m)
        nl = map_to_ecp5(d)
        pack_slices(nl)
        data = json.loads(emit_json_str(nl))
        mod = data["modules"]["top"]
        for pname, port in mod["ports"].items():
            if pname in mod["netnames"]:
                assert port["bits"] == mod["netnames"][pname]["bits"], \
                    f"port/netname mismatch for {pname}"

    def test_readmemh_init_files_detected(self):
        """$readmemh associations must be detected and applied to MEMORY cells."""
        from nosis.frontend import parse_files, lower_to_ir
        r = parse_files(RIME_SOC_SOURCES, top="top")
        assert "progmem" in r.readmem_associations
        assert r.readmem_associations["progmem"] == ("firmware.hex", "hex")
        d = lower_to_ir(r, top="top")
        mod = d.top_module()
        init_cells = [c for c in mod.cells.values()
                      if c.op == PrimOp.MEMORY and c.params.get("init_file")]
        assert len(init_cells) >= 2
        names = {c.params.get("mem_name", "").rsplit(".", 1)[-1] for c in init_cells}
        assert "progmem" in names
        assert "bootrom" in names

    def test_optimized_soc_lut_count(self):
        """Optimized SoC LUT count must stay under 15000 after slicepack."""
        from nosis.frontend import parse_files, lower_to_ir
        from nosis.slicepack import pack_slices
        from nosis.bram import infer_brams
        from nosis.carry import infer_carry_chains
        from nosis.fsm import extract_fsms, annotate_fsm_cells
        from nosis.lutpack import pack_luts_ir
        r = parse_files(RIME_SOC_SOURCES, top="top")
        d = lower_to_ir(r, top="top")
        m = d.top_module()
        run_default_passes(m)
        infer_brams(m)
        infer_carry_chains(m)
        fsms = extract_fsms(m)
        annotate_fsm_cells(m, fsms)
        pack_luts_ir(m)
        nl = map_to_ecp5(d)
        pack_slices(nl)
        luts = nl.stats().get("LUT4", 0)
        assert luts <= 15000, f"LUT count {luts} exceeds 15000"
        assert luts >= 8000, f"LUT count {luts} suspiciously low"


# ---------------------------------------------------------------------------
# picorv32 language feature coverage
# ---------------------------------------------------------------------------

@requires_rime_soc
class TestPicoRV32Features:

    def _d(self):
        return get_design("picorv32")

    def test_replication(self):
        mod = self._d().mod
        repeat_cells = [c for c in mod.cells.values() if c.op == PrimOp.REPEAT]
        assert len(repeat_cells) >= 1
        for cell in repeat_cells:
            assert "A" in cell.inputs
            assert "count" in cell.params

    def test_sshr(self):
        mod = self._d().mod
        sshr_cells = [c for c in mod.cells.values() if c.op == PrimOp.SSHR]
        assert len(sshr_cells) >= 1
        for cell in sshr_cells:
            assert "A" in cell.inputs
            assert "B" in cell.inputs

    def test_sshr_techmap(self):
        assert self._d().netlist.stats().get("LUT4", 0) > 0

    def test_call_expressions_resolved(self):
        mod = self._d().mod
        call_unsupported = [c for c in mod.cells.values() if "unsupported" in c.name.lower() and "Call" in c.name]
        assert len(call_unsupported) == 0


# ---------------------------------------------------------------------------
# sdram_controller — standalone
# ---------------------------------------------------------------------------

@requires_rime_soc
class TestSdramController:

    def _d(self):
        return get_design("sdram_ctrl")

    def test_parse_zero_errors(self):
        assert len(self._d().parsed.errors) == 0

    def test_port_count(self):
        assert len(self._d().mod.ports) >= 15

    def test_ff_count(self):
        ffs = [c for c in self._d().mod.cells.values() if c.op == PrimOp.FF]
        assert len(ffs) >= 10
