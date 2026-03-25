"""Nosis routing delay estimation — wire-length model from fanout and cell count.

Estimates routing delay based on the empirical observation that
average wire length grows with sqrt(cell_count) on an FPGA.
Combined with per-net fanout, this gives a routing delay component
to add to the logic-only timing from timing.py.

ECP5 routing model:
  - Base interconnect delay per hop: ~0.3 ns
  - Average hops per net: proportional to sqrt(fanout)
  - Global routing overhead: ~0.5 ns for high-fanout nets (>16)
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from nosis.ir import Module, PrimOp

__all__ = [
    "RoutingEstimate",
    "estimate_routing",
]


@dataclass(slots=True)
class RoutingEstimate:
    total_nets: int
    avg_routing_delay_ns: float
    max_routing_delay_ns: float
    estimated_total_delay_ns: float  # logic + routing on critical path
    logic_delay_ns: float

    def summary_lines(self) -> list[str]:
        return [
            "--- Routing Delay Estimate ---",
            f"Avg routing delay: {self.avg_routing_delay_ns:.2f} ns",
            f"Max routing delay: {self.max_routing_delay_ns:.2f} ns",
            f"Logic delay: {self.logic_delay_ns:.2f} ns",
            f"Estimated total: {self.estimated_total_delay_ns:.2f} ns",
            f"Estimated Fmax: {1000.0 / self.estimated_total_delay_ns:.1f} MHz" if self.estimated_total_delay_ns > 0 else "Estimated Fmax: N/A",
        ]


def estimate_routing(mod: Module, logic_delay_ns: float = 0.0) -> RoutingEstimate:
    """Estimate routing delays from the IR module structure."""
    # Build fanout map
    fanout: dict[str, int] = {}
    for cell in mod.cells.values():
        for net in cell.inputs.values():
            fanout[net.name] = fanout.get(net.name, 0) + 1

    if not fanout:
        return RoutingEstimate(0, 0.0, 0.0, logic_delay_ns, logic_delay_ns)

    total_cells = len(mod.cells)
    sqrt_cells = math.sqrt(max(total_cells, 1))

    # Per-net routing delay: base_delay * sqrt(fanout) * scaling
    # Calibrated against nextpnr ECP5-25F: uart_tx actual routing ~1.1 ns
    base_delay = 0.4  # ns per hop (ECP5 PIB interconnect)
    scale = max(0.3, min(sqrt_cells / 30.0, 2.5))  # minimum 0.3 for small designs

    # Identify nets that use dedicated routing resources (lower delay)
    clock_nets: set[str] = set()
    carry_nets: set[str] = set()
    for cell in mod.cells.values():
        if cell.op == PrimOp.FF:
            clk = cell.inputs.get("CLK")
            if clk:
                clock_nets.add(clk.name)
        if cell.params.get("carry_config"):
            for net in cell.outputs.values():
                carry_nets.add(net.name)

    delays: list[float] = []
    for net_name, fo in fanout.items():
        if net_name in clock_nets:
            # Dedicated clock routing — much lower delay than general routing
            net_delay = 0.05 * math.sqrt(fo)
        elif net_name in carry_nets:
            # Carry chain routing — dedicated column, very short
            net_delay = 0.02
        else:
            net_delay = base_delay * math.sqrt(fo) * scale
            if fo > 16:
                net_delay += 0.5  # global routing overhead
        delays.append(net_delay)

    avg_delay = sum(delays) / len(delays) if delays else 0.0
    max_delay = max(delays) if delays else 0.0

    # Critical path routing: assume routing adds proportionally to logic
    # Calibrated: routing is typically 40-70% of total delay on ECP5-25F
    routing_on_critical = max_delay * 0.75
    total = logic_delay_ns + routing_on_critical

    return RoutingEstimate(
        total_nets=len(fanout),
        avg_routing_delay_ns=avg_delay,
        max_routing_delay_ns=max_delay,
        estimated_total_delay_ns=total,
        logic_delay_ns=logic_delay_ns,
    )
