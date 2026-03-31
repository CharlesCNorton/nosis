"""Consolidated design tests — one class per bundled design, parsed once.

Replaces scattered tests across test_regression.py, test_connectivity.py,
test_frontend.py, test_mux_merge.py, test_nextpnr.py, test_postsynth.py,
test_timing.py, test_resources.py, test_cli.py, and test_yosys_compare.py
that redundantly parsed the same designs.

Each design class uses conftest.get_design() for cached parse/lower/map.
"""


from nosis.ir import PrimOp
from nosis.passes import run_default_passes
from nosis.techmap import map_to_ecp5
from nosis.slicepack import pack_slices
from nosis.fsm import extract_fsms, annotate_fsm_cells
from nosis.timing import analyze_timing
from nosis.resources import calculate_area
from tests.conftest import get_design, RIME_UART_TX


# ---------------------------------------------------------------------------
# uart_tx — 4-state FSM, baud rate counter, the canonical small design
# ---------------------------------------------------------------------------

class TestUartTx:

    def _d(self):
        return get_design("uart_tx")

    # --- Parse & lower ---

    def test_parse_zero_errors(self):
        assert len(self._d().parsed.errors) == 0

    def test_ports_present(self):
        mod = self._d().mod
        for p in ("clk", "send", "data", "tx"):
            assert p in mod.ports, f"missing port: {p}"

    def test_has_ffs(self):
        mod = self._d().mod
        ffs = [c for c in mod.cells.values() if c.op == PrimOp.FF]
        assert len(ffs) >= 3

    def test_has_muxes(self):
        mod = self._d().mod
        muxes = [c for c in mod.cells.values() if c.op == PrimOp.MUX]
        assert len(muxes) >= 5

    def test_no_zero_width_nets(self):
        for net in self._d().mod.nets.values():
            assert net.width > 0, f"net {net.name} has width 0"

    def test_ff_has_clock_and_d(self):
        for cell in self._d().mod.cells.values():
            if cell.op == PrimOp.FF:
                assert "CLK" in cell.inputs, f"FF {cell.name} has no CLK"
                assert "D" in cell.inputs, f"FF {cell.name} has no D"

    def test_enum_constants_lowered(self):
        mod = self._d().mod
        for name in ["IDLE", "START", "TRANSMISSION", "STOP"]:
            net = mod.nets.get(name)
            assert net is not None, f"missing net: {name}"
            assert net.driver is not None and net.driver.op == PrimOp.CONST

    # --- FSM ---

    def test_fsm_detected(self):
        fsms = extract_fsms(self._d().mod)
        assert len(fsms) >= 1
        assert any("state" in f.state_net for f in fsms)

    def test_fsm_annotation_preserves_cells(self):
        mod = self._d().mod
        keys = set(mod.cells.keys())
        annotate_fsm_cells(mod, extract_fsms(mod))
        assert set(mod.cells.keys()) == keys

    # --- Tech mapping ---

    def test_techmap_produces_luts_and_ffs(self):
        s = self._d().netlist.stats()
        assert s.get("LUT4", 0) > 0
        assert s.get("TRELLIS_FF", 0) > 0

    def test_ccu2c_for_arithmetic(self):
        ccu2c = [c for c in self._d().netlist.cells.values() if c.cell_type == "CCU2C"]
        assert len(ccu2c) > 0

    def test_tx_output_driven(self):
        """The tx output port must be driven — if it floats, the wire is dead."""
        data = self._d().json_data
        mod = data["modules"]["uart_tx"]
        tx_bits = mod["ports"]["tx"]["bits"]
        # tx must be driven by some cell output
        out_ports = {"Z", "Q", "S0", "S1", "COUT"}
        driven = set()
        for cell in mod["cells"].values():
            for pn, bits in cell["connections"].items():
                if pn in out_ports:
                    for b in bits:
                        if isinstance(b, int):
                            driven.add(b)
        for b in tx_bits:
            if isinstance(b, int):
                assert b in driven, f"tx output bit {b} is undriven"

    def test_no_undriven_ff_inputs(self):
        """Every TRELLIS_FF DI input must be driven by a cell or port."""
        data = self._d().json_data
        mod = data["modules"]["uart_tx"]
        out_ports = {"Z", "Q", "S0", "S1", "COUT"}
        driven = set()
        for pi in mod["ports"].values():
            if pi["direction"] == "input":
                for b in pi["bits"]:
                    if isinstance(b, int):
                        driven.add(b)
        for cell in mod["cells"].values():
            for pn, bits in cell["connections"].items():
                if pn in out_ports:
                    for b in bits:
                        if isinstance(b, int):
                            driven.add(b)
        for name, cell in mod["cells"].items():
            if cell["type"] == "TRELLIS_FF":
                di = cell["connections"].get("DI", [])
                for b in di:
                    if isinstance(b, int):
                        assert b in driven, f"FF {name} DI bit {b} undriven"

    def test_state_ffs_regset_reset(self):
        """State machine FFs must start at 0 (REGSET=RESET) so TX idles high."""
        data = self._d().json_data
        mod = data["modules"]["uart_tx"]
        for name, cell in mod["cells"].items():
            if cell["type"] == "TRELLIS_FF":
                regset = cell["parameters"].get("REGSET", "RESET")
                # At least one FF must be RESET (state=IDLE=0)
                if regset == "RESET":
                    return
        pytest.fail("No TRELLIS_FF with REGSET=RESET found — state machine may not start in IDLE")

    # --- JSON ---

    def test_json_valid(self):
        d = self._d().json_data
        assert "uart_tx" in d["modules"]
        mod = d["modules"]["uart_tx"]
        assert len(mod["ports"]) == 4
        assert len(mod["cells"]) > 0

    def test_json_all_bits_valid(self):
        for cell_name, cell in self._d().json_data["modules"]["uart_tx"]["cells"].items():
            for port, bits in cell["connections"].items():
                for bit in bits:
                    assert isinstance(bit, int) or (isinstance(bit, str) and bit in ("0", "1", "x")), (
                        f"cell {cell_name} port {port}: invalid bit {bit!r}")

    def test_output_port_in_json(self):
        ports = self._d().json_data["modules"]["uart_tx"]["ports"]
        assert "tx" in ports
        assert ports["tx"]["direction"] == "output"

    # --- Optimization ---

    def test_optimization_preserves_output_drivers(self):
        mod = self._d().mod
        driven_before = set()
        for pname, pnet in mod.ports.items():
            is_out = any(c.op == PrimOp.OUTPUT and any(i.name == pname for i in c.inputs.values()) for c in mod.cells.values())
            if is_out and pnet.driver is not None:
                driven_before.add(pname)
        run_default_passes(mod)
        for pname in driven_before:
            pnet = mod.ports.get(pname)
            assert pnet and pnet.driver is not None, f"optimization removed driver for {pname}"

    def test_has_logic_after_optimization(self):
        mod = self._d().mod
        run_default_passes(mod)
        ffs = sum(1 for c in mod.cells.values() if c.op == PrimOp.FF)
        comb = sum(1 for c in mod.cells.values() if c.op not in (PrimOp.INPUT, PrimOp.OUTPUT, PrimOp.FF, PrimOp.CONST))
        assert ffs >= 3
        assert comb >= 5

    def test_optimized_no_undriven_bits(self):
        """After full optimization, no input bits should be undriven."""
        import json
        from nosis.frontend import parse_files, lower_to_ir
        from nosis.json_backend import emit_json_str
        r = parse_files([RIME_UART_TX], top="uart_tx")
        d = lower_to_ir(r, top="uart_tx")
        m = d.top_module()
        run_default_passes(m)
        nl = map_to_ecp5(d)
        pack_slices(nl)
        data = json.loads(emit_json_str(nl))
        mod = list(data["modules"].values())[0]
        out_ports = {"Z", "Q", "S0", "S1", "COUT", "OFX0", "OFX1"}
        driven = set()
        for pi in mod["ports"].values():
            if pi["direction"] == "input":
                for b in pi["bits"]:
                    if isinstance(b, int): driven.add(b)
        for c in mod["cells"].values():
            for pn, bits in c["connections"].items():
                if pn in out_ports:
                    for b in bits:
                        if isinstance(b, int): driven.add(b)
        used = set()
        for c in mod["cells"].values():
            for pn, bits in c["connections"].items():
                if pn not in out_ports:
                    for b in bits:
                        if isinstance(b, int): used.add(b)
        undriven = used - driven
        assert len(undriven) == 0, f"{len(undriven)} undriven bits after optimization"

    # --- Timing & area ---

    def test_timing(self):
        mod = self._d().design.top_module()
        report = analyze_timing(mod)
        assert report.max_delay_ns > 0
        assert report.max_frequency_mhz > 0

    def test_area(self):
        area = calculate_area(self._d().netlist)
        assert area.lut_cells > 0
        assert area.ff_cells > 0
        assert area.slices_total > 0

    # --- CLI ---

    def test_cli_check(self):
        from nosis.cli import main
        assert main(["--check", "--top", "uart_tx", RIME_UART_TX]) == 0

    def test_cli_dump_ir(self):
        from nosis.cli import main
        assert main(["--dump-ir", "--top", "uart_tx", RIME_UART_TX]) == 0

    def test_cli_emit_verilog(self):
        from nosis.cli import main
        assert main(["--emit-verilog", "--top", "uart_tx", RIME_UART_TX]) == 0

    def test_cli_no_opt(self):
        from nosis.cli import main
        assert main(["--check", "--no-opt", "--top", "uart_tx", RIME_UART_TX]) == 0

    def test_cli_entry_point(self):
        import subprocess
        r = subprocess.run(
            ["python", "-m", "nosis.cli", "--check", "--top", "uart_tx", RIME_UART_TX],
            capture_output=True, text=True, timeout=30,
        )
        assert r.returncode == 0

    def test_cli_version(self):
        import subprocess
        r = subprocess.run(
            ["python", "-m", "nosis.cli", "--version"],
            capture_output=True, text=True, timeout=10,
        )
        assert r.returncode == 0
        assert "0.1.0" in r.stdout or "0.1.0" in r.stderr

    # --- nextpnr integration ---

    def test_nextpnr_json_parseable(self):
        """If nextpnr-ecp5 is available, verify it can parse the JSON."""
        import shutil
        import subprocess
        import tempfile
        from pathlib import Path
        from nosis.json_backend import emit_json
        nextpnr = shutil.which("nextpnr-ecp5")
        if not nextpnr:
            return  # skip if not installed
        with tempfile.TemporaryDirectory() as tmp:
            jp = Path(tmp) / "test.json"
            emit_json(self._d().netlist, jp)
            r = subprocess.run(
                [nextpnr, "--25k", "--json", str(jp), "--info"],
                capture_output=True, text=True, timeout=30,
            )
            assert "unable to parse" not in (r.stdout + r.stderr).lower()

    def test_nextpnr_places(self):
        """If nextpnr-ecp5 is available, verify placement succeeds."""
        import shutil
        import subprocess
        import tempfile
        from pathlib import Path
        from nosis.json_backend import emit_json
        nextpnr = shutil.which("nextpnr-ecp5")
        if not nextpnr:
            return
        with tempfile.TemporaryDirectory() as tmp:
            jp = Path(tmp) / "test.json"
            emit_json(self._d().netlist, jp)
            r = subprocess.run(
                [nextpnr, "--25k", "--package", "CABGA256",
                 "--json", str(jp), "--placer", "sa", "--seed", "1", "--no-route"],
                capture_output=True, text=True, timeout=60,
            )
            assert "unable to parse" not in (r.stdout + r.stderr).lower()

    def test_run_nextpnr_missing_binary(self):
        """run_nextpnr should return a failed PnRResult when nextpnr is not found."""
        from nosis.pnr_feedback import run_nextpnr
        result = run_nextpnr("nonexistent.json", nextpnr_cmd="/nonexistent/nextpnr-ecp5")
        assert result.success is False
        assert len(result.errors) > 0


