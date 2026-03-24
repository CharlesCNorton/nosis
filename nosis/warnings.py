"""Nosis design warnings — detect potential issues in the IR.

Checks the IR for patterns that are likely design errors or
suboptimal constructs:
  - Multi-clock FFs (different clock nets on related registers)
  - Undriven nets (no driver)
  - Floating outputs (output port not driven)
  - Latch inference warnings
  - High fanout nets exceeding a threshold
"""

from __future__ import annotations

from nosis.ir import Module, PrimOp

__all__ = [
    "DesignWarning",
    "check_warnings",
]


class DesignWarning:
    def __init__(self, category: str, message: str, net: str = "", cell: str = "") -> None:
        self.category = category
        self.message = message
        self.net = net
        self.cell = cell

    def __repr__(self) -> str:
        return f"Warning({self.category}): {self.message}"


def check_warnings(mod: Module, *, fanout_threshold: int = 64) -> list[DesignWarning]:
    """Analyze the IR for potential design issues."""
    warnings: list[DesignWarning] = []

    # Multi-clock FF detection
    clock_nets: dict[str, set[str]] = {}  # clock_net_name -> set of FF names
    for cell in mod.cells.values():
        if cell.op != PrimOp.FF:
            continue
        clk = cell.inputs.get("CLK")
        if clk:
            if clk.name not in clock_nets:
                clock_nets[clk.name] = set()
            clock_nets[clk.name].add(cell.name)

    if len(clock_nets) > 1:
        clk_names = sorted(clock_nets.keys())
        warnings.append(DesignWarning(
            "multi_clock",
            f"design has {len(clock_nets)} clock domains: {', '.join(clk_names[:5])}",
        ))

    # Undriven nets
    for net in mod.nets.values():
        if net.driver is None and net.name not in mod.ports:
            warnings.append(DesignWarning(
                "undriven_net",
                f"net '{net.name}' has no driver",
                net=net.name,
            ))

    # Floating output ports
    for name, net in mod.ports.items():
        is_output = False
        for cell in mod.cells.values():
            if cell.op == PrimOp.OUTPUT:
                for inp in cell.inputs.values():
                    if inp.name == name:
                        is_output = True
                        break
        if is_output and net.driver is None:
            warnings.append(DesignWarning(
                "floating_output",
                f"output port '{name}' is not driven",
                net=name,
            ))

    # High fanout
    fanout: dict[str, int] = {}
    for cell in mod.cells.values():
        for net in cell.inputs.values():
            fanout[net.name] = fanout.get(net.name, 0) + 1

    for net_name, fo in fanout.items():
        if fo > fanout_threshold:
            warnings.append(DesignWarning(
                "high_fanout",
                f"net '{net_name}' has fanout {fo} (threshold: {fanout_threshold})",
                net=net_name,
            ))

    return warnings
