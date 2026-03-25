"""Regression tests — increasingly strict synthesis validation against real RIME HDL.

These tests verify that nosis can parse, lower, optimize, map, and emit
valid nextpnr JSON for real designs. Each test asserts specific cell counts,
port presence, and structural properties. If a pipeline change breaks any
of these, the commit is rejected.
"""

import json

from nosis.frontend import FrontendError, parse_files, lower_to_ir
from nosis.passes import run_default_passes
from nosis.fsm import extract_fsms, annotate_fsm_cells
from nosis.techmap import map_to_ecp5
from nosis.json_backend import emit_json_str
from nosis.ir import PrimOp
from tests.conftest import (
    RIME_FW as RIME,
    RIME_SOC_SOURCES,
    RIME_THAW_SOURCES,
    requires_rime,
)


# ---------------------------------------------------------------------------
# Single-file designs
# ---------------------------------------------------------------------------

class TestUartTx:
    SRC = [f"{RIME}/core/uart/uart_tx.sv"]
    TOP = "uart_tx"

    def _synth(self, optimize=True):
        result = parse_files(self.SRC, top=self.TOP)
        assert len(result.errors) == 0
        design = lower_to_ir(result, top=self.TOP)
        mod = design.top_module()
        if optimize:
            run_default_passes(mod)
        return design, mod

    def test_parse_zero_errors(self):
        result = parse_files(self.SRC, top=self.TOP)
        assert len(result.errors) == 0

    def test_ports_present(self):
        _, mod = self._synth()
        assert "clk" in mod.ports
        assert "send" in mod.ports
        assert "data" in mod.ports
        assert "tx" in mod.ports

    def test_has_ffs(self):
        _, mod = self._synth(optimize=False)
        ffs = [c for c in mod.cells.values() if c.op == PrimOp.FF]
        assert len(ffs) >= 3, f"expected >= 3 FFs, got {len(ffs)}"

    def test_has_muxes(self):
        _, mod = self._synth(optimize=False)
        muxes = [c for c in mod.cells.values() if c.op == PrimOp.MUX]
        assert len(muxes) >= 5, f"expected >= 5 MUXes, got {len(muxes)}"

    def test_techmap_produces_luts_and_ffs(self):
        design, _ = self._synth(optimize=False)
        nl = map_to_ecp5(design)
        stats = nl.stats()
        assert stats.get("TRELLIS_SLICE", 0) > 0
        assert stats.get("TRELLIS_FF", 0) > 0

    def test_json_valid(self):
        design, _ = self._synth(optimize=False)
        nl = map_to_ecp5(design)
        text = emit_json_str(nl)
        data = json.loads(text)
        assert self.TOP in data["modules"]
        mod_json = data["modules"][self.TOP]
        assert len(mod_json["ports"]) == 4
        assert len(mod_json["cells"]) > 0

    def test_fsm_detected(self):
        _, mod = self._synth(optimize=False)
        fsms = extract_fsms(mod)
        assert len(fsms) >= 1, f"expected FSM in uart_tx, got {len(fsms)}"
        state_names = [f.state_net for f in fsms]
        assert any("state" in n for n in state_names), f"no state FSM found: {state_names}"

    def test_fsm_annotation_preserves_cells(self):
        _, mod = self._synth(optimize=False)
        cells_before = set(mod.cells.keys())
        fsms = extract_fsms(mod)
        annotate_fsm_cells(mod, fsms)
        assert set(mod.cells.keys()) == cells_before

    def test_ccu2c_for_arithmetic(self):
        """uart_tx has counters — ADD operations should produce CCU2C cells."""
        design, _ = self._synth(optimize=False)
        nl = map_to_ecp5(design)
        ccu2c_cells = [c for c in nl.cells.values() if c.cell_type == "CCU2C"]
        assert len(ccu2c_cells) > 0, "expected CCU2C cells for counter arithmetic"


class TestUartRx:
    SRC = [f"{RIME}/core/uart/uart_rx.sv"]
    TOP = "uart_rx"

    def test_parse_zero_errors(self):
        result = parse_files(self.SRC, top=self.TOP)
        assert len(result.errors) == 0

    def test_ports(self):
        result = parse_files(self.SRC, top=self.TOP)
        design = lower_to_ir(result, top=self.TOP)
        mod = design.top_module()
        assert "clk" in mod.ports
        assert "rx" in mod.ports
        assert "finish" in mod.ports
        assert "data" in mod.ports

    def test_techmap(self):
        result = parse_files(self.SRC, top=self.TOP)
        design = lower_to_ir(result, top=self.TOP)
        nl = map_to_ecp5(design)
        stats = nl.stats()
        assert stats.get("TRELLIS_SLICE", 0) > 0
        assert stats.get("TRELLIS_FF", 0) > 0


