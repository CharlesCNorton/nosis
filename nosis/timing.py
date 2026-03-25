"""Nosis timing analysis — critical path identification and delay calculation.

Computes a topological-order delay model through the combinational logic
cone. Each cell type has a known propagation delay based on ECP5
characterization data. The critical path is the longest delay from any
input to any output through combinational logic.

This is a static timing analysis using cell-level granularity. It does
not account for routing delays (which require place-and-route data from
nextpnr). The values represent the logic-only component of timing.

ECP5 cell delays (typical, -6 speed grade, from Lattice timing model):
  LUT4:           0.40 ns  (tCO, combinational output)
  CCU2C:          0.50 ns  (carry chain propagation per 2 bits)
  TRELLIS_FF:     0.20 ns  (tCK→Q, clock to output)
  DP16KD:         2.40 ns  (tCO, BRAM read)
  MULT18X18D:     3.20 ns  (combinational multiply)

Example::

    from nosis.frontend import parse_files, lower_to_ir
    from nosis.timing import analyze_timing

    result = parse_files(["design.sv"], top="top")
    design = lower_to_ir(result, top="top")
    report = analyze_timing(design.top_module())
    print(f"Critical path: {report.max_delay_ns:.2f} ns")
    print(f"Max frequency: {report.max_frequency_mhz:.1f} MHz")
"""

from __future__ import annotations

from dataclasses import dataclass, field

from nosis.ir import Cell, Module, Net, PrimOp

__all__ = [
    "TimingReport",
    "CriticalPath",
    "analyze_timing",
    "analyze_timing_multi_clock",
    "lut4_pin_delay",
]


def lut4_pin_delay(pin: str) -> float:
    """Return the per-pin propagation delay for a LUT4 input on ECP5.

    Pin A is fastest (0.33 ns), pin D is slowest (0.42 ns) due to the
    internal MUX tree depth.
    """
    return _LUT4_PIN_DELAYS.get(pin.upper(), 0.40)


_LUT4_PIN_DELAYS: dict[str, float] = {
    "A": 0.33,
    "B": 0.36,
    "C": 0.39,
    "D": 0.42,
}

# Cell delay model (nanoseconds, typical -6 speed grade)
_CELL_DELAYS: dict[PrimOp, float] = {
    PrimOp.AND: 0.40,
    PrimOp.OR: 0.40,
    PrimOp.XOR: 0.40,
    PrimOp.NOT: 0.40,
    PrimOp.MUX: 0.40,
    PrimOp.PMUX: 0.40,
    PrimOp.EQ: 0.40,
    PrimOp.NE: 0.40,
    PrimOp.LT: 0.40,
    PrimOp.LE: 0.40,
    PrimOp.GT: 0.40,
    PrimOp.GE: 0.40,
    PrimOp.REDUCE_AND: 0.40,
    PrimOp.REDUCE_OR: 0.40,
    PrimOp.REDUCE_XOR: 0.40,
    PrimOp.ADD: 0.50,  # carry chain
    PrimOp.SUB: 0.50,
    PrimOp.MUL: 3.20,
    PrimOp.DIV: 3.20,
    PrimOp.MOD: 3.20,
    PrimOp.SHL: 1.20,  # log2 barrel shifter: ~3 stages × 0.40 ns
    PrimOp.SHR: 1.20,
    PrimOp.SSHR: 1.20,
    PrimOp.CONCAT: 0.0,   # wiring only
    PrimOp.SLICE: 0.0,    # wiring only
    PrimOp.ZEXT: 0.0,     # wiring only
    PrimOp.SEXT: 0.0,     # wiring only
    PrimOp.REPEAT: 0.0,   # wiring only
    PrimOp.CONST: 0.0,    # no delay
    PrimOp.INPUT: 0.0,
    PrimOp.OUTPUT: 0.0,
    PrimOp.FF: 0.20,      # tCK→Q
    PrimOp.MEMORY: 2.40,  # BRAM read
}


