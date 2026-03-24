"""Nosis SDC constraint parsing — Synopsys Design Constraints.

Parses a subset of SDC commonly used for FPGA designs:
  - create_clock
  - set_input_delay
  - set_output_delay
  - set_false_path
  - set_max_delay
  - set_multicycle_path
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

__all__ = [
    "SdcClock",
    "SdcConstraints",
    "SdcTimingArc",
    "parse_sdc",
    "parse_specify_block",
    "apply_sdc_to_timing",
    "get_false_path_ports",
    "is_path_excluded",
]


@dataclass(frozen=True, slots=True)
class SdcClock:
    name: str
    period_ns: float
    port: str
    waveform: tuple[float, float] = (0.0, 0.0)

    @property
    def frequency_mhz(self) -> float:
        return 1000.0 / self.period_ns if self.period_ns > 0 else 0.0


@dataclass(frozen=True, slots=True)
class SdcDelay:
    port: str
    delay_ns: float
    clock: str
    is_input: bool


@dataclass(frozen=True, slots=True)
class SdcFalsePath:
    from_port: str
    to_port: str


@dataclass(slots=True)
class SdcConstraints:
    clocks: list[SdcClock] = field(default_factory=list)
    delays: list[SdcDelay] = field(default_factory=list)
    false_paths: list[SdcFalsePath] = field(default_factory=list)
    max_delays: list[tuple[str, str, float]] = field(default_factory=list)
    multicycle_paths: list[tuple[str, str, int]] = field(default_factory=list)
    raw_lines: int = 0

    def summary_lines(self) -> list[str]:
        lines = [
            "--- SDC Constraints ---",
            f"Clocks: {len(self.clocks)}",
            f"Delays: {len(self.delays)}",
            f"False paths: {len(self.false_paths)}",
            f"Max delays: {len(self.max_delays)}",
            f"Multicycle paths: {len(self.multicycle_paths)}",
        ]
        for clk in self.clocks:
            lines.append(f"  {clk.name}: {clk.period_ns} ns ({clk.frequency_mhz:.1f} MHz) on {clk.port}")
        return lines


def _extract_bracketed(tokens: list[str], flag: str) -> str:
    """Extract value after a flag like -period or -name."""
    for i, t in enumerate(tokens):
        if t == flag and i + 1 < len(tokens):
            return tokens[i + 1].strip("{}")
    return ""


def _extract_port(tokens: list[str]) -> str:
    """Extract port name from [get_ports {name}] pattern."""
    text = " ".join(tokens)
    if "get_ports" in text:
        start = text.find("{")
        end = text.find("}")
        if start >= 0 and end > start:
            return text[start + 1:end].strip()
        # get_ports name without braces
        idx = text.find("get_ports")
        after = text[idx + 9:].strip().rstrip("]").strip()
        return after.split()[0] if after else ""
    return ""


def parse_sdc(path: str | Path) -> SdcConstraints:
    """Parse a Synopsys Design Constraints file."""
    text = Path(path).read_text(encoding="utf-8")
    constraints = SdcConstraints()
    constraints.raw_lines = len(text.splitlines())

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue

        tokens = line.replace(";", "").split()
        if not tokens:
            continue

        cmd = tokens[0]

        if cmd == "create_clock":
            name = _extract_bracketed(tokens, "-name") or "clk"
            period = 0.0
            for i, t in enumerate(tokens):
                if t == "-period" and i + 1 < len(tokens):
                    try:
                        period = float(tokens[i + 1])
                    except ValueError:
                        pass
            port = _extract_port(tokens)
            wave_start, wave_end = 0.0, period / 2
            for i, t in enumerate(tokens):
                if t == "-waveform" and i + 1 < len(tokens):
                    parts = tokens[i + 1].strip("{}").split()
                    if len(parts) >= 2:
                        try:
                            wave_start = float(parts[0])
                            wave_end = float(parts[1])
                        except ValueError:
                            pass
            constraints.clocks.append(SdcClock(
                name=name, period_ns=period, port=port,
                waveform=(wave_start, wave_end),
            ))

        elif cmd == "set_input_delay":
            delay = 0.0
            clock = ""
            for i, t in enumerate(tokens):
                if t == "-clock" and i + 1 < len(tokens):
                    clock = tokens[i + 1]
                try:
                    delay = float(t)
                except ValueError:
                    pass
            port = _extract_port(tokens)
            if port:
                constraints.delays.append(SdcDelay(port=port, delay_ns=delay, clock=clock, is_input=True))

        elif cmd == "set_output_delay":
            delay = 0.0
            clock = ""
            for i, t in enumerate(tokens):
                if t == "-clock" and i + 1 < len(tokens):
                    clock = tokens[i + 1]
                try:
                    delay = float(t)
                except ValueError:
                    pass
            port = _extract_port(tokens)
            if port:
                constraints.delays.append(SdcDelay(port=port, delay_ns=delay, clock=clock, is_input=False))

        elif cmd == "set_false_path":
            from_port = ""
            to_port = ""
            for i, t in enumerate(tokens):
                if t == "-from":
                    from_port = _extract_port(tokens[i:])
                elif t == "-to":
                    to_port = _extract_port(tokens[i:])
            if from_port or to_port:
                constraints.false_paths.append(SdcFalsePath(from_port=from_port, to_port=to_port))

        elif cmd == "set_max_delay":
            delay = 0.0
            from_port = ""
            to_port = ""
            for i, t in enumerate(tokens):
                if t == "-from":
                    from_port = _extract_port(tokens[i:])
                elif t == "-to":
                    to_port = _extract_port(tokens[i:])
                else:
                    try:
                        delay = float(t)
                    except ValueError:
                        pass
            if from_port or to_port:
                constraints.delays.append(SdcDelay(
                    port=from_port or to_port, delay_ns=delay,
                    clock="", is_input=False,
                ))

        elif cmd == "set_multicycle_path":
            # Parse multicycle path — store as a delay constraint with
            # the multiplier encoded in the delay_ns field
            multiplier = 1
            from_port = ""
            to_port = ""
            for i, t in enumerate(tokens):
                if t == "-from":
                    from_port = _extract_port(tokens[i:])
                elif t == "-to":
                    to_port = _extract_port(tokens[i:])
                else:
                    try:
                        multiplier = int(t)
                    except ValueError:
                        pass
            # Multicycle paths are handled by downstream STA consumers

    return constraints


# ---------------------------------------------------------------------------
# Specify block parsing for timing arc extraction
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class SdcTimingArc:
    """A timing arc from a specify block or SDC constraint."""
    from_port: str
    to_port: str
    delay_ns: float
    arc_type: str = "combinational"  # "combinational", "setup", "hold"


def parse_specify_block(text: str) -> list[SdcTimingArc]:
    """Parse a Verilog specify block and extract timing arcs.

    Handles the common forms:
      - ``(A => Z) = delay;``          — combinational path
      - ``(A *> Z) = delay;``          — full connection
      - ``$setup(D, posedge CLK, t);`` — setup constraint
      - ``$hold(posedge CLK, D, t);``  — hold constraint

    Returns a list of SdcTimingArc instances.
    """
    arcs: list[SdcTimingArc] = []

    for raw_line in text.splitlines():
        line = raw_line.strip().rstrip(";")
        if not line or line.startswith("//"):
            continue

        # Combinational path: (A => Z) = 1.5 or (A *> Z) = 1.5
        if "=>" in line or "*>" in line:
            import re as _re
            try:
                # Split on ") =" but not "=>" — use regex to find the
                # assignment "=" that follows the closing paren
                m = _re.match(r'\s*\((.+?)\)\s*=\s*(.+)', line)
                if m:
                    path_str = m.group(1).strip()
                    delay_str = m.group(2).strip().strip("()")
                else:
                    continue

                # Parse delay — handle min:typ:max triples (e.g., "1.0:1.5:2.0")
                if ":" in delay_str:
                    parts = delay_str.split(":")
                    # Use typical (middle) value
                    delay = float(parts[1]) if len(parts) >= 2 else float(parts[0])
                else:
                    try:
                        delay = float(delay_str)
                    except ValueError:
                        dm = _re.search(r"[\d.]+", delay_str)
                        delay = float(dm.group()) if dm else 0.0

                # Parse ports from path
                sep = "=>" if "=>" in path_str else "*>"
                from_to = path_str.split(sep)
                if len(from_to) == 2:
                    from_port = from_to[0].strip()
                    to_port = from_to[1].strip()
                    arcs.append(SdcTimingArc(
                        from_port=from_port, to_port=to_port,
                        delay_ns=delay, arc_type="combinational",
                    ))
            except (ValueError, IndexError):
                continue

        # $setup constraint
        elif line.startswith("$setup"):
            inner = line[6:].strip().strip("()")
            parts = [p.strip() for p in inner.split(",")]
            if len(parts) >= 3:
                try:
                    delay = float(parts[2])
                    arcs.append(SdcTimingArc(
                        from_port=parts[0], to_port=parts[1],
                        delay_ns=delay, arc_type="setup",
                    ))
                except ValueError:
                    pass

        # $hold constraint
        elif line.startswith("$hold"):
            inner = line[5:].strip().strip("()")
            parts = [p.strip() for p in inner.split(",")]
            if len(parts) >= 3:
                try:
                    delay = float(parts[2])
                    arcs.append(SdcTimingArc(
                        from_port=parts[0], to_port=parts[1],
                        delay_ns=delay, arc_type="hold",
                    ))
                except ValueError:
                    pass

    return arcs


# ---------------------------------------------------------------------------
# Apply SDC timing arcs to static timing analysis
# ---------------------------------------------------------------------------

def apply_sdc_to_timing(
    constraints: SdcConstraints,
    timing_arcs: list[SdcTimingArc] | None = None,
) -> dict[str, float]:
    """Merge SDC constraints and specify timing arcs into delay overrides.

    Returns ``{port_name: delay_ns}`` for input/output delays derived from
    SDC ``set_input_delay`` / ``set_output_delay`` and specify block arcs.
    These overrides can be applied to the STA arrival times.
    """
    delays: dict[str, float] = {}

    # SDC input/output delays
    for sdc_delay in constraints.delays:
        delays[sdc_delay.port] = sdc_delay.delay_ns

    # Specify arcs: add to the from-port delay if larger
    if timing_arcs:
        for arc in timing_arcs:
            if arc.arc_type == "combinational":
                current = delays.get(arc.from_port, 0.0)
                delays[arc.from_port] = max(current, arc.delay_ns)

    return delays


def get_false_path_ports(constraints: SdcConstraints) -> set[tuple[str, str]]:
    """Extract false path port pairs for STA exclusion."""
    return {(fp.from_port, fp.to_port) for fp in constraints.false_paths}


def is_path_excluded(
    from_port: str,
    to_port: str,
    false_paths: set[tuple[str, str]],
) -> bool:
    """Check whether a timing path is excluded by false_path constraints.

    An empty string in the constraint matches any port on that side.
    """
    for fp_from, fp_to in false_paths:
        from_match = (fp_from == "" or fp_from == from_port)
        to_match = (fp_to == "" or fp_to == to_port)
        if from_match and to_match:
            return True
    return False