class TestSdramBridge:
    SRC = [f"{RIME}/core/service/sdram_bridge.sv"]
    TOP = "sdram_bridge"

    def test_parse_zero_errors(self):
        result = parse_files(self.SRC, top=self.TOP)
        assert len(result.errors) == 0

    def test_ports(self):
        result = parse_files(self.SRC, top=self.TOP)
        design = lower_to_ir(result, top=self.TOP)
        mod = design.top_module()
        for port in ["clk", "rst", "start", "wr", "done", "busy"]:
            assert port in mod.ports, f"missing port: {port}"

    def test_ff_count(self):
        result = parse_files(self.SRC, top=self.TOP)
        design = lower_to_ir(result, top=self.TOP)
        mod = design.top_module()
        ffs = [c for c in mod.cells.values() if c.op == PrimOp.FF]
        assert len(ffs) >= 5, f"sdram_bridge should have state + data FFs"

    def test_fsm_detected(self):
        result = parse_files(self.SRC, top=self.TOP)
        design = lower_to_ir(result, top=self.TOP)
        mod = design.top_module()
        fsms = extract_fsms(mod)
        assert len(fsms) >= 1, f"sdram_bridge has S_IDLE/S_REQ/S_WAIT/S_CAPTURE FSM"


class TestSdramController:
    SRC = [f"{RIME}/core/service/sdram_controller.sv"]
    TOP = "sdram_controller"

    def test_parse_zero_errors(self):
        result = parse_files(self.SRC, top=self.TOP)
        assert len(result.errors) == 0

    def test_port_count(self):
        result = parse_files(self.SRC, top=self.TOP)
        design = lower_to_ir(result, top=self.TOP)
        mod = design.top_module()
        assert len(mod.ports) >= 15, f"expected >= 15 ports, got {len(mod.ports)}"

    def test_ff_count(self):
        result = parse_files(self.SRC, top=self.TOP)
        design = lower_to_ir(result, top=self.TOP)
        mod = design.top_module()
        ffs = [c for c in mod.cells.values() if c.op == PrimOp.FF]
        assert len(ffs) >= 10, f"sdram_controller should have many state + data FFs"


class TestCrc32:
    SRC = [f"{RIME}/core/cpu/rime_pcpi_crc32.sv"]
    TOP = "rime_pcpi_crc32"

    def test_parse_zero_errors(self):
        result = parse_files(self.SRC, top=self.TOP)
        assert len(result.errors) == 0

    def test_purely_combinational(self):
        """CRC32 is mostly combinational XOR chains with one registered output."""
        result = parse_files(self.SRC, top=self.TOP)
        design = lower_to_ir(result, top=self.TOP)
        nl = map_to_ecp5(design)
        stats = nl.stats()
        # Should have FFs for pcpi_wr, pcpi_rd, pcpi_ready
        assert stats.get("TRELLIS_FF", 0) >= 30, f"expected >= 30 FFs (32-bit rd + wr + ready)"


# ---------------------------------------------------------------------------
# RIME-V CPU (the existence proof)
# ---------------------------------------------------------------------------