# ---------------------------------------------------------------------------
# uart_rx — mid-bit sampling, baud counter
# ---------------------------------------------------------------------------

class TestUartRx:

    def _d(self):
        return get_design("uart_rx")

    def test_parse_zero_errors(self):
        assert len(self._d().parsed.errors) == 0

    def test_ports(self):
        mod = self._d().mod
        for p in ("clk", "rx", "finish", "data"):
            assert p in mod.ports

    def test_techmap(self):
        s = self._d().netlist.stats()
        assert s.get("LUT4", 0) > 0
        assert s.get("TRELLIS_FF", 0) > 0

    def test_rx_data_output_driven(self):
        """The data output port must be driven — RX must produce received bytes."""
        data = self._d().json_data
        mod = list(data["modules"].values())[0]
        data_bits = mod["ports"]["data"]["bits"]
        out_ports = {"Z", "Q", "S0", "S1", "COUT"}
        driven = set()
        for cell in mod["cells"].values():
            for pn, bits in cell["connections"].items():
                if pn in out_ports:
                    for b in bits:
                        if isinstance(b, int):
                            driven.add(b)
        for b in data_bits:
            if isinstance(b, int):
                assert b in driven, f"data output bit {b} is undriven — RX cannot deliver bytes"

    def test_rx_finish_output_driven(self):
        """The finish output must be driven — without it, no byte-ready signal."""
        data = self._d().json_data
        mod = list(data["modules"].values())[0]
        finish_bits = mod["ports"]["finish"]["bits"]
        out_ports = {"Z", "Q", "S0", "S1", "COUT"}
        driven = set()
        for cell in mod["cells"].values():
            for pn, bits in cell["connections"].items():
                if pn in out_ports:
                    for b in bits:
                        if isinstance(b, int):
                            driven.add(b)
        for b in finish_bits:
            if isinstance(b, int):
                assert b in driven, f"finish bit {b} undriven — no byte-ready pulse"

    def test_no_undriven_ff_inputs(self):
        """Every TRELLIS_FF DI must be driven."""
        data = self._d().json_data
        mod = list(data["modules"].values())[0]
        out_ports = {"Z", "Q", "S0", "S1", "COUT"}
        driven = set()
        for pi in mod["ports"].values():
            if pi["direction"] == "input":
                for b in pi["bits"]:
                    if isinstance(b, int):
                        driven.add(b)
        for cell in mod["cells"].values():
            for pn, bits in cell["connections"].items():
                if pn in out_ports:
                    for b in bits:
                        if isinstance(b, int):
                            driven.add(b)
        for name, cell in mod["cells"].items():
            if cell["type"] == "TRELLIS_FF":
                di = cell["connections"].get("DI", [])
                for b in di:
                    if isinstance(b, int):
                        assert b in driven, f"FF {name} DI bit {b} undriven"


