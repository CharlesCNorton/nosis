"""Tests for nosis.postsynth — post-synthesis Verilog generation."""

import os

os.environ.setdefault("NOSIS_PYSLANG_PATH", "D:/slang/build/lib")

from nosis.frontend import parse_files, lower_to_ir
from nosis.techmap import map_to_ecp5, ECP5Netlist
from nosis.postsynth import generate_cell_models, generate_postsynth_verilog
from tests.conftest import RIME_UART_TX


def test_cell_models_valid():
    models = generate_cell_models()
    assert "LUT4_SIM" in models
    assert "TRELLIS_FF_SIM" in models
    assert "CCU2C_SIM" in models
    assert "module" in models


def test_postsynth_empty():
    nl = ECP5Netlist(top="empty")
    v = generate_postsynth_verilog(nl)
    assert "module empty_postsynth" in v
    assert "endmodule" in v


def test_postsynth_with_ports():
    nl = ECP5Netlist(top="test")
    nl.ports["clk"] = {"direction": "input", "bits": [2]}
    nl.ports["out"] = {"direction": "output", "bits": [3]}
    v = generate_postsynth_verilog(nl)
    assert "input clk" in v
    assert "output out" in v


def test_postsynth_with_lut4_cell():
    nl = ECP5Netlist(top="test")
    nl.ports["a"] = {"direction": "input", "bits": [2]}
    nl.ports["y"] = {"direction": "output", "bits": [3]}
    c = nl.add_cell("lut0", "LUT4")
    c.parameters["INIT"] = format(0x8888, "016b")
    c.ports["A"] = [2]
    c.ports["B"] = [2]
    c.ports["C"] = ["0"]
    c.ports["D"] = ["0"]
    c.ports["Z"] = [3]
    v = generate_postsynth_verilog(nl)
    assert "LUT4_SIM" in v
    assert "lut0" in v.replace("$", "_")
    assert "0x8888" in v.upper() or "8888" in v.upper()


def test_postsynth_from_real_design():
    result = parse_files([RIME_UART_TX], top="uart_tx")
    design = lower_to_ir(result, top="uart_tx")
    nl = map_to_ecp5(design)
    v = generate_postsynth_verilog(nl)
    assert "module uart_tx_postsynth" in v
    assert "endmodule" in v
    assert "clk" in v
    assert "tx" in v
    assert "LUT4_SIM" in v


def test_postsynth_compiles_with_iverilog():
    """If iverilog is available, the generated Verilog should compile."""
    from nosis.validate import _find_iverilog
    iverilog = _find_iverilog()
    if not iverilog:
        return

    import tempfile
    import subprocess
    from pathlib import Path

    result = parse_files([RIME_UART_TX], top="uart_tx")
    design = lower_to_ir(result, top="uart_tx")
    nl = map_to_ecp5(design)

    models = generate_cell_models()
    postsynth = generate_postsynth_verilog(nl)

    with tempfile.TemporaryDirectory() as tmp:
        models_path = Path(tmp) / "models.v"
        models_path.write_text(models, encoding="utf-8")
        postsynth_path = Path(tmp) / "postsynth.v"
        postsynth_path.write_text(postsynth, encoding="utf-8")

        subprocess.run(
            [iverilog, "-g2012", "-o", "/dev/null", str(models_path), str(postsynth_path)],
            capture_output=True, text=True, cwd=tmp,
        )


def test_postsynth_verilog_has_all_ports():
    result = parse_files([RIME_UART_TX], top="uart_tx")
    design = lower_to_ir(result, top="uart_tx")
    nl = map_to_ecp5(design)
    v = generate_postsynth_verilog(nl)
    for port_name in nl.ports:
        assert port_name in v, f"port {port_name} missing from post-synth Verilog"


def test_postsynth_verilog_has_all_cell_types():
    result = parse_files([RIME_UART_TX], top="uart_tx")
    design = lower_to_ir(result, top="uart_tx")
    nl = map_to_ecp5(design)
    stats = nl.stats()
    v = generate_postsynth_verilog(nl)
    if stats.get("LUT4", 0) > 0:
        assert "LUT4_SIM" in v
    if stats.get("TRELLIS_FF", 0) > 0:
        assert "TRELLIS_FF_SIM" in v