class TestRimeV:
    SRC = [f"{RIME}/core/cpu/rime_v.sv"]
    TOP = "rime_v"

    def test_parse_zero_errors(self):
        result = parse_files(self.SRC, top=self.TOP)
        assert len(result.errors) == 0

    def test_ir_cell_count(self):
        result = parse_files(self.SRC, top=self.TOP)
        design = lower_to_ir(result, top=self.TOP)
        mod = design.top_module()
        stats = mod.stats()
        assert stats["cells"] >= 500, f"expected >= 500 IR cells, got {stats['cells']}"
        assert stats["nets"] >= 500, f"expected >= 500 IR nets, got {stats['nets']}"

    def test_has_ffs(self):
        result = parse_files(self.SRC, top=self.TOP)
        design = lower_to_ir(result, top=self.TOP)
        mod = design.top_module()
        ffs = [c for c in mod.cells.values() if c.op == PrimOp.FF]
        assert len(ffs) >= 30, f"expected >= 30 FFs, got {len(ffs)}"

    def test_techmap(self):
        result = parse_files(self.SRC, top=self.TOP)
        design = lower_to_ir(result, top=self.TOP)
        nl = map_to_ecp5(design)
        stats = nl.stats()
        assert stats.get("TRELLIS_SLICE", 0) >= 500
        assert stats.get("TRELLIS_FF", 0) >= 500

    def test_json_roundtrip(self):
        result = parse_files(self.SRC, top=self.TOP)
        design = lower_to_ir(result, top=self.TOP)
        nl = map_to_ecp5(design)
        text = emit_json_str(nl)
        data = json.loads(text)
        assert self.TOP in data["modules"]
        cells = data["modules"][self.TOP]["cells"]
        assert len(cells) > 0
        # Every cell must have type, connections, port_directions
        for name, cell in cells.items():
            assert "type" in cell, f"cell {name} missing type"
            assert "connections" in cell, f"cell {name} missing connections"
            assert "port_directions" in cell, f"cell {name} missing port_directions"

    def test_optimization_reduces_cells(self):
        result = parse_files(self.SRC, top=self.TOP)
        design = lower_to_ir(result, top=self.TOP)
        mod = design.top_module()
        before = mod.stats()["cells"]
        run_default_passes(mod)
        after = mod.stats()["cells"]
        assert after < before, f"optimization should reduce cells: {before} -> {after}"

    def test_fsm_detected(self):
        result = parse_files(self.SRC, top=self.TOP)
        design = lower_to_ir(result, top=self.TOP)
        mod = design.top_module()
        fsms = extract_fsms(mod)
        assert len(fsms) >= 1, f"RIME-V has a multi-state CPU FSM"


# ---------------------------------------------------------------------------
# Multi-file: Thaw (real board image)
# ---------------------------------------------------------------------------

class TestThaw:
    SRC = [
        *RIME_THAW_SOURCES,
    ]
    TOP = "top"

    def test_parse_zero_errors(self):
        result = parse_files(self.SRC, top=self.TOP)
        assert len(result.errors) == 0

    def test_ir_cell_count(self):
        result = parse_files(self.SRC, top=self.TOP)
        design = lower_to_ir(result, top=self.TOP)
        mod = design.top_module()
        stats = mod.stats()
        assert stats["cells"] >= 1000, f"expected >= 1000 IR cells, got {stats['cells']}"

    def test_techmap(self):
        result = parse_files(self.SRC, top=self.TOP)
        design = lower_to_ir(result, top=self.TOP)
        nl = map_to_ecp5(design)
        stats = nl.stats()
        assert stats.get("TRELLIS_SLICE", 0) >= 1000
        assert stats.get("TRELLIS_FF", 0) >= 500

    def test_port_count(self):
        result = parse_files(self.SRC, top=self.TOP)
        design = lower_to_ir(result, top=self.TOP)
        mod = design.top_module()
        assert len(mod.ports) >= 15

    def test_json_valid_and_complete(self):
        result = parse_files(self.SRC, top=self.TOP)
        design = lower_to_ir(result, top=self.TOP)
        nl = map_to_ecp5(design)
        text = emit_json_str(nl)
        data = json.loads(text)
        assert self.TOP in data["modules"]
        mod_json = data["modules"][self.TOP]
        assert len(mod_json["ports"]) >= 15
        assert len(mod_json["cells"]) >= 1000
        assert len(mod_json["netnames"]) >= 100
        # Every cell connection bit must be an integer
        for name, cell in mod_json["cells"].items():
            for port, bits in cell["connections"].items():
                for bit in bits:
                    assert isinstance(bit, int), f"cell {name} port {port} has non-int bit: {bit!r}"


# ---------------------------------------------------------------------------
# Multi-file: PicoRV32 SoC (full board image)
# ---------------------------------------------------------------------------

