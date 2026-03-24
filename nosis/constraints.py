"""Nosis constraint handling — parse LPF pin constraints and I/O standards.

Reads Lattice Preference Files (.lpf) to extract:
  - Pin LOCATIONs (COMP → pin assignment)
  - I/O standards (IOBUF PORT → voltage/standard)
  - FREQUENCY preferences
  - SYSCONFIG settings

The parsed constraints are available for validation against the
synthesized netlist — verifying that every constrained port exists
in the design and that I/O standards are compatible.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

__all__ = [
    "PinConstraint",
    "IoStandard",
    "FrequencyConstraint",
    "LpfConstraints",
    "parse_lpf",
]


@dataclass(frozen=True, slots=True)
class PinConstraint:
    comp: str          # component/port name
    pin: str           # physical pin (e.g., "A4", "B2")
    site: str = ""     # site name if specified


@dataclass(frozen=True, slots=True)
class IoStandard:
    port: str          # port name
    standard: str      # I/O standard (e.g., "LVCMOS33", "LVDS25")
    drive: str = ""    # drive strength (e.g., "8")
    pullmode: str = "" # pull mode (e.g., "UP", "DOWN", "NONE")
    slewrate: str = "" # slew rate (e.g., "FAST", "SLOW")


@dataclass(frozen=True, slots=True)
class FrequencyConstraint:
    net: str           # net/port name
    frequency_mhz: float


@dataclass(slots=True)
class LpfConstraints:
    pins: list[PinConstraint] = field(default_factory=list)
    io_standards: list[IoStandard] = field(default_factory=list)
    frequencies: list[FrequencyConstraint] = field(default_factory=list)
    sysconfig: dict[str, str] = field(default_factory=dict)
    raw_lines: int = 0

    def port_names(self) -> set[str]:
        """Return the list of port names."""
        names: set[str] = set()
        for p in self.pins:
            names.add(p.comp)
        for io in self.io_standards:
            names.add(io.port)
        return names

    def validate_against_ports(self, design_ports: set[str]) -> list[str]:
        """Check that all constrained ports exist in the design."""
        warnings: list[str] = []
        for name in self.port_names():
            if name not in design_ports:
                warnings.append(f"constrained port '{name}' not found in design")
        return warnings

    def summary_lines(self) -> list[str]:
        """Return human-readable summary lines."""
        lines = [
            "--- LPF Constraints ---",
            f"Pin assignments: {len(self.pins)}",
            f"I/O standards: {len(self.io_standards)}",
            f"Frequency constraints: {len(self.frequencies)}",
            f"SYSCONFIG entries: {len(self.sysconfig)}",
        ]
        return lines


def parse_lpf(path: str | Path) -> LpfConstraints:
    """Parse a Lattice Preference File (.lpf)."""
    text = Path(path).read_text(encoding="utf-8")
    constraints = LpfConstraints()
    constraints.raw_lines = len(text.splitlines())

    # Join multi-line statements: lines not ending with ';' are continuations
    raw_lines = text.splitlines()
    joined_lines: list[str] = []
    current = ""
    for raw_line in raw_lines:
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#") or stripped.startswith("//"):
            continue
        current += " " + stripped if current else stripped
        if stripped.endswith(";") or not any(c.isalpha() for c in stripped):
            joined_lines.append(current)
            current = ""
    if current:
        joined_lines.append(current)

    for line in joined_lines:

        upper = line.upper()

        # LOCATE COMP "name" SITE "pin";
        if upper.startswith("LOCATE"):
            parts = line.split('"')
            if len(parts) >= 4:
                comp = parts[1]
                site = parts[3]
                constraints.pins.append(PinConstraint(comp=comp, pin=site, site=site))

        # IOBUF PORT "name" IO_TYPE=LVCMOS33 DRIVE=8;
        elif upper.startswith("IOBUF"):
            parts = line.split('"')
            if len(parts) >= 2:
                port = parts[1]
                standard = ""
                drive = ""
                pullmode = ""
                slewrate = ""
                remainder = line.split('"')[-1] if len(parts) > 2 else ""
                for token in remainder.replace(";", "").split():
                    if "=" in token:
                        key, val = token.split("=", 1)
                        key_up = key.upper()
                        if key_up == "IO_TYPE":
                            standard = val
                        elif key_up == "DRIVE":
                            drive = val
                        elif key_up == "PULLMODE":
                            pullmode = val
                        elif key_up == "SLEWRATE":
                            slewrate = val
                constraints.io_standards.append(IoStandard(
                    port=port, standard=standard, drive=drive,
                    pullmode=pullmode, slewrate=slewrate,
                ))

        # FREQUENCY PORT "clk" 25.0 MHz;
        elif upper.startswith("FREQUENCY"):
            parts = line.split('"')
            if len(parts) >= 2:
                net = parts[1]
                remainder = line.split('"')[-1].replace(";", "").strip()
                tokens = remainder.split()
                freq = 0.0
                for t in tokens:
                    try:
                        freq = float(t)
                        break
                    except ValueError:
                        continue
                if freq > 0:
                    constraints.frequencies.append(FrequencyConstraint(net=net, frequency_mhz=freq))

        # SYSCONFIG key=value;
        elif upper.startswith("SYSCONFIG"):
            for token in line.replace("SYSCONFIG", "").replace(";", "").split():
                if "=" in token:
                    key, val = token.split("=", 1)
                    constraints.sysconfig[key] = val

        # BLOCK ... (ignored for now)

    return constraints
