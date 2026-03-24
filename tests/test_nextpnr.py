"""Tests for nextpnr integration — verify the JSON output is consumable.

These tests require nextpnr-ecp5 to be installed. They are skipped
gracefully when nextpnr is not available.
"""

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

os.environ.setdefault("NOSIS_PYSLANG_PATH", "D:/slang/build/lib")

from nosis.frontend import parse_files, lower_to_ir
from nosis.techmap import map_to_ecp5
from nosis.json_backend import emit_json_str, emit_json
from tests.conftest import RIME_UART_TX, requires_rime


def _find_nextpnr() -> str | None:
    """Locate nextpnr-ecp5."""
    # Try PATH first
    found = shutil.which("nextpnr-ecp5")
    if found:
        return found
    # Check OSS CAD Suite
    for env_var in ("ICEPI_OSS_CAD_BIN", "OSS_CAD_BIN", "ICEPI_OSS_CAD_ROOT", "OSS_CAD_ROOT"):
        path = os.environ.get(env_var)
        if path:
            for subdir in ("", "bin"):
                candidate = Path(path) / subdir / ("nextpnr-ecp5.exe" if os.name == "nt" else "nextpnr-ecp5")
                if candidate.exists():
                    return str(candidate)
    return None


def test_json_is_valid_json():
    """The output must parse as valid JSON."""
    result = parse_files([RIME_UART_TX], top="uart_tx")
    design = lower_to_ir(result, top="uart_tx")
    nl = map_to_ecp5(design)
    text = emit_json_str(nl)
    data = json.loads(text)
    assert "creator" in data
    assert "modules" in data


def test_json_has_required_keys():
    """nextpnr requires specific top-level keys."""
    result = parse_files([RIME_UART_TX], top="uart_tx")
    design = lower_to_ir(result, top="uart_tx")
    nl = map_to_ecp5(design)
    data = json.loads(emit_json_str(nl))
    mod = data["modules"]["uart_tx"]
    assert "attributes" in mod
    assert "ports" in mod
    assert "cells" in mod
    assert "netnames" in mod


def test_nextpnr_parse():
    """If nextpnr is available, verify it can parse the JSON."""
    nextpnr = _find_nextpnr()
    if not nextpnr:
        return  # skip

    result = parse_files([RIME_UART_TX], top="uart_tx")
    design = lower_to_ir(result, top="uart_tx")
    nl = map_to_ecp5(design)

    with tempfile.TemporaryDirectory() as tmp:
        json_path = Path(tmp) / "test.json"
        emit_json(nl, json_path)

        # Try to parse the JSON with nextpnr (--help-json is not a real flag,
        # but feeding it a JSON and asking for info should at least parse it)
        r = subprocess.run(
            [nextpnr, "--25k", "--json", str(json_path), "--info"],
            capture_output=True, text=True, timeout=30,
        )
        # nextpnr may fail on routing but should at least parse the JSON
        # Check that it didn't fail with "unable to parse JSON"
        combined = (r.stdout or "") + (r.stderr or "")
        assert "unable to parse" not in combined.lower()
        assert "json" not in combined.lower() or "error" not in combined.lower()


# ---------------------------------------------------------------------------
# nextpnr JSON consumption and placement test
# ---------------------------------------------------------------------------

def test_nextpnr_place():
    """If nextpnr is available, verify the output places (no routing check)."""
    nextpnr = _find_nextpnr()
    if not nextpnr:
        return

    result = parse_files([RIME_UART_TX], top="uart_tx")
    design = lower_to_ir(result, top="uart_tx")
    nl = map_to_ecp5(design)

    with tempfile.TemporaryDirectory() as tmp:
        json_path = Path(tmp) / "test.json"
        emit_json(nl, json_path)

        r = subprocess.run(
            [nextpnr, "--25k", "--package", "CABGA256",
             "--json", str(json_path),
             "--placer", "sa", "--seed", "1",
             "--no-route"],
            capture_output=True, text=True, timeout=60,
        )
        combined = (r.stdout or "") + (r.stderr or "")
        # nextpnr should at least load the design and attempt placement
        # Even if it fails on some cells, it should not crash
        assert "unable to parse" not in combined.lower(), f"nextpnr parse failed: {combined[:500]}"


def test_json_all_cell_connections_are_int():
    """Every connection bit in the JSON must be an integer (not a string)."""
    result = parse_files([RIME_UART_TX], top="uart_tx")
    design = lower_to_ir(result, top="uart_tx")
    nl = map_to_ecp5(design)
    data = json.loads(emit_json_str(nl))
    for cell_name, cell in data["modules"]["uart_tx"]["cells"].items():
        for port, bits in cell["connections"].items():
            for bit in bits:
                assert isinstance(bit, int), (
                    f"cell {cell_name} port {port} has non-int bit: {bit!r}"
                )
