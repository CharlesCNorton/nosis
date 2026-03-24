"""Nosis post-PnR feedback — parse nextpnr logs for timing closure.

Parses nextpnr-ecp5 stderr output to extract:
  - Max frequency per clock domain
  - Critical path net names and delays
  - Slack values

This data can feed back into optimization to prioritize timing-critical nets.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

__all__ = [
    "PnRResult",
    "CriticalPathArc",
    "run_nextpnr",
    "parse_nextpnr_log",
    "extract_critical_nets",
]


@dataclass(slots=True)
class CriticalPathArc:
    """One arc in a nextpnr critical path report."""
    arc_type: str  # "clk-to-q", "routing", "logic", "setup"
    delay_ns: float
    cumulative_ns: float
    source: str
    net: str = ""


@dataclass(slots=True)
class PnRResult:
    """Parsed results from a nextpnr run."""
    success: bool
    max_freq_mhz: float = 0.0
    clock_name: str = ""
    critical_path: list[CriticalPathArc] = field(default_factory=list)
    critical_nets: set[str] = field(default_factory=set)
    total_luts: int = 0
    total_ffs: int = 0
    errors: list[str] = field(default_factory=list)
    raw_log: str = ""


# Primary patterns (nextpnr 0.6/0.7 format)
_FREQ_RE = re.compile(r"Max frequency for clock '([^']+)':\s+([\d.]+)\s+MHz")
_ARC_RE = re.compile(r"Info:\s+(clk-to-q|routing|logic|setup)\s+([\d.]+)\s+([\d.]+)\s+(Source|Net|Sink)\s+(\S+)")
_LUT_RE = re.compile(r"Total LUT4s:\s+(\d+)")
_FF_RE = re.compile(r"Total DFFs:\s+(\d+)")
_NET_RE = re.compile(r"Net\s+(\S+)\s+\(")

# Fallback patterns for format variations across nextpnr versions
_FREQ_RE2 = re.compile(r"Fmax.*?:\s+([\d.]+)\s+MHz.*?clock\s+'([^']+)'")
_LUT_RE2 = re.compile(r"(?:LUT|LUT4|TRELLIS_SLICE).*?(\d+)\s*/\s*\d+")
_FF_RE2 = re.compile(r"(?:DFF|FF|TRELLIS_FF).*?(\d+)\s*/\s*\d+")


def parse_nextpnr_log(log: str) -> PnRResult:
    """Parse nextpnr stderr output into structured data."""
    result = PnRResult(success=True, raw_log=log)

    for line in log.splitlines():
        if "ERROR" in line:
            result.errors.append(line.strip())
            result.success = False

        m = _FREQ_RE.search(line)
        if m:
            freq = float(m.group(2))
            if freq > result.max_freq_mhz:
                result.max_freq_mhz = freq
                result.clock_name = m.group(1)

        m = _LUT_RE.search(line)
        if m:
            result.total_luts = int(m.group(1))

        m = _FF_RE.search(line)
        if m:
            result.total_ffs = int(m.group(1))

        # Extract net names from critical path reports
        m = _NET_RE.search(line)
        if m and "Critical path" not in line:
            net_name = m.group(1)
            # Strip nextpnr suffixes to get the original net name
            base = net_name.split("$")[0] if "$" in net_name else net_name
            if base:
                result.critical_nets.add(base)

        # Parse critical path arcs
        m = _ARC_RE.search(line)
        if m:
            result.critical_path.append(CriticalPathArc(
                arc_type=m.group(1),
                delay_ns=float(m.group(2)),
                cumulative_ns=float(m.group(3)),
                source=m.group(5),
            ))

    # Fallback: try alternate frequency pattern
    if result.max_freq_mhz == 0.0:
        for line in log.splitlines():
            m = _FREQ_RE2.search(line)
            if m:
                result.max_freq_mhz = float(m.group(1))
                result.clock_name = m.group(2)
                break

    # Fallback: try alternate LUT/FF patterns
    if result.total_luts == 0:
        for line in log.splitlines():
            m = _LUT_RE2.search(line)
            if m:
                result.total_luts = int(m.group(1))
                break
    if result.total_ffs == 0:
        for line in log.splitlines():
            m = _FF_RE2.search(line)
            if m:
                result.total_ffs = int(m.group(1))
                break

    if result.errors:
        result.success = False

    return result


def run_nextpnr(
    json_path: str | Path,
    *,
    device: str = "25k",
    package: str = "CABGA256",
    nextpnr_cmd: str | None = None,
    extra_args: list[str] | None = None,
) -> PnRResult:
    """Run nextpnr-ecp5 on a JSON netlist and parse the results.

    Returns a PnRResult with timing, utilization, and critical path data.
    If nextpnr is not found, returns a failed result with an error message.
    """
    import shutil
    import os

    if nextpnr_cmd is None:
        nextpnr_cmd = shutil.which("nextpnr-ecp5")
        if not nextpnr_cmd:
            for env_var in ("ICEPI_OSS_CAD_BIN", "OSS_CAD_BIN"):
                p = os.environ.get(env_var)
                if p:
                    candidate = Path(p) / ("nextpnr-ecp5.exe" if os.name == "nt" else "nextpnr-ecp5")
                    if candidate.exists():
                        nextpnr_cmd = str(candidate)
                        break
            for env_var in ("ICEPI_OSS_CAD_ROOT", "OSS_CAD_ROOT"):
                if nextpnr_cmd:
                    break
                p = os.environ.get(env_var)
                if p:
                    candidate = Path(p) / "bin" / ("nextpnr-ecp5.exe" if os.name == "nt" else "nextpnr-ecp5")
                    if candidate.exists():
                        nextpnr_cmd = str(candidate)

    if not nextpnr_cmd:
        return PnRResult(success=False, errors=["nextpnr-ecp5 not found"])

    cmd = [
        nextpnr_cmd,
        f"--{device}",
        "--package", package,
        "--json", str(json_path),
        *(extra_args or []),
    ]

    env = dict(os.environ)
    tool_dir = str(Path(nextpnr_cmd).parent)
    env["PATH"] = tool_dir + os.pathsep + env.get("PATH", "")
    # Also add lib dir for shared libraries
    lib_dir = str(Path(nextpnr_cmd).parent.parent / "lib")
    if Path(lib_dir).is_dir():
        env["PATH"] = lib_dir + os.pathsep + env["PATH"]

    try:
        r = subprocess.run(
            cmd, capture_output=True, text=True, env=env, timeout=120,
        )
        return parse_nextpnr_log(r.stderr)
    except FileNotFoundError:
        return PnRResult(success=False, errors=["nextpnr-ecp5 not found"])
    except subprocess.TimeoutExpired:
        return PnRResult(success=False, errors=["nextpnr timed out"])


def extract_critical_nets(result: PnRResult) -> set[str]:
    """Extract net names from the critical path for optimization targeting."""
    nets: set[str] = set(result.critical_nets)
    for arc in result.critical_path:
        if arc.net:
            nets.add(arc.net)
        # Extract from source field (e.g., "state_TRELLIS_FF_Q.Q" -> "state")
        src = arc.source
        if src:
            base = src.split("_TRELLIS")[0].split("_LUT4")[0].split("_CCU2C")[0]
            if base:
                nets.add(base)
    return nets
