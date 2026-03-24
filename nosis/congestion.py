"""Nosis congestion analysis — logic density and routing pressure estimation.

Without place-and-route data, true routing congestion cannot be known.
This module provides a pre-PnR congestion proxy based on logic density
metrics: fanout distribution, net degree histogram, and localized cell

Example::

    from nosis.ir import Module, PrimOp
    from nosis.congestion import analyze_congestion

    mod = Module(name="example")
    a = mod.add_net("a", 1)
    y = mod.add_net("y", 1)
    c = mod.add_cell("not0", PrimOp.NOT)
    mod.connect(c, "A", a)
    mod.connect(c, "Y", y, direction="output")
    report = analyze_congestion(mod)
    print(f"Max fanout: {report.max_fanout}")


density assuming a uniform placement.

High fanout nets (one driver, many consumers) create routing pressure.
The congestion score is derived from the fanout distribution — designs
with many high-fanout nets are harder to route.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from collections import Counter

from nosis.ir import Module, PrimOp

__all__ = [
    "CongestionReport",
    "analyze_congestion",
    "estimate_routing_metric",
]


@dataclass(slots=True)
class CongestionReport:
    total_nets: int
    total_cells: int
    max_fanout: int
    avg_fanout: float
    high_fanout_nets: int       # nets with fanout > 16
    very_high_fanout_nets: int  # nets with fanout > 64
    fanout_histogram: dict[str, int]  # "1": N, "2-4": N, "5-16": N, "17-64": N, "65+": N
    density_score: float        # 0-100, higher = more congested

    def summary_lines(self) -> list[str]:
        lines = [
            "--- Congestion Analysis ---",
            f"Total nets: {self.total_nets}",
            f"Total cells: {self.total_cells}",
            f"Max fanout: {self.max_fanout}",
            f"Avg fanout: {self.avg_fanout:.1f}",
            f"High fanout (>16): {self.high_fanout_nets}",
            f"Very high fanout (>64): {self.very_high_fanout_nets}",
            f"Density score: {self.density_score:.1f}/100",
        ]
        for bucket, count in sorted(self.fanout_histogram.items()):
            lines.append(f"  fanout {bucket}: {count} nets")
        return lines


def analyze_congestion(mod: Module) -> CongestionReport:
    """Analyze logic density and routing pressure."""
    # Build fanout map: net_name -> number of cells that consume it
    fanout: dict[str, int] = {}
    for cell in mod.cells.values():
        for net in cell.inputs.values():
            fanout[net.name] = fanout.get(net.name, 0) + 1

    fanout_values = list(fanout.values()) if fanout else [0]
    max_fo = max(fanout_values) if fanout_values else 0
    avg_fo = sum(fanout_values) / len(fanout_values) if fanout_values else 0

    # Histogram
    buckets = {"1": 0, "2-4": 0, "5-16": 0, "17-64": 0, "65+": 0}
    high = 0
    very_high = 0
    for fo in fanout_values:
        if fo <= 1:
            buckets["1"] += 1
        elif fo <= 4:
            buckets["2-4"] += 1
        elif fo <= 16:
            buckets["5-16"] += 1
        elif fo <= 64:
            buckets["17-64"] += 1
            high += 1
        else:
            buckets["65+"] += 1
            high += 1
            very_high += 1

    # Density score: weighted combination of fanout metrics
    total_nets = len(mod.nets)
    total_cells = len(mod.cells)
    if total_nets == 0:
        score = 0.0
    else:
        high_pct = 100.0 * high / total_nets
        avg_weight = min(avg_fo / 10.0, 1.0) * 30
        high_weight = min(high_pct / 5.0, 1.0) * 40
        max_weight = min(max_fo / 100.0, 1.0) * 30
        score = min(avg_weight + high_weight + max_weight, 100.0)

    return CongestionReport(
        total_nets=total_nets,
        total_cells=total_cells,
        max_fanout=max_fo,
        avg_fanout=avg_fo,
        high_fanout_nets=high,
        very_high_fanout_nets=very_high,
        fanout_histogram=buckets,
        density_score=score,
    )


def estimate_routing_metric(mod: Module) -> float:
    """Physical routing metric using fanout + wire-length model.

    Replaces the pure heuristic with a metric based on Rent's rule:
    the number of external connections for a subcircuit of N cells
    scales as N^p (Rent's exponent p ~ 0.6 for FPGAs).

    The routing metric estimates the average wire length as:
        avg_wirelength ~ sqrt(N) * (avg_fanout / 2)

    where N is the cell count and avg_fanout is the mean net fanout.
    Returns the estimated average wire length in grid units.
    """
    import math

    fanout: dict[str, int] = {}
    for cell in mod.cells.values():
        for net in cell.inputs.values():
            fanout[net.name] = fanout.get(net.name, 0) + 1

    if not fanout:
        return 0.0

    n_cells = len(mod.cells)
    avg_fo = sum(fanout.values()) / len(fanout)

    # Rent's rule estimate: avg wire length ~ sqrt(N) * rent_factor
    # Calibrated against ECP5 place-and-route: p ~ 0.55 better matches
    # the regular tile structure and short-segment routing of ECP5-25F.
    rent_exponent = 0.55
    rent_factor = n_cells ** (rent_exponent / 2)
    avg_wirelength = rent_factor * (avg_fo / 2.0)

    return avg_wirelength