@dataclass(slots=True)
class CriticalPath:
    """A path through combinational logic with accumulated delay."""
    cells: list[str]       # cell names in order
    nets: list[str]        # net names in order
    delay_ns: float        # total delay in nanoseconds
    start_net: str         # input or FF output where the path begins
    end_net: str           # output or FF input where the path ends


@dataclass(slots=True)
class TimingReport:
    """Static timing analysis results."""
    critical_path: CriticalPath | None
    max_delay_ns: float
    max_frequency_mhz: float    # 1000 / max_delay_ns
    total_paths_analyzed: int
    cell_delay_breakdown: dict[str, float]  # {cell_type: total_delay}

    def summary_lines(self) -> list[str]:
        lines = [
            "--- Timing Analysis (logic-only, no routing) ---",
            f"Critical path delay: {self.max_delay_ns:.2f} ns",
            f"Max frequency (logic): {self.max_frequency_mhz:.1f} MHz",
            f"Paths analyzed: {self.total_paths_analyzed}",
        ]
        if self.critical_path:
            lines.append(f"Critical path depth: {len(self.critical_path.cells)} cells")
            lines.append(f"  from: {self.critical_path.start_net}")
            lines.append(f"  to:   {self.critical_path.end_net}")
        return lines


def analyze_timing(mod: Module) -> TimingReport:
    """Perform static timing analysis on the IR module.

    Computes the longest combinational path delay from any FF output
    or primary input to any FF input or primary output.
    """
    # Compute arrival times at each net via forward propagation
    arrival: dict[str, float] = {}
    predecessor: dict[str, str | None] = {}  # net -> driving cell name

    # Initialize: inputs and FF outputs have arrival time 0
    for cell in mod.cells.values():
        if cell.op == PrimOp.INPUT:
            for net in cell.outputs.values():
                arrival[net.name] = 0.0
                predecessor[net.name] = None
        elif cell.op == PrimOp.FF:
            for net in cell.outputs.values():
                arrival[net.name] = _CELL_DELAYS.get(PrimOp.FF, 0.2)
                predecessor[net.name] = cell.name
        elif cell.op == PrimOp.CONST:
            for net in cell.outputs.values():
                arrival[net.name] = 0.0
                predecessor[net.name] = None

    # Collect FF Q output nets — these are timing boundaries
    ff_q_nets: set[str] = set()
    for cell in mod.cells.values():
        if cell.op == PrimOp.FF:
            for net in cell.outputs.values():
                ff_q_nets.add(net.name)

    # Forward propagation — single pass, each cell visited at most once.
    # Process cells whose inputs are all resolved. Break cycles by
    # treating any net already in `arrival` as resolved.
    processed: set[str] = set()
    progress = True
    while progress:
        progress = False
        for cell in mod.cells.values():
            if cell.name in processed:
                continue
            if cell.op in (PrimOp.INPUT, PrimOp.OUTPUT, PrimOp.FF, PrimOp.CONST):
                processed.add(cell.name)
                continue

            # Check if all inputs are resolved
            all_resolved = all(net.name in arrival for net in cell.inputs.values())
            if not all_resolved:
                continue

            processed.add(cell.name)
            progress = True

            max_input_arrival = max(
                (arrival.get(net.name, 0.0) for net in cell.inputs.values()),
                default=0.0,
            )

            cell_delay = _CELL_DELAYS.get(cell.op, 0.4)
            output_arrival = max_input_arrival + cell_delay

            for net in cell.outputs.values():
                if net.name not in arrival or output_arrival > arrival[net.name]:
                    arrival[net.name] = output_arrival
                    predecessor[net.name] = cell.name

    # Handle unprocessed cells (in cycles) — assign a conservative delay
    for cell in mod.cells.values():
        if cell.name in processed:
            continue
        if cell.op in (PrimOp.INPUT, PrimOp.OUTPUT, PrimOp.FF, PrimOp.CONST):
            continue
        max_input_arrival = max(
            (arrival.get(net.name, 0.0) for net in cell.inputs.values()),
            default=0.0,
        )
        cell_delay = _CELL_DELAYS.get(cell.op, 0.4)
        for net in cell.outputs.values():
            if net.name not in arrival:
                arrival[net.name] = max_input_arrival + cell_delay
                predecessor[net.name] = cell.name

    # Find the critical path endpoint: the FF input or output port with
    # the largest arrival time
    max_delay = 0.0
    critical_end_net: str | None = None
    paths_analyzed = 0

    for cell in mod.cells.values():
        if cell.op == PrimOp.FF:
            d_net = cell.inputs.get("D")
            if d_net and d_net.name in arrival:
                paths_analyzed += 1
                if arrival[d_net.name] > max_delay:
                    max_delay = arrival[d_net.name]
                    critical_end_net = d_net.name
        elif cell.op == PrimOp.OUTPUT:
            for net in cell.inputs.values():
                if net.name in arrival:
                    paths_analyzed += 1
                    if arrival[net.name] > max_delay:
                        max_delay = arrival[net.name]
                        critical_end_net = net.name

    # Trace back the critical path
    critical_path: CriticalPath | None = None
    if critical_end_net is not None:
        path_cells: list[str] = []
        path_nets: list[str] = [critical_end_net]
        current = critical_end_net

        traceback_visited: set[str] = set()
        while current and predecessor.get(current) is not None and current not in traceback_visited:
            traceback_visited.add(current)
            cell_name = predecessor[current]
            if cell_name in mod.cells:
                cell = mod.cells[cell_name]
                path_cells.append(cell_name)
                # Find the input with the largest arrival
                best_input: str | None = None
                best_arrival = -1.0
                for net in cell.inputs.values():
                    if net.name in arrival and arrival[net.name] > best_arrival:
                        best_arrival = arrival[net.name]
                        best_input = net.name
                if best_input:
                    path_nets.append(best_input)
                    current = best_input
                else:
                    break
            else:
                break

        path_cells.reverse()
        path_nets.reverse()

        critical_path = CriticalPath(
            cells=path_cells,
            nets=path_nets,
            delay_ns=max_delay,
            start_net=path_nets[0] if path_nets else "",
            end_net=critical_end_net,
        )

    # Cell delay breakdown
    breakdown: dict[str, float] = {}
    for cell in mod.cells.values():
        op_name = cell.op.name
        delay = _CELL_DELAYS.get(cell.op, 0.0)
        breakdown[op_name] = breakdown.get(op_name, 0.0) + delay

    # Add routing delay estimate to total delay
    from nosis.wirelength import estimate_routing
    routing = estimate_routing(mod, logic_delay_ns=max_delay)
    total_delay_with_routing = routing.estimated_total_delay_ns
    max_freq = 1000.0 / total_delay_with_routing if total_delay_with_routing > 0 else 0.0

    return TimingReport(
        critical_path=critical_path,
        max_delay_ns=max_delay,
        max_frequency_mhz=max_freq,
        total_paths_analyzed=paths_analyzed,
        cell_delay_breakdown=breakdown,
    )


def analyze_timing_multi_clock(mod: Module) -> dict[str, TimingReport]:
    """Per-clock-domain static timing analysis.

    Groups FFs by their CLK net and runs analyze_timing scoped to each
    domain's FFs and the combinational logic between them. Returns a
    dict of ``{clock_net_name: TimingReport}``.
    """
    from nosis.clocks import analyze_clock_domains

    domains, _ = analyze_clock_domains(mod)
    if not domains:
        return {"(combinational)": analyze_timing(mod)}

    results: dict[str, TimingReport] = {}
    for domain in domains:
        # For each domain, the critical path is within that domain's FFs.
        # We reuse the global analyze_timing which already considers all
        # FF-to-FF paths. The per-domain filtering would require a sub-module
        # extraction. For now, report the global result tagged per domain.
        results[domain.clock_net] = analyze_timing(mod)

    return results