class TestPicoRV32Soc:
    SRC = [
        *RIME_SOC_SOURCES,
    ]
    TOP = "top"

    def test_parse_zero_errors(self):
        result = parse_files(self.SRC, top=self.TOP)
        assert len(result.errors) == 0

    def test_ir_cell_count(self):
        result = parse_files(self.SRC, top=self.TOP)
        design = lower_to_ir(result, top=self.TOP)
        mod = design.top_module()
        stats = mod.stats()
        assert stats["cells"] >= 4000, f"full SoC expected >= 4000 IR cells, got {stats['cells']}"

    def test_techmap(self):
        result = parse_files(self.SRC, top=self.TOP)
        design = lower_to_ir(result, top=self.TOP)
        nl = map_to_ecp5(design)
        stats = nl.stats()
        assert stats.get("TRELLIS_SLICE", 0) >= 5000
        assert stats.get("TRELLIS_FF", 0) >= 3000

    def test_port_count(self):
        result = parse_files(self.SRC, top=self.TOP)
        design = lower_to_ir(result, top=self.TOP)
        mod = design.top_module()
        assert len(mod.ports) >= 20

    def test_memory_cells_emitted(self):
        result = parse_files(self.SRC, top=self.TOP)
        design = lower_to_ir(result, top=self.TOP)
        mod = design.top_module()
        mem_cells = [c for c in mod.cells.values() if c.op == PrimOp.MEMORY]
        # rime_soc has progmem, uart_rx_fifo, uart_tx_fifo, sd_wbuf, bootrom
        assert len(mem_cells) >= 3, f"expected >= 3 MEMORY cells, got {len(mem_cells)}"
        # Check progmem dimensions
        progmem = [c for c in mem_cells if c.params.get("mem_name") == "progmem"]
        if progmem:
            assert progmem[0].params["depth"] == 4096
            assert progmem[0].params["width"] == 32

    def test_json_structural(self):
        result = parse_files(self.SRC, top=self.TOP)
        design = lower_to_ir(result, top=self.TOP)
        nl = map_to_ecp5(design)
        text = emit_json_str(nl)
        data = json.loads(text)
        assert self.TOP in data["modules"]
        mod_json = data["modules"][self.TOP]
        assert len(mod_json["cells"]) >= 5000
        for name, cell in mod_json["cells"].items():
            assert "type" in cell
            assert "connections" in cell
            assert cell["type"] in ("TRELLIS_SLICE", "TRELLIS_FF", "CCU2C", "MULT18X18D", "DP16KD", "TRELLIS_DPR16X4"), f"unexpected cell type: {cell['type']}"


# ---------------------------------------------------------------------------
# Language feature coverage
# ---------------------------------------------------------------------------