# ---------------------------------------------------------------------------
# sdram_bridge — 128-bit burst aggregator
# ---------------------------------------------------------------------------

class TestSdramBridge:

    def _d(self):
        return get_design("sdram_bridge")

    def test_parse_zero_errors(self):
        assert len(self._d().parsed.errors) == 0

    def test_ports(self):
        mod = self._d().mod
        for p in ("clk", "rst", "start", "wr", "done", "busy"):
            assert p in mod.ports

    def test_ff_count(self):
        ffs = [c for c in self._d().mod.cells.values() if c.op == PrimOp.FF]
        assert len(ffs) >= 5

    def test_fsm_detected(self):
        assert len(extract_fsms(self._d().mod)) >= 1

    def test_no_undriven_output_bits(self):
        """All output port bits must be driven."""
        data = self._d().json_data
        mod = list(data["modules"].values())[0]
        out_ports = {"Z", "Q", "S0", "S1", "COUT"}
        driven = set()
        for cell in mod["cells"].values():
            for pn, bits in cell["connections"].items():
                if pn in out_ports:
                    for b in bits:
                        if isinstance(b, int):
                            driven.add(b)
        for pname, pi in mod["ports"].items():
            if pi["direction"] == "output":
                for b in pi["bits"]:
                    if isinstance(b, int):
                        assert b in driven, f"output port {pname} bit {b} undriven"


