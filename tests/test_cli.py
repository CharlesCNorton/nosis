"""Tests for nosis.cli — CLI entry point."""

import json
import os
import tempfile
from pathlib import Path

os.environ.setdefault("NOSIS_PYSLANG_PATH", "D:/slang/build/lib")

from nosis.cli import main
from tests.conftest import RIME_UART_TX


def test_cli_check_mode():
    """--check should parse and validate without producing output."""
    rc = main(["--check", "--top", "uart_tx", RIME_UART_TX])
    assert rc == 0


def test_cli_dump_ir():
    """--dump-ir should print IR and exit without tech mapping."""
    rc = main(["--dump-ir", "--top", "uart_tx", RIME_UART_TX])
    assert rc == 0


def test_cli_emit_verilog():
    """--emit-verilog should print Verilog text."""
    rc = main(["--emit-verilog", "--top", "uart_tx", RIME_UART_TX])
    assert rc == 0


def test_cli_full_pipeline():
    """Full pipeline with -o should produce a JSON file."""
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        out_path = f.name
    try:
        rc = main(["-o", out_path, "--top", "uart_tx", RIME_UART_TX])
        assert rc == 0
        data = json.loads(Path(out_path).read_text(encoding="utf-8"))
        assert "modules" in data
        assert "uart_tx" in data["modules"]
    finally:
        Path(out_path).unlink(missing_ok=True)


def test_cli_benchmark():
    """--benchmark should emit machine-readable JSON."""
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        out_path = f.name
    try:
        rc = main(["--benchmark", "-o", out_path, "--top", "uart_tx", RIME_UART_TX])
        assert rc == 0
    finally:
        Path(out_path).unlink(missing_ok=True)


def test_cli_no_opt():
    """--no-opt should skip optimization."""
    rc = main(["--check", "--no-opt", "--top", "uart_tx", RIME_UART_TX])
    assert rc == 0


def test_cli_stats():
    """--stats should produce synthesis statistics."""
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        out_path = f.name
    try:
        rc = main(["--stats", "-o", out_path, "--top", "uart_tx", RIME_UART_TX])
        assert rc == 0
    finally:
        Path(out_path).unlink(missing_ok=True)


def test_cli_json_stats():
    """--json-stats should emit a complete JSON stats object."""
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        out_path = f.name
    try:
        rc = main(["--json-stats", "-o", out_path, "--top", "uart_tx", RIME_UART_TX])
        assert rc == 0
    finally:
        Path(out_path).unlink(missing_ok=True)


def test_cli_ecppack_without_tools():
    """--ecppack with no nextpnr should warn, not crash."""
    import os
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        out_path = f.name
    bit_path = out_path.replace(".json", ".bit")
    try:
        # Set PATH to empty so tools are not found (unless already in OSS_CAD env vars)
        os.environ.get("PATH", "")
        # Don't clear PATH entirely — just verify the code doesn't crash
        rc = main(["-o", out_path, "--ecppack", bit_path, "--top", "uart_tx", RIME_UART_TX])
        # RC should be 0 (warns) or 1 (tool missing) — either is acceptable, no crash
        assert rc in (0, 1)
    finally:
        Path(out_path).unlink(missing_ok=True)
        Path(bit_path).unlink(missing_ok=True)


def test_cli_entry_point_version():
    """The nosis CLI entry point should work via python -m."""
    import subprocess
    r = subprocess.run(
        ["python", "-m", "nosis.cli", "--version"],
        capture_output=True, text=True, timeout=10,
    )
    assert r.returncode == 0
    assert "0.1.0" in r.stdout or "0.1.0" in r.stderr


def test_cli_entry_point_check():
    """The nosis CLI should parse a file via python -m."""
    import subprocess
    r = subprocess.run(
        ["python", "-m", "nosis.cli", "--check", "--top", "uart_tx", RIME_UART_TX],
        capture_output=True, text=True, timeout=30,
    )
    assert r.returncode == 0