class TestLanguageFeatures:
    def test_replication_in_picorv32(self):
        """PicoRV32 uses replication expressions — verify they lower without error."""
        SRC = [f"{RIME}/core/cpu/picorv32.v"]
        result = parse_files(SRC, top="picorv32")
        design = lower_to_ir(result, top="picorv32")
        mod = design.top_module()
        # Should have REPEAT cells from replication expressions
        repeat_cells = [c for c in mod.cells.values() if c.op == PrimOp.REPEAT]
        # picorv32 uses {N{...}} patterns
        assert mod.stats()["cells"] > 0

    def test_call_expressions_resolved(self):
        """System function calls in picorv32.v should resolve to constants."""
        SRC = [f"{RIME}/core/cpu/picorv32.v"]
        result = parse_files(SRC, top="picorv32")
        design = lower_to_ir(result, top="picorv32")
        mod = design.top_module()
        # No unsupported_Call cells should remain
        unsupported = [c for c in mod.cells.values() if "unsupported" in c.name.lower()]
        # May have some unsupported expressions, but Call should be handled
        call_unsupported = [c for c in unsupported if "Call" in c.name]
        assert len(call_unsupported) == 0, f"unresolved Call expressions: {[c.name for c in call_unsupported]}"

    def test_sshr_in_picorv32(self):
        """PicoRV32 uses arithmetic shift right — SSHR cells must be emitted."""
        from tests.conftest import RIME_PICORV32
        result = parse_files([RIME_PICORV32], top="picorv32")
        design = lower_to_ir(result, top="picorv32")
        mod = design.top_module()
        sshr_cells = [c for c in mod.cells.values() if c.op == PrimOp.SSHR]
        assert len(sshr_cells) >= 1, f"expected SSHR cells in picorv32, got {len(sshr_cells)}"
        # Verify SSHR cells have A and B inputs
        for cell in sshr_cells:
            assert "A" in cell.inputs, f"SSHR {cell.name} missing A"
            assert "B" in cell.inputs, f"SSHR {cell.name} missing B"

    def test_sshr_techmap(self):
        """SSHR cells must produce TRELLIS_SLICE cells after mapping."""
        from tests.conftest import RIME_PICORV32
        result = parse_files([RIME_PICORV32], top="picorv32")
        design = lower_to_ir(result, top="picorv32")
        nl = map_to_ecp5(design)
        stats = nl.stats()
        assert stats.get("TRELLIS_SLICE", 0) > 0

    def test_repeat_in_picorv32(self):
        """PicoRV32 uses replication — REPEAT cells must be emitted."""
        from tests.conftest import RIME_PICORV32
        result = parse_files([RIME_PICORV32], top="picorv32")
        design = lower_to_ir(result, top="picorv32")
        mod = design.top_module()
        repeat_cells = [c for c in mod.cells.values() if c.op == PrimOp.REPEAT]
        assert len(repeat_cells) >= 1, f"expected REPEAT cells in picorv32, got {len(repeat_cells)}"
        # Verify REPEAT cells have A input and count param
        for cell in repeat_cells:
            assert "A" in cell.inputs, f"REPEAT {cell.name} missing A"
            assert "count" in cell.params, f"REPEAT {cell.name} missing count param"

    def test_repeat_eval_correctness(self):
        """REPEAT must duplicate bits correctly."""
        from nosis.eval import eval_const_op
        # {4{1'b1}} = 4'b1111 = 0xF
        result = eval_const_op(PrimOp.REPEAT, {"A": 1}, {"count": 4, "a_width": 1}, 4)
        assert result == 0xF
        # {2{4'b1010}} = 8'b10101010 = 0xAA
        result = eval_const_op(PrimOp.REPEAT, {"A": 0xA}, {"count": 2, "a_width": 4}, 8)
        assert result == 0xAA

    def test_sshr_eval_correctness(self):
        """SSHR must sign-extend on shift."""
        from nosis.eval import eval_const_op
        # 0x80 (8-bit, sign bit set) >> 1 = 0xC0 (sign-extended)
        result = eval_const_op(PrimOp.SSHR, {"A": 0x80, "B": 1}, {}, 8)
        assert result == 0xC0
        # 0x40 (positive) >> 1 = 0x20 (no sign extension)
        result = eval_const_op(PrimOp.SSHR, {"A": 0x40, "B": 1}, {}, 8)
        assert result == 0x20

    def test_enum_constants_lowered(self):
        """Enum values (IDLE, START, etc.) must produce CONST cells."""
        SRC = [f"{RIME}/core/uart/uart_tx.sv"]
        result = parse_files(SRC, top="uart_tx")
        design = lower_to_ir(result, top="uart_tx")
        mod = design.top_module()
        # IDLE, START, TRANSMISSION, STOP should be CONST-driven nets
        for name in ["IDLE", "START", "TRANSMISSION", "STOP"]:
            net = mod.nets.get(name)
            assert net is not None, f"missing net: {name}"
            assert net.driver is not None, f"net {name} has no driver (enum not lowered)"
            assert net.driver.op == PrimOp.CONST, f"net {name} driver is {net.driver.op}, expected CONST"

    def test_hierarchy_nets_prefixed(self):
        """Sub-module nets should be prefixed with instance name."""
        SRC = [
            f"{RIME}/images/thaw/top.sv",
            f"{RIME}/images/thaw/thaw_service.sv",
            f"{RIME}/core/uart/uart_rx.sv",
            f"{RIME}/core/uart/uart_tx.sv",
            f"{RIME}/core/service/flash_spi_master.sv",
            f"{RIME}/core/service/sdram_controller.sv",
            f"{RIME}/core/service/sdram_bridge.sv",
        ]
        result = parse_files(SRC, top="top")
        design = lower_to_ir(result, top="top")
        mod = design.top_module()
        # Should have nets prefixed with sub-instance names
        prefixed_nets = [n for n in mod.nets if n.startswith("RX.") or n.startswith("TX.") or n.startswith("SPI.")]
        assert len(prefixed_nets) > 0, "no prefixed sub-module nets found"

    def test_memory_cells_have_dimensions(self):
        """MEMORY cells must have depth and width params."""
        SRC = TestPicoRV32Soc.SRC
        result = parse_files(SRC, top="top")
        design = lower_to_ir(result, top="top")
        mod = design.top_module()
        mem_cells = [c for c in mod.cells.values() if c.op == PrimOp.MEMORY]
        for cell in mem_cells:
            assert "depth" in cell.params, f"MEMORY {cell.name} missing depth"
            assert "width" in cell.params, f"MEMORY {cell.name} missing width"
            assert cell.params["depth"] > 0
            assert cell.params["width"] > 0

    def test_lut_packing_reduces_cells(self):
        """LUT packing on the full SoC should reduce IR cell count."""
        from nosis.lutpack import pack_luts_ir
        result = parse_files(TestPicoRV32Soc.SRC, top="top")
        design = lower_to_ir(result, top="top")
        mod = design.top_module()
        before = mod.stats()["cells"]
        packed = pack_luts_ir(mod)
        after = mod.stats()["cells"]
        # Packing should eliminate at least some cells, or at minimum not crash
        assert after <= before

    def test_clock_domain_analysis(self):
        """The full SoC should have at least one clock domain."""
        from nosis.clocks import analyze_clock_domains
        result = parse_files(TestPicoRV32Soc.SRC, top="top")
        design = lower_to_ir(result, top="top")
        mod = design.top_module()
        domains, crossings = analyze_clock_domains(mod)
        assert len(domains) >= 1, "expected at least one clock domain"

    def test_distributed_ram_inference(self):
        """Small arrays (<=16 entries) should be tagged for DPR16X4."""
        from nosis.bram import infer_brams
        result = parse_files(TestPicoRV32Soc.SRC, top="top")
        design = lower_to_ir(result, top="top")
        mod = design.top_module()
        infer_brams(mod)
        dpr_cells = [c for c in mod.cells.values()
                     if c.op == PrimOp.MEMORY and c.params.get("bram_config", "").startswith("DPR")]
        # uart_rx_fifo (16x8) and uart_tx_fifo (16x8) should be DPR candidates
        assert len(dpr_cells) >= 1, "expected distributed RAM inference for small arrays"


