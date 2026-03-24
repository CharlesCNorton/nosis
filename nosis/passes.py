"""Nosis optimization passes — transform the IR while preserving equivalence.

Each pass takes a Module and mutates it in place. Passes are composable
and idempotent: running a pass twice produces the same result as running
it once.
"""

from __future__ import annotations

from nosis.eval import eval_const_op
from nosis.ir import Cell, Module, Net, PrimOp

__all__ = [
    "constant_fold",
    "dead_code_eliminate",
    "run_default_passes",
]


def _is_const_cell(cell: Cell) -> bool:
    return cell.op == PrimOp.CONST


def _const_value(cell: Cell) -> int | None:
    if cell.op == PrimOp.CONST:
        return int(cell.params.get("value", 0))
    return None


def constant_fold(mod: Module) -> int:
    """Fold cells with all-constant inputs into CONST cells.

    Returns the number of cells folded.
    """
    folded = 0
    changed = True

    while changed:
        changed = False
        to_replace: list[tuple[str, int, int]] = []  # (cell_name, value, width)

        for cell in mod.cells.values():
            if _is_const_cell(cell):
                continue
            if cell.op in (PrimOp.INPUT, PrimOp.OUTPUT, PrimOp.FF):
                continue

            # Check if all inputs are driven by CONST cells
            const_inputs: dict[str, int] = {}
            all_const = True
            for port_name, net in cell.inputs.items():
                if net.driver is not None and _is_const_cell(net.driver):
                    const_inputs[port_name] = _const_value(net.driver) or 0
                else:
                    all_const = False
                    break

            if not all_const:
                continue

            # Get output width
            out_nets = list(cell.outputs.values())
            if not out_nets:
                continue
            width = out_nets[0].width

            # Try to evaluate using the shared evaluator
            try:
                result = eval_const_op(cell.op, const_inputs, cell.params, width)
            except Exception:
                result = None
            if result is not None:
                to_replace.append((cell.name, result, width))

        for cell_name, value, width in to_replace:
            cell = mod.cells[cell_name]
            # Convert to CONST: clear inputs, set params
            cell.inputs.clear()
            cell.op = PrimOp.CONST
            cell.params = {"value": value, "width": width}
            folded += 1
            changed = True

    return folded


# ---------------------------------------------------------------------------
# Dead code elimination
# ---------------------------------------------------------------------------

def _find_live_nets(mod: Module) -> set[str]:
    """Find all nets reachable from outputs and FF inputs (backward from sinks)."""
    live: set[str] = set()
    worklist: list[str] = []

    # Seeds: output ports and FF data inputs
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

    # Also seed any net that is a module port
    for name in mod.ports:
        if name not in live:
            live.add(name)
            worklist.append(name)

    # Backward reachability
    while worklist:
        net_name = worklist.pop()
        net = mod.nets.get(net_name)
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


# ---------------------------------------------------------------------------
# Default pass pipeline
# ---------------------------------------------------------------------------

def run_default_passes(mod: Module) -> dict[str, int]:
    """Run the default optimization pipeline. Returns pass statistics."""
    stats: dict[str, int] = {}
    stats["const_fold"] = constant_fold(mod)
    stats["dce"] = dead_code_eliminate(mod)
    # Second round after DCE may expose more constants
    stats["const_fold_2"] = constant_fold(mod)
    stats["dce_2"] = dead_code_eliminate(mod)
    return stats
