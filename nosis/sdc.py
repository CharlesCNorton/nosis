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
    "parse_sdc",
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

    return constraints