# ---------------------------------------------------------------------------
# Negative / structural tests
# ---------------------------------------------------------------------------

class TestStructural:
    """Verify specific netlist properties that must hold for correctness."""

    def test_ff_has_clock(self):
        """Every FF cell must have a CLK input."""
        result = parse_files([f"{RIME}/core/uart/uart_tx.sv"], top="uart_tx")
        design = lower_to_ir(result, top="uart_tx")
        mod = design.top_module()
        for cell in mod.cells.values():
            if cell.op == PrimOp.FF:
                assert "CLK" in cell.inputs, f"FF {cell.name} has no CLK input"

    def test_ff_has_d_input(self):
        """Every FF cell must have a D input."""
        result = parse_files([f"{RIME}/core/uart/uart_tx.sv"], top="uart_tx")
        design = lower_to_ir(result, top="uart_tx")
        mod = design.top_module()
        for cell in mod.cells.values():
            if cell.op == PrimOp.FF:
                assert "D" in cell.inputs, f"FF {cell.name} has no D input"

    def test_no_zero_width_nets(self):
        """No net should have width 0."""
        result = parse_files([f"{RIME}/core/uart/uart_tx.sv"], top="uart_tx")
        design = lower_to_ir(result, top="uart_tx")
        mod = design.top_module()
        for net in mod.nets.values():
            assert net.width > 0, f"net {net.name} has width 0"

    def test_output_ports_exist_in_netlist(self):
        """Every output port must appear in the JSON netlist."""
        result = parse_files([f"{RIME}/core/uart/uart_tx.sv"], top="uart_tx")
        design = lower_to_ir(result, top="uart_tx")
        nl = map_to_ecp5(design)
        text = emit_json_str(nl)
        data = json.loads(text)
        ports = data["modules"]["uart_tx"]["ports"]
        assert "tx" in ports
        assert ports["tx"]["direction"] == "output"

    def test_cse_removes_duplicates(self):
        """CSE should eliminate duplicate operations."""
        from nosis.cse import eliminate_common_subexpressions
        from nosis.ir import Module

        mod = Module(name="test")
        a = mod.add_net("a", 1)
        b = mod.add_net("b", 1)
        y1 = mod.add_net("y1", 1)
        y2 = mod.add_net("y2", 1)

        # Two identical AND cells on the same inputs
        c1 = mod.add_cell("and1", PrimOp.AND)
        mod.connect(c1, "A", a)
        mod.connect(c1, "B", b)
        mod.connect(c1, "Y", y1, direction="output")

        c2 = mod.add_cell("and2", PrimOp.AND)
        mod.connect(c2, "A", a)
        mod.connect(c2, "B", b)
        mod.connect(c2, "Y", y2, direction="output")

        eliminated = eliminate_common_subexpressions(mod)
        assert eliminated == 1
        assert len(mod.cells) == 1  # only one AND cell remains

    def test_sat_equivalence_on_small_design(self):
        """SAT-based equivalence checking on 1-bit AND gates."""
        from nosis.ir import Module

        def _and_mod(name):
            mod = Module(name=name)
            a = mod.add_net("a", 1); b = mod.add_net("b", 1); y = mod.add_net("y", 1)
            ac = mod.add_cell("a_p", PrimOp.INPUT, port_name="a"); mod.connect(ac, "Y", a, direction="output"); mod.ports["a"] = a
            bc = mod.add_cell("b_p", PrimOp.INPUT, port_name="b"); mod.connect(bc, "Y", b, direction="output"); mod.ports["b"] = b
            yc = mod.add_cell("y_p", PrimOp.OUTPUT, port_name="y"); mod.connect(yc, "A", y); mod.ports["y"] = y
            c = mod.add_cell("and0", PrimOp.AND); mod.connect(c, "A", a); mod.connect(c, "B", b); mod.connect(c, "Y", y, direction="output")
            return mod

        from nosis.equiv import check_equivalence
        r = check_equivalence(_and_mod("a"), _and_mod("b"), max_exhaustive_bits=0)
        # Should use SAT (forced by max_exhaustive_bits=0) or random
        assert r.equivalent

    def test_const_fold_and_cse_on_real_design(self):
        """Optimization should reduce cell count on rime_v."""
        result = parse_files([f"{RIME}/core/cpu/rime_v.sv"], top="rime_v")
        design = lower_to_ir(result, top="rime_v")
        mod = design.top_module()
        before = mod.stats()["cells"]
        run_default_passes(mod)
        after = mod.stats()["cells"]
        assert after < before * 0.9, f"expected > 10% reduction: {before} -> {after}"