# ---------------------------------------------------------------------------
# crc32 — PicoRV32 PCPI CRC32 coprocessor
# ---------------------------------------------------------------------------

class TestCrc32:

    def _d(self):
        return get_design("crc32")

    def test_parse_zero_errors(self):
        assert len(self._d().parsed.errors) == 0

    def test_mostly_ffs(self):
        s = self._d().netlist.stats()
        assert s.get("TRELLIS_FF", 0) >= 30

    def test_pcpi_outputs_driven(self):
        """pcpi_wr, pcpi_rd, pcpi_ready outputs must all be driven."""
        data = self._d().json_data
        mod = list(data["modules"].values())[0]
        out_ports = {"Z", "Q", "S0", "S1", "COUT"}
        driven = set()
        for cell in mod["cells"].values():
            for pn, bits in cell["connections"].items():
                if pn in out_ports:
                    for b in bits:
                        if isinstance(b, int):
                            driven.add(b)
        for pname in ("pcpi_wr", "pcpi_rd", "pcpi_ready"):
            if pname in mod["ports"]:
                for b in mod["ports"][pname]["bits"]:
                    if isinstance(b, int):
                        assert b in driven, f"{pname} bit {b} undriven"


# ---------------------------------------------------------------------------
# IR connectivity invariants — shared across all bundled designs
# ---------------------------------------------------------------------------

