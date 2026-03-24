"""Regression tests — increasingly strict synthesis validation against real RIME HDL.

These tests verify that nosis can parse, lower, optimize, map, and emit
valid nextpnr JSON for real designs. Each test asserts specific cell counts,
port presence, and structural properties. If a pipeline change breaks any
of these, the commit is rejected.
"""

import json
import os

os.environ.setdefault("NOSIS_PYSLANG_PATH", "D:/slang/build/lib")

from nosis.frontend import FrontendError, parse_files, lower_to_ir
from nosis.passes import run_default_passes
from nosis.fsm import extract_fsms, annotate_fsm_cells
from nosis.techmap import map_to_ecp5
from nosis.json_backend import emit_json_str
from nosis.ir import PrimOp

RIME = "D:/rime/firmware"


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

    def test_fsm_annotation_preserves_cells(self):
        _, mod = self._synth(optimize=False)
        cells_before = set(mod.cells.keys())
        fsms = extract_fsms(mod)
        annotate_fsm_cells(mod, fsms)
        # FSM pass must never add or remove cells
        assert set(mod.cells.keys()) == cells_before


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
        assert stats.get("TRELLIS_SLICE", 0) >= 1000
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


# ---------------------------------------------------------------------------
# Multi-file: Thaw (real board image)
# ---------------------------------------------------------------------------

class TestThaw:
    SRC = [
        f"{RIME}/images/thaw/top.sv",
        f"{RIME}/images/thaw/thaw_service.sv",
        f"{RIME}/core/uart/uart_rx.sv",
        f"{RIME}/core/uart/uart_tx.sv",
        f"{RIME}/core/service/flash_spi_master.sv",
        f"{RIME}/core/service/sdram_controller.sv",
        f"{RIME}/core/service/sdram_bridge.sv",
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
        assert stats.get("TRELLIS_SLICE", 0) >= 2000
        assert stats.get("TRELLIS_FF", 0) >= 500

    def test_port_count(self):
        result = parse_files(self.SRC, top=self.TOP)
        design = lower_to_ir(result, top=self.TOP)
        mod = design.top_module()
        # Thaw top has: clk, usb_rx, usb_tx, led[4:0], button[1:0],
        # flash pins, sd pins, sdram pins
        assert len(mod.ports) >= 15

    def test_json_valid_and_complete(self):
        result = parse_files(self.SRC, top=self.TOP)
        design = lower_to_ir(result, top=self.TOP)
        nl = map_to_ecp5(design)
        text = emit_json_str(nl)
        data = json.loads(text)
        assert self.TOP in data["modules"]
        mod_json = data["modules"][self.TOP]
        # Structural checks
        assert len(mod_json["ports"]) >= 15
        assert len(mod_json["cells"]) >= 2000
        assert len(mod_json["netnames"]) >= 100
        # Every cell connection bit must be an integer
        for name, cell in mod_json["cells"].items():
            for port, bits in cell["connections"].items():
                for bit in bits:
                    assert isinstance(bit, int), f"cell {name} port {port} has non-int bit: {bit!r}"


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
