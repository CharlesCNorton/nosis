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
]

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
    PrimOp.SHL: 0.80,  # barrel shifter LUT chain
    PrimOp.SHR: 0.80,
    PrimOp.SSHR: 0.80,
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

    # Forward propagation through combinational logic
    changed = True
    iterations = 0
    max_iterations = len(mod.cells) + 10

    while changed and iterations < max_iterations:
        changed = False
        iterations += 1
        for cell in mod.cells.values():
            if cell.op in (PrimOp.INPUT, PrimOp.OUTPUT, PrimOp.FF, PrimOp.CONST):
                continue

            # Maximum arrival time of all inputs
            max_input_arrival = 0.0
            for net in cell.inputs.values():
                if net.name in arrival:
                    max_input_arrival = max(max_input_arrival, arrival[net.name])

            cell_delay = _CELL_DELAYS.get(cell.op, 0.4)
            output_arrival = max_input_arrival + cell_delay

            for net in cell.outputs.values():
                if net.name not in arrival or output_arrival > arrival[net.name]:
                    arrival[net.name] = output_arrival
                    predecessor[net.name] = cell.name
                    changed = True

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

        while current and predecessor.get(current) is not None:
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

    max_freq = 1000.0 / max_delay if max_delay > 0 else 0.0

    return TimingReport(
        critical_path=critical_path,
        max_delay_ns=max_delay,
        max_frequency_mhz=max_freq,
        total_paths_analyzed=paths_analyzed,
        cell_delay_breakdown=breakdown,
    )