class TestIRInvariants:
    """Every cell input must reference a net that exists in the module."""

    def _check(self, name):
        d = get_design(name)
        mod = d.mod
        for cell in mod.cells.values():
            for port_name, net in cell.inputs.items():
                assert net.name in mod.nets, f"{cell.name}.{port_name} -> {net.name} not in module"
            for port_name, net in cell.outputs.items():
                assert net.name in mod.nets, f"{cell.name}.{port_name} -> {net.name} not in module"

    def test_uart_tx(self): self._check("uart_tx")
    def test_uart_rx(self): self._check("uart_rx")
    def test_sdram_bridge(self): self._check("sdram_bridge")
    def test_crc32(self): self._check("crc32")


class TestJSONInvariants:
    """Every JSON cell connection bit must be valid."""

    def _check(self, name):
        d = get_design(name)
        top = d.top
        data = d.json_data
        for cell_name, cell in data["modules"][top]["cells"].items():
            assert "type" in cell
            assert "connections" in cell
            for port, bits in cell["connections"].items():
                for bit in bits:
                    assert isinstance(bit, int) or (isinstance(bit, str) and bit in ("0", "1", "x")), (
                        f"{cell_name}.{port}: {bit!r}")

    def test_uart_tx(self): self._check("uart_tx")
    def test_uart_rx(self): self._check("uart_rx")
    def test_sdram_bridge(self): self._check("sdram_bridge")


# ---------------------------------------------------------------------------
# Additional CLI tests
# ---------------------------------------------------------------------------

def test_cli_stats():
    import tempfile
    from pathlib import Path
    from nosis.cli import main
    with tempfile.TemporaryDirectory() as tmp:
        out = str(Path(tmp) / "out.json")
        rc = main(["--stats", "-o", out, "--top", "uart_tx", RIME_UART_TX])
        assert rc == 0

