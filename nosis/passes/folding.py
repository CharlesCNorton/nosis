"""Constant folding pass."""

from __future__ import annotations

import nosis.passes as _passes_pkg
from nosis.eval import eval_const_op
from nosis.ir import Cell, Module, PrimOp

__all__ = ["constant_fold"]


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
    # Use the global memory protection set from run_default_passes.
    _mem_fanout = _passes_pkg._active_mem_protect

    folded = 0
    changed = True

    while changed:
        changed = False
        to_replace: list[tuple[str, int, int]] = []  # (cell_name, value, width)

        for cell in mod.cells.values():
            if _is_const_cell(cell):
                continue
            if cell.op in (PrimOp.INPUT, PrimOp.OUTPUT, PrimOp.FF, PrimOp.MEMORY):
                continue
            # Don't fold cells whose inputs or outputs are in the memory fanout cone
            if any(net.name in _mem_fanout for net in cell.inputs.values()):
                continue
            if any(net.name in _mem_fanout for net in cell.outputs.values()):
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
