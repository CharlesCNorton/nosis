"""Nosis common subexpression elimination — deduplicate identical operations.

When two cells compute the same operation on the same input nets, one
is redundant. This pass identifies duplicates by hashing (op, input_nets)
and redirects all consumers of the duplicate to the original's output.
"""

from __future__ import annotations

from nosis.ir import Cell, Module, PrimOp

__all__ = [
    "eliminate_common_subexpressions",
]

# Operations safe to deduplicate (deterministic, no side effects)
_CSE_OPS = {
    PrimOp.AND, PrimOp.OR, PrimOp.XOR, PrimOp.NOT,
    PrimOp.ADD, PrimOp.SUB, PrimOp.MUL, PrimOp.DIV, PrimOp.MOD,
    PrimOp.SHL, PrimOp.SHR, PrimOp.SSHR,
    PrimOp.EQ, PrimOp.NE, PrimOp.LT, PrimOp.LE, PrimOp.GT, PrimOp.GE,
    PrimOp.MUX, PrimOp.REDUCE_AND, PrimOp.REDUCE_OR, PrimOp.REDUCE_XOR,
    PrimOp.CONCAT, PrimOp.SLICE, PrimOp.ZEXT, PrimOp.SEXT, PrimOp.REPEAT,
}


def _cell_signature(cell: Cell) -> tuple | None:
    """Compute a hashable signature for a cell. Returns None if not CSE-eligible."""
    if cell.op not in _CSE_OPS:
        return None
    input_key = tuple(sorted((port, net.name) for port, net in cell.inputs.items()))
    try:
        param_key = tuple(sorted(
            (k, v if not isinstance(v, list) else tuple(v))
            for k, v in cell.params.items()
            if k not in ("packed", "packed_lut_init", "fsm_state", "fsm_encoding",
                          "fsm_num_states", "fsm_transition", "_bdd_absorbable",
                          "eq_carry", "eq_carry_width")
        ))
    except TypeError:
        return None
    return (cell.op, input_key, param_key)


def eliminate_common_subexpressions(mod: Module) -> int:
    """Remove duplicate cells that compute the same operation on the same inputs.

    Returns the number of cells eliminated.
    """
    # Build signature -> first cell with that signature
    sig_to_cell: dict[tuple, Cell] = {}
    to_redirect: list[tuple[Cell, Cell]] = []  # (duplicate, original)

    for cell in mod.cells.values():
        if cell.attributes.get("keep"):
            continue  # (* keep *) cells must not be merged
        sig = _cell_signature(cell)
        if sig is None:
            continue
        if sig in sig_to_cell:
            to_redirect.append((cell, sig_to_cell[sig]))
        else:
            sig_to_cell[sig] = cell

    # Redirect: for each duplicate, point its output net's driver to the original
    eliminated = 0
    to_remove: set[str] = set()

    for dup, orig in to_redirect:
        dup_outs = list(dup.outputs.values())
        orig_outs = list(orig.outputs.values())
        if not dup_outs or not orig_outs:
            continue
        if dup_outs[0].width != orig_outs[0].width:
            continue

        # Redirect all consumers of dup's output to orig's output
        dup_out = dup_outs[0]
        orig_out = orig_outs[0]

        # Find all cells that use dup_out as input and rewire to orig_out
        for other_cell in mod.cells.values():
            if other_cell.name == dup.name:
                continue
            for port_name, net in list(other_cell.inputs.items()):
                if net is dup_out:
                    other_cell.inputs[port_name] = orig_out

        to_remove.add(dup.name)
        eliminated += 1

    for name in to_remove:
        del mod.cells[name]

    return eliminated
