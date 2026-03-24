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
    "identity_simplify",
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
# Identity / absorbing simplification
# ---------------------------------------------------------------------------

def identity_simplify(mod: Module) -> int:
    """Simplify identity and absorbing operations where one input is constant.

    Handles patterns like:
      a & all_ones  -> a           a & 0 -> 0
      a | 0         -> a           a | all_ones -> all_ones
      a ^ 0         -> a           a ^ a -> 0 (when detectable)
      NOT(NOT(a))   -> a
      a + 0         -> a
      a - 0         -> a
      a * 1         -> a           a * 0 -> 0
      a << 0        -> a
      a >> 0        -> a
      MUX(0, a, b)  -> a           MUX(1, a, b) -> b

    Returns the number of cells simplified.
    """
    simplified = 0
    to_bypass: list[tuple[str, str]] = []  # (cell_name, replacement_net_name)
    to_const: list[tuple[str, int, int]] = []  # (cell_name, value, width)

    for cell in mod.cells.values():
        if cell.op in (PrimOp.INPUT, PrimOp.OUTPUT, PrimOp.FF, PrimOp.CONST):
            continue

        out_nets = list(cell.outputs.values())
        if not out_nets:
            continue
        out_width = out_nets[0].width
        all_ones = (1 << out_width) - 1

        a_net = cell.inputs.get("A")
        b_net = cell.inputs.get("B")
        s_net = cell.inputs.get("S")

        a_const = _const_value(a_net.driver) if a_net and a_net.driver and _is_const_cell(a_net.driver) else None
        b_const = _const_value(b_net.driver) if b_net and b_net.driver and _is_const_cell(b_net.driver) else None
        s_const = _const_value(s_net.driver) if s_net and s_net.driver and _is_const_cell(s_net.driver) else None

        if cell.op == PrimOp.AND:
            if a_const is not None and (a_const & all_ones) == all_ones and b_net:
                to_bypass.append((cell.name, b_net.name))
            elif b_const is not None and (b_const & all_ones) == all_ones and a_net:
                to_bypass.append((cell.name, a_net.name))
            elif a_const == 0 or b_const == 0:
                to_const.append((cell.name, 0, out_width))
        elif cell.op == PrimOp.OR:
            if a_const == 0 and b_net:
                to_bypass.append((cell.name, b_net.name))
            elif b_const == 0 and a_net:
                to_bypass.append((cell.name, a_net.name))
            elif (a_const is not None and (a_const & all_ones) == all_ones) or \
                 (b_const is not None and (b_const & all_ones) == all_ones):
                to_const.append((cell.name, all_ones, out_width))
        elif cell.op == PrimOp.XOR:
            if a_const == 0 and b_net:
                to_bypass.append((cell.name, b_net.name))
            elif b_const == 0 and a_net:
                to_bypass.append((cell.name, a_net.name))
        elif cell.op == PrimOp.ADD:
            if a_const == 0 and b_net:
                to_bypass.append((cell.name, b_net.name))
            elif b_const == 0 and a_net:
                to_bypass.append((cell.name, a_net.name))
        elif cell.op == PrimOp.SUB:
            if b_const == 0 and a_net:
                to_bypass.append((cell.name, a_net.name))
        elif cell.op == PrimOp.MUL:
            if a_const == 1 and b_net:
                to_bypass.append((cell.name, b_net.name))
            elif b_const == 1 and a_net:
                to_bypass.append((cell.name, a_net.name))
            elif a_const == 0 or b_const == 0:
                to_const.append((cell.name, 0, out_width))
        elif cell.op in (PrimOp.SHL, PrimOp.SHR, PrimOp.SSHR):
            if b_const == 0 and a_net:
                to_bypass.append((cell.name, a_net.name))
        elif cell.op == PrimOp.MUX:
            if s_const == 0 and a_net:
                to_bypass.append((cell.name, a_net.name))
            elif s_const == 1 and b_net:
                to_bypass.append((cell.name, b_net.name))
        elif cell.op == PrimOp.NOT:
            # NOT(NOT(a)) -> a
            if a_net and a_net.driver and a_net.driver.op == PrimOp.NOT:
                inner_a = a_net.driver.inputs.get("A")
                if inner_a:
                    to_bypass.append((cell.name, inner_a.name))

    # Apply bypasses: redirect all consumers of the cell's output to use the source net
    for cell_name, src_net_name in to_bypass:
        cell = mod.cells[cell_name]
        src_net = mod.nets.get(src_net_name)
        if src_net is None:
            continue
        for out_net in cell.outputs.values():
            # Redirect every consumer that reads out_net to read src_net instead
            for other_cell in mod.cells.values():
                if other_cell is cell:
                    continue
                for port, net in list(other_cell.inputs.items()):
                    if net is out_net:
                        other_cell.inputs[port] = src_net
            out_net.driver = src_net.driver
        cell.inputs.clear()
        cell.outputs.clear()
        cell.op = PrimOp.CONST
        cell.params = {"value": 0, "width": 1, "_dead": True}
        simplified += 1

    for cell_name, value, width in to_const:
        cell = mod.cells[cell_name]
        cell.inputs.clear()
        cell.op = PrimOp.CONST
        cell.params = {"value": value, "width": width}
        simplified += 1

    return simplified


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

def remove_const_ffs(mod: Module) -> int:
    """Remove FFs whose D input is driven by a constant.

    A FF with a constant D input will always hold the same value after
    reset. Replace its Q output connections with the constant value.
    Returns the number of FFs removed.
    """
    removed = 0
    to_remove: list[str] = []

    for cell in mod.cells.values():
        if cell.op != PrimOp.FF:
            continue
        d_net = cell.inputs.get("D")
        if d_net is None or d_net.driver is None:
            continue
        if d_net.driver.op != PrimOp.CONST:
            continue

        # D is constant — this FF always holds the same value
        # Replace the FF output with the constant
        q_nets = list(cell.outputs.values())
        if not q_nets:
            continue
        q_net = q_nets[0]

        # Point q_net's driver to the constant cell
        q_net.driver = d_net.driver
        to_remove.append(cell.name)
        removed += 1

    for name in to_remove:
        del mod.cells[name]

    return removed


def run_default_passes(mod: Module) -> dict[str, int]:
    """Run the default optimization pipeline. Returns pass statistics."""
    from nosis.cse import eliminate_common_subexpressions
    from nosis.boolopt import boolean_optimize

    stats: dict[str, int] = {}
    stats["const_fold"] = constant_fold(mod)
    stats["identity"] = identity_simplify(mod)
    stats["bool_opt"] = boolean_optimize(mod)
    stats["const_ff"] = remove_const_ffs(mod)
    stats["cse"] = eliminate_common_subexpressions(mod)
    stats["dce"] = dead_code_eliminate(mod)
    stats["const_fold_2"] = constant_fold(mod)
    stats["identity_2"] = identity_simplify(mod)
    stats["bool_opt_2"] = boolean_optimize(mod)
    stats["const_ff_2"] = remove_const_ffs(mod)
    stats["cse_2"] = eliminate_common_subexpressions(mod)
    stats["dce_2"] = dead_code_eliminate(mod)
    return stats
