"""Tests for nosis.cli — CLI entry point."""

import json
import os
import tempfile
from pathlib import Path

os.environ.setdefault("NOSIS_PYSLANG_PATH", "D:/slang/build/lib")

from nosis.cli import main
from tests.conftest import RIME_UART_TX, requires_rime


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
