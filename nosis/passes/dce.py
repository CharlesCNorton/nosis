"""Dead code elimination pass."""

from __future__ import annotations

from nosis.ir import Module, PrimOp

__all__ = ["dead_code_eliminate"]


def _find_live_nets(mod: Module) -> set[str]:
    """Find all nets reachable from outputs and FF inputs (backward from sinks)."""
    live: set[str] = set()
    worklist: list[str] = []

    # Seeds: output ports, FF data inputs, and MEMORY cells
    for cell in mod.cells.values():
        if cell.op == PrimOp.OUTPUT:
            for net in cell.inputs.values():
                if net.name not in live:
                    live.add(net.name)
                    worklist.append(net.name)
        elif cell.op == PrimOp.FF:
            for port_name, net in cell.inputs.items():
                if net.name not in live:
                    live.add(net.name)
                    worklist.append(net.name)
        elif cell.op == PrimOp.MEMORY:
            # MEMORY cells are stateful — all their inputs and outputs are live
            for net in cell.inputs.values():
                if net.name not in live:
                    live.add(net.name)
                    worklist.append(net.name)
            for net in cell.outputs.values():
                if net.name not in live:
                    live.add(net.name)
                    worklist.append(net.name)

    # Also seed any net that is a module port
    for name in mod.ports:
        if name not in live:
            live.add(name)
            worklist.append(name)

    # Backward reachability
    while worklist:
        net_name = worklist.pop()
        net = mod.nets.get(net_name)  # type: ignore[assignment]
        if net is None or net.driver is None:
            continue
        driver = net.driver
        for input_net in driver.inputs.values():
            if input_net.name not in live:
                live.add(input_net.name)
                worklist.append(input_net.name)

    return live


def dead_code_eliminate(mod: Module) -> int:
    """Remove cells and nets not reachable from outputs.

    Returns the number of cells removed.
    """
    live_nets = _find_live_nets(mod)
    removed = 0

    # Find dead cells: cells whose outputs are all dead
    dead_cells: list[str] = []
    for cell in mod.cells.values():
        if cell.op in (PrimOp.OUTPUT, PrimOp.INPUT):
            continue
        if not cell.outputs:
            dead_cells.append(cell.name)
            continue
        all_dead = all(net.name not in live_nets for net in cell.outputs.values())
        if all_dead:
            dead_cells.append(cell.name)

    for name in dead_cells:
        del mod.cells[name]
        removed += 1

    # Remove dead nets
    dead_nets = [name for name in mod.nets if name not in live_nets]
    for name in dead_nets:
        del mod.nets[name]

    return removed
