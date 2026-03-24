"""Tests for yosys comparison — compare nosis synthesis against yosys output.

These tests require yosys to be installed. They synthesize the same design
with both nosis and yosys, then compare cell counts and structure.

Skipped gracefully when yosys is not available.
"""

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

os.environ.setdefault("NOSIS_PYSLANG_PATH", "D:/slang/build/lib")

from nosis.frontend import parse_files, lower_to_ir
from nosis.passes import run_default_passes
from nosis.techmap import map_to_ecp5
from nosis.resources import calculate_area
from tests.conftest import RIME_UART_TX, RIME_V, requires_rime


def _find_yosys() -> str | None:
    found = shutil.which("yosys")
    if found:
        return found
    for env_var in ("ICEPI_OSS_CAD_BIN", "OSS_CAD_BIN"):
        path = os.environ.get(env_var)
        if path:
            candidate = Path(path) / ("yosys.exe" if os.name == "nt" else "yosys")
            if candidate.exists():
                return str(candidate)
    for env_var in ("ICEPI_OSS_CAD_ROOT", "OSS_CAD_ROOT"):
        path = os.environ.get(env_var)
        if path:
            candidate = Path(path) / "bin" / ("yosys.exe" if os.name == "nt" else "yosys")
            if candidate.exists():
                return str(candidate)
    return None


def _yosys_synth_ecp5(yosys: str, source: str, top: str) -> dict[str, int] | None:
    """Run yosys synthesis and return cell type counts."""
    with tempfile.TemporaryDirectory() as tmp:
        json_out = Path(tmp) / "stats.json"
        script = f"""
read_verilog -sv {source}
synth_ecp5 -top {top} -json {json_out}
"""
        script_path = Path(tmp) / "synth.ys"
        script_path.write_text(script, encoding="utf-8")

        r = subprocess.run(
            [yosys, "-s", str(script_path)],
            capture_output=True, text=True, timeout=120,
            cwd=tmp,
        )
        if r.returncode != 0:
            return None

        if not json_out.exists():
            return None

        data = json.loads(json_out.read_text(encoding="utf-8"))
        # Count cell types from yosys JSON
        counts: dict[str, int] = {}
        for mod_data in data.get("modules", {}).values():
            for cell_data in mod_data.get("cells", {}).values():
                cell_type = cell_data.get("type", "unknown")
                counts[cell_type] = counts.get(cell_type, 0) + 1
        return counts


def test_uart_tx_comparison():
    """Compare nosis and yosys synthesis of uart_tx."""
    yosys = _find_yosys()
    if not yosys:
        return  # skip

    # Nosis synthesis
    result = parse_files([RIME_UART_TX], top="uart_tx")
    design = lower_to_ir(result, top="uart_tx")
    nl = map_to_ecp5(design)
    nosis_area = calculate_area(nl)

    # Yosys synthesis
    yosys_counts = _yosys_synth_ecp5(yosys, RIME_UART_TX, "uart_tx")
    if yosys_counts is None:
        return  # yosys failed

    yosys_luts = yosys_counts.get("LUT4", 0) + yosys_counts.get("TRELLIS_SLICE", 0)
    yosys_ffs = yosys_counts.get("TRELLIS_FF", 0)

    print(f"uart_tx comparison:")
    print(f"  nosis: {nosis_area.lut_cells} LUTs, {nosis_area.ff_cells} FFs")
    print(f"  yosys: {yosys_luts} LUTs, {yosys_ffs} FFs")
    if yosys_luts > 0:
        ratio = nosis_area.lut_cells / yosys_luts
        print(f"  ratio: {ratio:.1f}x")
        # Assert ratio bounds — nosis should not be more than 20x yosys
        assert nosis_area.lut_cells > 0
        assert nosis_area.ff_cells > 0
        assert ratio < 20.0, f"LUT ratio {ratio:.1f}x exceeds 20x bound"


def test_rime_v_comparison():
    """Compare nosis and yosys synthesis of rime_v."""
    yosys = _find_yosys()
    if not yosys:
        return

    result = parse_files([RIME_V], top="rime_v")
    design = lower_to_ir(result, top="rime_v")
    nl = map_to_ecp5(design)
    nosis_area = calculate_area(nl)

    yosys_counts = _yosys_synth_ecp5(yosys, RIME_V, "rime_v")
    if yosys_counts is None:
        return

    yosys_luts = yosys_counts.get("LUT4", 0) + yosys_counts.get("TRELLIS_SLICE", 0)

    print(f"rime_v comparison:")
    print(f"  nosis: {nosis_area.lut_cells} LUTs")
    print(f"  yosys: {yosys_luts} LUTs")
    if yosys_luts > 0:
        ratio = nosis_area.lut_cells / yosys_luts
        print(f"  ratio: {ratio:.1f}x")
        assert ratio < 20.0, f"rime_v LUT ratio {ratio:.1f}x exceeds 20x bound"