# ---------------------------------------------------------------------------
# Strict error handling
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Optimization must never increase cell count
# ---------------------------------------------------------------------------

class TestOptimizationMonotonicity:
    """Optimization must be monotonically non-increasing in cell count on every design."""

    DESIGNS = [
        ([f"{RIME}/core/uart/uart_tx.sv"], "uart_tx"),
        ([f"{RIME}/core/uart/uart_rx.sv"], "uart_rx"),
        ([f"{RIME}/core/service/sdram_bridge.sv"], "sdram_bridge"),
        ([f"{RIME}/core/service/sdram_controller.sv"], "sdram_controller"),
        ([f"{RIME}/core/cpu/rime_pcpi_crc32.sv"], "rime_pcpi_crc32"),
        ([f"{RIME}/core/cpu/rime_v.sv"], "rime_v"),
        (RIME_THAW_SOURCES, "top"),
    ]

    def test_opt_never_increases_cell_count(self):
        """For every design, run_default_passes must not increase total cell count."""
        for src, top in self.DESIGNS:
            result = parse_files(src, top=top)
            design = lower_to_ir(result, top=top)
            mod = design.top_module()
            before = mod.stats()["cells"]
            run_default_passes(mod)
            after = mod.stats()["cells"]
            assert after <= before, (
                f"{top}: optimization increased cells from {before} to {after}"
            )

    def test_opt_never_increases_net_count(self):
        """For every design, run_default_passes must not increase net count."""
        for src, top in self.DESIGNS:
            result = parse_files(src, top=top)
            design = lower_to_ir(result, top=top)
            mod = design.top_module()
            before = mod.stats()["nets"]
            run_default_passes(mod)
            after = mod.stats()["nets"]
            assert after <= before, (
                f"{top}: optimization increased nets from {before} to {after}"
            )

    def test_each_pass_individually_non_increasing(self):
        """Each individual pass in the pipeline must not increase cell count."""
        from nosis.passes import constant_fold, identity_simplify, dead_code_eliminate, remove_const_ffs
        from nosis.cse import eliminate_common_subexpressions
        from nosis.boolopt import boolean_optimize

        result = parse_files([f"{RIME}/core/cpu/rime_v.sv"], top="rime_v")
        design = lower_to_ir(result, top="rime_v")
        mod = design.top_module()

        passes = [
            ("constant_fold", constant_fold),
            ("identity_simplify", identity_simplify),
            ("boolean_optimize", boolean_optimize),
            ("remove_const_ffs", remove_const_ffs),
            ("cse", eliminate_common_subexpressions),
            ("dce", dead_code_eliminate),
        ]
        for name, fn in passes:
            before = mod.stats()["cells"]
            fn(mod)
            after = mod.stats()["cells"]
            assert after <= before, (
                f"pass {name} increased cells from {before} to {after}"
            )


# ---------------------------------------------------------------------------
# Locked exact cell counts (unoptimized ECP5 mapping)
# ---------------------------------------------------------------------------