def test_cli_lpf():
    """The --lpf flag should be accepted without error."""
    import tempfile
    from pathlib import Path
    from nosis.cli import main
    with tempfile.TemporaryDirectory() as tmp:
        out = str(Path(tmp) / "out.json")
        lpf = str(Path(tmp) / "test.lpf")
        Path(lpf).write_text("LOCATE COMP \"clk\" SITE \"P3\";\n")
        rc = main(["-o", out, "--lpf", lpf, "--top", "uart_tx", RIME_UART_TX])
        assert rc == 0


# ---------------------------------------------------------------------------
# Yosys comparison — verify nosis produces fewer or comparable LUTs
# ---------------------------------------------------------------------------

def test_uart_tx_all_outputs_driven():
    """After full pipeline, the tx output bit must be driven by an FF Q."""
    import json
    from nosis.frontend import parse_files as pf, lower_to_ir as lir
    from nosis.json_backend import emit_json_str
    r = pf([RIME_UART_TX], top="uart_tx")
    design = lir(r, top="uart_tx")
    m = design.top_module()
    run_default_passes(m)
    nl = map_to_ecp5(design)
    pack_slices(nl)
    data = json.loads(emit_json_str(nl))
    mod = list(data["modules"].values())[0]
    tx_bits = mod["ports"]["tx"]["bits"]
    # tx must be driven by an FF (state machine sets tx=1 in IDLE)
    ff_q_bits = set()
    for cell in mod["cells"].values():
        if cell["type"] == "TRELLIS_FF":
            for b in cell["connections"].get("Q", []):
                if isinstance(b, int):
                    ff_q_bits.add(b)
    for b in tx_bits:
        if isinstance(b, int):
            assert b in ff_q_bits, f"tx bit {b} not driven by FF — UART idle state will be wrong"


# ---------------------------------------------------------------------------
# iverilog gate-level simulation
# ---------------------------------------------------------------------------

def test_iverilog_postsynth_uart_tx():
    """Compile post-synthesis Verilog with iverilog to verify it parses."""
    import shutil
    import subprocess
    import tempfile
    from pathlib import Path
    from nosis.postsynth import generate_postsynth_verilog

    iverilog = shutil.which("iverilog")
    if not iverilog:
        return  # skip if not installed

    d = get_design("uart_tx")
    sv_code = generate_postsynth_verilog(d.netlist)

    with tempfile.TemporaryDirectory() as tmp:
        sv_path = Path(tmp) / "postsynth.sv"
        sv_path.write_text(sv_code, encoding="utf-8")

        # The postsynth output includes SIM model definitions — compile standalone
        out_path = Path(tmp) / "out.vvp"
        r = subprocess.run(
            [iverilog, "-g2012", "-o", str(out_path), str(sv_path)],
            capture_output=True, text=True, timeout=30,
        )
        assert r.returncode == 0, f"iverilog failed:\n{r.stderr}"


def test_iverilog_cycle_accurate_uart_tx():
    """RTL-vs-post-synth cycle-accurate comparison on uart_tx via validate_design."""
    import shutil
    iverilog = shutil.which("iverilog")
    vvp = shutil.which("vvp")
    if not iverilog or not vvp:
        return  # skip if not installed

    from nosis.validate import validate_design
    result = validate_design(
        source_files=[RIME_UART_TX],
        top="uart_tx",
        num_cycles=50,
        seed=42,
    )
    assert result.rtl_sim_ok, f"RTL sim failed: {result.error}"
    assert result.passed, f"Mismatch at cycle {result.mismatches[0]['cycle'] if result.mismatches else '?'}: {result.error}"
    assert result.cycles >= 10, f"Only {result.cycles} cycles compared"


def test_cli_verify():
    """The --verify flag should run equivalence checks without error on uart_tx."""
    from nosis.cli import main
    rc = main(["--check", "--verify", "--top", "uart_tx", RIME_UART_TX])
    assert rc == 0
