"""Nosis black box support — handle unresolved module instantiations.

A black box is a module that has ports but no internal implementation.
It passes through synthesis as an opaque cell: its port connections
are preserved in the netlist, but no logic is inferred from its body.

Black boxes arise from:
  1. Vendor primitives (USRMCLK, EHXPLLL, etc.) — provided by ecp5_prims.sv
  2. User-defined black boxes declared with empty module bodies
  3. External IP cores referenced but not included in the source list
  4. Modules that failed elaboration and are treated as opaque

This module provides:
  - A registry of known black box modules with port declarations
  - Detection of black box instantiations during lowering
  - Tech mapping of black box cells to passthrough cells in the netlist
  - Validation that all black box ports are connected
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


__all__ = [
    "BlackBoxPort",
    "BlackBoxDef",
    "BlackBoxRegistry",
    "load_ecp5_blackboxes",
]


@dataclass(frozen=True, slots=True)
class BlackBoxPort:
    """A port on a black box module."""
    name: str
    direction: str  # "input", "output", "inout"
    width: int = 1


@dataclass(frozen=True, slots=True)
class BlackBoxDef:
    """Definition of a black box module."""
    name: str
    ports: tuple[BlackBoxPort, ...]
    category: str = "unknown"  # "vendor", "user", "ip"
    description: str = ""

    @property
    def port_names(self) -> list[str]:
        return [p.name for p in self.ports]

    @property
    def input_ports(self) -> list[BlackBoxPort]:
        return [p for p in self.ports if p.direction == "input"]

    @property
    def output_ports(self) -> list[BlackBoxPort]:
        return [p for p in self.ports if p.direction == "output"]


class BlackBoxRegistry:
    """Registry of known black box modules.

    Modules registered here are treated as opaque during lowering —
    their bodies are not traversed, and their port connections are
    preserved as-is in the IR.
    """

    def __init__(self) -> None:
        self._defs: dict[str, BlackBoxDef] = {}

    def register(self, defn: BlackBoxDef) -> None:
        self._defs[defn.name] = defn

    def is_blackbox(self, name: str) -> bool:
        return name in self._defs

    def get(self, name: str) -> BlackBoxDef | None:
        return self._defs.get(name)

    def all_names(self) -> list[str]:
        return sorted(self._defs.keys())

    def __len__(self) -> int:
        return len(self._defs)

    def __contains__(self, name: str) -> bool:
        return name in self._defs

    def register_from_dict(self, name: str, ports: dict[str, str], **kwargs: Any) -> None:
        """Register from a simple dict: {port_name: direction}."""
        port_list = tuple(
            BlackBoxPort(name=pn, direction=pd)
            for pn, pd in ports.items()
        )
        self.register(BlackBoxDef(name=name, ports=port_list, **kwargs))

    def summary(self) -> list[str]:
        lines = [f"Black box registry: {len(self._defs)} modules"]
        for name in sorted(self._defs):
            defn = self._defs[name]
            n_in = len(defn.input_ports)
            n_out = len(defn.output_ports)
            lines.append(f"  {name}: {n_in} inputs, {n_out} outputs [{defn.category}]")
        return lines


# ---------------------------------------------------------------------------
# ECP5 vendor primitives — full port declarations
# ---------------------------------------------------------------------------

def _ecp5_prim(name: str, ports: dict[str, str], desc: str = "") -> BlackBoxDef:
    port_list = tuple(
        BlackBoxPort(name=pn, direction=pd)
        for pn, pd in ports.items()
    )
    return BlackBoxDef(name=name, ports=port_list, category="vendor", description=desc)


_ECP5_PRIMITIVES: list[BlackBoxDef] = [
    # System
    _ecp5_prim("USRMCLK", {"USRMCLKI": "input", "USRMCLKTS": "input"}, "User master SPI clock"),
    _ecp5_prim("GSR", {"GSR": "input"}, "Global set/reset"),
    _ecp5_prim("SGSR", {"GSR": "input", "CLK": "input"}, "Slice global set/reset"),
    _ecp5_prim("PUR", {"PUR": "input"}, "Power-up reset"),
    _ecp5_prim("TSALL", {"TSALL": "input"}, "Tristate all outputs"),
    _ecp5_prim("DTR", {"DTROUT7": "output", "DTROUT6": "output", "DTROUT5": "output",
                        "DTROUT4": "output", "DTROUT3": "output", "DTROUT2": "output",
                        "DTROUT1": "output", "DTROUT0": "output"}, "Die temperature readout"),

    # JTAG
    _ecp5_prim("JTAGG", {
        "JTCK": "output", "JTDI": "output", "JTMS": "output",
        "JTDO1": "output", "JTDO2": "output", "JSHIFT": "output",
        "JUPDATE": "output", "JRSTN": "output", "JCE1": "output",
        "JCE2": "output", "JRTI1": "output", "JRTI2": "output",
        "JTDO": "input",
    }, "JTAG interface"),

    # SED
    _ecp5_prim("SEDGA", {
        "SEDSTDBY": "output", "SEDENABLE": "output", "SEDSTART": "output",
        "SEDDONE": "output", "SEDINPROG": "output", "SEDERR": "output",
    }, "Soft error detection"),

    # Internal oscillator
    _ecp5_prim("OSCG", {"OSC": "output"}, "Internal oscillator (2.4-133 MHz)"),

    # Clock
    _ecp5_prim("CLKDIVF", {
        "CLKI": "input", "RST": "input", "ALIGNWD": "input", "CDIVX": "output",
    }, "Clock divider"),
    _ecp5_prim("DCCA", {"CLKI": "input", "CE": "input", "CLKO": "output"}, "Dedicated clock buffer"),
    _ecp5_prim("DCC", {"CLKI": "input", "CE": "input", "CLKO": "output"}, "Dynamic clock control"),
    _ecp5_prim("DCSC", {
        "CLK0": "input", "CLK1": "input", "SEL0": "input", "SEL1": "input",
        "MODESEL": "input", "DCSOUT": "output",
    }, "Dynamic clock stop"),
    _ecp5_prim("DQSCE", {
        "CLK": "input", "DQSW": "input", "CE": "input",
        "DQSW270": "output",
    }, "DQS clock enable"),
    _ecp5_prim("ECLKSYNCB", {
        "ECLKI": "input", "STOP": "input", "ECLKO": "output",
    }, "Edge clock sync buffer"),
    _ecp5_prim("ECLKBRIDGECS", {
        "CLK0": "input", "CLK1": "input", "SEL": "input", "ECSOUT": "output",
    }, "Edge clock bridge"),
    _ecp5_prim("PCSCLKDIV", {
        "CLKI": "input", "RST": "input", "SEL2": "input", "SEL1": "input", "SEL0": "input",
        "CDIV1": "output", "CDIVX": "output",
    }, "PCS clock divider"),
    _ecp5_prim("EXTREFB", {
        "REFCLKP": "input", "REFCLKN": "input", "REFCLKO": "output",
    }, "External reference clock buffer"),

    # PLL
    _ecp5_prim("EHXPLLL", {
        "CLKI": "input", "CLKFB": "input", "RST": "input", "STDBY": "input",
        "PHASESEL0": "input", "PHASESEL1": "input", "PHASEDIR": "input",
        "PHASESTEP": "input", "PHASELOADREG": "input",
        "CLKOP": "output", "CLKOS": "output", "CLKOS2": "output", "CLKOS3": "output",
        "LOCK": "output", "INTLOCK": "output",
    }, "Primary PLL"),
    _ecp5_prim("EHXPLLJ", {
        "CLKI": "input", "CLKFB": "input", "RST": "input", "STDBY": "input",
        "PHASESEL0": "input", "PHASESEL1": "input", "PHASEDIR": "input",
        "PHASESTEP": "input", "PHASELOADREG": "input",
        "CLKOP": "output", "CLKOS": "output", "CLKOS2": "output", "CLKOS3": "output",
        "LOCK": "output", "INTLOCK": "output",
    }, "JTAG-configurable PLL"),

    # I/O
    _ecp5_prim("BB", {"I": "input", "T": "input", "O": "output", "B": "inout"}, "Bidirectional buffer"),
    _ecp5_prim("IB", {"I": "input", "O": "output"}, "Input buffer"),
    _ecp5_prim("OB", {"I": "input", "O": "output"}, "Output buffer"),
    _ecp5_prim("OBZ", {"I": "input", "T": "input", "O": "output"}, "Tristate output buffer"),
    _ecp5_prim("BBPU", {"I": "input", "T": "input", "O": "output", "B": "inout"}, "Bidirectional with pull-up"),
    _ecp5_prim("BBPD", {"I": "input", "T": "input", "O": "output", "B": "inout"}, "Bidirectional with pull-down"),
    _ecp5_prim("IBPU", {"I": "input", "O": "output"}, "Input with pull-up"),
    _ecp5_prim("IBPD", {"I": "input", "O": "output"}, "Input with pull-down"),

    # DDR I/O
    _ecp5_prim("IDDRX1F", {
        "D": "input", "SCLK": "input", "RST": "input",
        "Q0": "output", "Q1": "output",
    }, "Input DDR 1:1"),
    _ecp5_prim("IDDRX2F", {
        "D": "input", "SCLK": "input", "ECLK": "input", "RST": "input",
        "Q0": "output", "Q1": "output", "Q2": "output", "Q3": "output",
    }, "Input DDR 1:2"),
    _ecp5_prim("ODDRX1F", {
        "D0": "input", "D1": "input", "SCLK": "input", "RST": "input",
        "Q": "output",
    }, "Output DDR 1:1"),
    _ecp5_prim("ODDRX2F", {
        "D0": "input", "D1": "input", "D2": "input", "D3": "input",
        "SCLK": "input", "ECLK": "input", "RST": "input",
        "Q": "output",
    }, "Output DDR 1:2"),
    _ecp5_prim("IDDR71B", {
        "D": "input", "SCLK": "input", "ECLK": "input", "RST": "input",
        "ALIGNWD": "input",
        "Q0": "output", "Q1": "output", "Q2": "output", "Q3": "output",
        "Q4": "output", "Q5": "output", "Q6": "output",
    }, "Input DDR 1:7"),
    _ecp5_prim("ODDR71B", {
        "D0": "input", "D1": "input", "D2": "input", "D3": "input",
        "D4": "input", "D5": "input", "D6": "input",
        "SCLK": "input", "ECLK": "input", "RST": "input",
        "Q": "output",
    }, "Output DDR 1:7"),
    _ecp5_prim("OSHX2A", {
        "D0": "input", "D1": "input", "SCLK": "input", "ECLK": "input", "RST": "input",
        "Q": "output",
    }, "Output serializer"),
    _ecp5_prim("ISHX2A", {
        "D": "input", "SCLK": "input", "ECLK": "input", "RST": "input",
        "Q0": "output", "Q1": "output",
    }, "Input deserializer"),
    _ecp5_prim("TSHX2DQA", {
        "T0": "input", "T1": "input", "SCLK": "input", "ECLK": "input", "RST": "input",
        "Q": "output",
    }, "Tristate DDR"),
    _ecp5_prim("TSHX2DQSA", {
        "T0": "input", "T1": "input", "SCLK": "input", "ECLK": "input", "RST": "input",
        "Q": "output",
    }, "Tristate DDR (DQS)"),

    # I/O delay
    _ecp5_prim("DELAYF", {
        "A": "input", "LOADN": "input", "MOVE": "input", "DIRECTION": "input",
        "Z": "output", "CFLAG": "output",
    }, "Input programmable delay"),
    _ecp5_prim("DELAYG", {
        "A": "input",
        "Z": "output",
    }, "Output programmable delay"),
    _ecp5_prim("DQSBUFM", {
        "DQSI": "input", "READ0": "input", "READ1": "input",
        "READCLKSEL0": "input", "READCLKSEL1": "input", "READCLKSEL2": "input",
        "DDRDEL": "input", "ECLK": "input", "SCLK": "input", "RST": "input",
        "DYNDELAY0": "input", "DYNDELAY1": "input", "DYNDELAY2": "input",
        "DYNDELAY3": "input", "DYNDELAY4": "input", "DYNDELAY5": "input",
        "DYNDELAY6": "input", "DYNDELAY7": "input",
        "PAUSE": "input", "RDLOADN": "input", "RDMOVE": "input", "RDDIRECTION": "input",
        "WRLOADN": "input", "WRMOVE": "input", "WRDIRECTION": "input",
        "DQSR90": "output", "DQSW": "output", "DQSW270": "output",
        "RDPNTR0": "output", "RDPNTR1": "output", "RDPNTR2": "output",
        "WRPNTR0": "output", "WRPNTR1": "output", "WRPNTR2": "output",
        "DATAVALID": "output", "BURSTDET": "output",
        "RDCFLAG": "output", "WRCFLAG": "output",
    }, "DQS buffer manager"),

    # I/O registers
    _ecp5_prim("IFS1P3BX", {
        "D": "input", "SP": "input", "SCLK": "input", "PD": "input",
        "Q": "output",
    }, "Input FF (preset)"),
    _ecp5_prim("IFS1P3DX", {
        "D": "input", "SP": "input", "SCLK": "input", "CD": "input",
        "Q": "output",
    }, "Input FF (clear)"),
    _ecp5_prim("OFS1P3BX", {
        "D": "input", "SP": "input", "SCLK": "input", "PD": "input",
        "Q": "output",
    }, "Output FF (preset)"),
    _ecp5_prim("OFS1P3DX", {
        "D": "input", "SP": "input", "SCLK": "input", "CD": "input",
        "Q": "output",
    }, "Output FF (clear)"),

    # SerDes
    _ecp5_prim("DCUA", {
        "CH0_HDINP": "input", "CH0_HDINN": "input",
        "CH0_HDOUTP": "output", "CH0_HDOUTN": "output",
        "CH1_HDINP": "input", "CH1_HDINN": "input",
        "CH1_HDOUTP": "output", "CH1_HDOUTN": "output",
        "D_REFCLKI": "input", "D_FFS_PLOL": "output",
    }, "Dual-channel SerDes (5G variants)"),

    # Configuration
    _ecp5_prim("START", {"STARTCLK": "input"}, "Startup sequence control"),
    _ecp5_prim("BCINRD", {"PADDT": "input", "BCINRDP": "output", "BCINRDN": "output"}, "Configuration readback"),
]


def load_ecp5_blackboxes() -> BlackBoxRegistry:
    """Create a registry pre-loaded with all ECP5 vendor primitives."""
    registry = BlackBoxRegistry()
    for prim in _ECP5_PRIMITIVES:
        registry.register(prim)
    return registry


def load_blackbox_file(path: str | Path, registry: BlackBoxRegistry | None = None) -> BlackBoxRegistry:
    """Load user-defined black box declarations from a file.

    The file format is one module per line:
      module_name input:port1 input:port2 output:port3

    Lines starting with # are comments. Empty lines are skipped.
    """
    if registry is None:
        registry = BlackBoxRegistry()

    text = Path(path).read_text(encoding="utf-8")
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) < 2:
            continue
        name = parts[0]
        ports: list[BlackBoxPort] = []
        for part in parts[1:]:
            if ":" in part:
                direction, port_name = part.split(":", 1)
                ports.append(BlackBoxPort(name=port_name, direction=direction))
            else:
                ports.append(BlackBoxPort(name=part, direction="input"))
        registry.register(BlackBoxDef(
            name=name,
            ports=tuple(ports),
            category="user",
        ))

    return registry