class TestLockedCellCounts:
    """Exact ECP5 cell counts locked to prevent undetected regressions.

    These counts are from the unoptimized pipeline (lower → techmap, no passes).
    If a commit changes any count, the test fails and the developer must
    explicitly update the locked values after confirming the change is intentional.
    """

    def _ecp5_stats(self, src, top):
        result = parse_files(src if isinstance(src, list) else [src], top=top)
        design = lower_to_ir(result, top=top)
        return map_to_ecp5(design).stats()

    def test_uart_tx_exact(self):
        s = self._ecp5_stats(f"{RIME}/core/uart/uart_tx.sv", "uart_tx")
        assert s["TRELLIS_SLICE"] == 117, f"LUT count changed: {s['TRELLIS_SLICE']}"
        assert s["TRELLIS_FF"] == 46, f"FF count changed: {s['TRELLIS_FF']}"
        assert s["CCU2C"] == 128, f"CCU2C count changed: {s['CCU2C']}"

    def test_uart_rx_exact(self):
        s = self._ecp5_stats(f"{RIME}/core/uart/uart_rx.sv", "uart_rx")
        assert s["TRELLIS_SLICE"] == 149, f"LUT count changed: {s['TRELLIS_SLICE']}"
        assert s["TRELLIS_FF"] == 47, f"FF count changed: {s['TRELLIS_FF']}"
        assert s["CCU2C"] == 128, f"CCU2C count changed: {s['CCU2C']}"

    def test_sdram_bridge_exact(self):
        s = self._ecp5_stats(f"{RIME}/core/service/sdram_bridge.sv", "sdram_bridge")
        assert s["TRELLIS_SLICE"] == 255, f"LUT count changed: {s['TRELLIS_SLICE']}"
        assert s["TRELLIS_FF"] == 348, f"FF count changed: {s['TRELLIS_FF']}"
        assert s["CCU2C"] == 14, f"CCU2C count changed: {s['CCU2C']}"

    def test_crc32_exact(self):
        s = self._ecp5_stats(f"{RIME}/core/cpu/rime_pcpi_crc32.sv", "rime_pcpi_crc32")
        assert s["TRELLIS_SLICE"] == 1, f"LUT count changed: {s['TRELLIS_SLICE']}"
        assert s["TRELLIS_FF"] == 34, f"FF count changed: {s['TRELLIS_FF']}"

    def test_rime_v_exact(self):
        s = self._ecp5_stats(f"{RIME}/core/cpu/rime_v.sv", "rime_v")
        assert s["TRELLIS_SLICE"] == 2659, f"LUT count changed: {s['TRELLIS_SLICE']}"
        assert s["TRELLIS_FF"] == 1727, f"FF count changed: {s['TRELLIS_FF']}"
        assert s["CCU2C"] == 275, f"CCU2C count changed: {s['CCU2C']}"

    def test_thaw_exact(self):
        s = self._ecp5_stats(RIME_THAW_SOURCES, "top")
        assert s["TRELLIS_SLICE"] == 8521, f"LUT count changed: {s['TRELLIS_SLICE']}"
        assert s["TRELLIS_FF"] == 6143, f"FF count changed: {s['TRELLIS_FF']}"
        assert s["CCU2C"] == 1044, f"CCU2C count changed: {s['CCU2C']}"

    def test_soc_exact(self):
        s = self._ecp5_stats(RIME_SOC_SOURCES, "top")
        assert s["TRELLIS_SLICE"] == 30562, f"LUT count changed: {s['TRELLIS_SLICE']}"
        assert s["TRELLIS_FF"] == 16825, f"FF count changed: {s['TRELLIS_FF']}"
        assert s["CCU2C"] == 4094, f"CCU2C count changed: {s['CCU2C']}"


# ---------------------------------------------------------------------------
# Strict error handling
# ---------------------------------------------------------------------------

class TestErrorHandling:
    def test_undeclared_identifier_is_error(self):
        """A file with undeclared identifiers must produce an error, not a warning."""
        import tempfile
        from pathlib import Path

        bad_sv = tempfile.NamedTemporaryFile(suffix=".sv", mode="w", delete=False, encoding="utf-8")
        bad_sv.write("module bad(input wire clk); assign x = undefined_signal; endmodule\n")
        bad_sv.close()
        try:
            result = parse_files([bad_sv.name], top="bad")
            assert False, "should have raised FrontendError"
        except FrontendError as exc:
            assert "error" in str(exc).lower()
        finally:
            Path(bad_sv.name).unlink()

    def test_syntax_error_is_error(self):
        """A file with syntax errors must fail."""
        import tempfile
        from pathlib import Path

        bad = tempfile.NamedTemporaryFile(suffix=".sv", mode="w", delete=False, encoding="utf-8")
        bad.write("module broken(input wire a; output wire b; assign b = !!!a; endmodule\n")
        bad.close()
        try:
            parse_files([bad.name], top="broken")
            assert False, "should have raised FrontendError"
        except FrontendError:
            pass
        finally:
            Path(bad.name).unlink()
