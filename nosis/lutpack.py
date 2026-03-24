"""Nosis LUT packing — merge cascaded small operations into single LUT4 cells.

A LUT4 has 4 inputs and 1 output. Many IR operations use only 2 inputs
(AND, OR, XOR, etc.). When two such operations are chained (output of
one feeds input of another), the entire function can fit in a single
LUT4 instead of two.

This pass runs on the IR level (before tech mapping) to merge cascaded
2-input operations. It is more effective at this level because the
cell-to-net connectivity is explicit.
"""

from __future__ import annotations

from nosis.ir import Cell, Module, PrimOp
from nosis.eval import eval_const_op

__all__ = [
    "pack_luts_ir",
]


# Operations that use exactly 2 inputs (A, B) and produce 1 output (Y)
_PACKABLE_OPS = {
    PrimOp.AND, PrimOp.OR, PrimOp.XOR, PrimOp.EQ, PrimOp.NE,
}


def _compute_composed_truth_table(
    outer_op: PrimOp,
    inner_op: PrimOp,
    inner_on_a: bool,
) -> int:
    """Compute 16-bit LUT4 truth table for composed function.

    inner_on_a: True means outer.A is driven by inner's output.
    The composed LUT4 has: A=inner.A, B=inner.B, C=outer.other_input, D=0.
    """
    init = 0
    for i in range(16):
        a = (i >> 0) & 1  # inner input A
        b = (i >> 1) & 1  # inner input B
        c = (i >> 2) & 1  # outer other input
        # d = (i >> 3) & 1  # unused

        inner_result = eval_const_op(inner_op, {"A": a, "B": b}, {}, 1)
        if inner_result is None:
            inner_result = 0

        if inner_on_a:
            outer_result = eval_const_op(outer_op, {"A": inner_result, "B": c}, {}, 1)
        else:
            outer_result = eval_const_op(outer_op, {"A": c, "B": inner_result}, {}, 1)
        if outer_result is None:
            outer_result = 0

        if outer_result & 1:
            init |= (1 << i)

    return init


def pack_luts_ir(mod: Module) -> int:
    """Merge cascaded 2-input logic operations in the IR.

    When cell A feeds exactly one consumer cell B, and both are
    packable 2-input operations, replace them with a single cell
    that computes the composed function.

    Returns the number of cells eliminated.
    """
    packed = 0

    # Build consumer map: net_name -> list of (cell, port_name)
    net_consumers: dict[str, list[tuple[Cell, str]]] = {}
    for cell in mod.cells.values():
        for port_name, net in cell.inputs.items():
            if net.name not in net_consumers:
                net_consumers[net.name] = []
            net_consumers[net.name].append((cell, port_name))

    to_remove: set[str] = set()
    changed = True

    while changed:
        changed = False

        for inner_cell in list(mod.cells.values()):
            if inner_cell.name in to_remove:
                continue
            if inner_cell.op not in _PACKABLE_OPS:
                continue

            # Inner cell must have exactly 1 output
            inner_outs = list(inner_cell.outputs.values())
            if len(inner_outs) != 1:
                continue
            inner_out_net = inner_outs[0]

            # That output must have exactly 1 consumer
            consumers = net_consumers.get(inner_out_net.name, [])
            live_consumers = [(c, p) for c, p in consumers if c.name not in to_remove]
            if len(live_consumers) != 1:
                continue

            outer_cell, outer_port = live_consumers[0]
            if outer_cell.name in to_remove:
                continue
            if outer_cell.op not in _PACKABLE_OPS:
                continue
            if outer_port not in ("A", "B"):
                continue

            # Both cells must have matching output width
            outer_outs = list(outer_cell.outputs.values())
            if not outer_outs:
                continue
            if inner_out_net.width != outer_outs[0].width:
                continue

            # Get the other input to the outer cell
            inner_on_a = (outer_port == "A")
            other_port = "B" if inner_on_a else "A"
            other_net = outer_cell.inputs.get(other_port)
            if other_net is None:
                continue

            inner_a = inner_cell.inputs.get("A")
            inner_b = inner_cell.inputs.get("B")
            if inner_a is None or inner_b is None:
                continue

            # Compute the composed truth table
            init = _compute_composed_truth_table(
                outer_cell.op, inner_cell.op, inner_on_a
            )

            # Replace outer cell: rewire to use inner's inputs + other
            outer_cell.inputs.clear()
            outer_cell.inputs["A"] = inner_a
            outer_cell.inputs["B"] = inner_b
            outer_cell.inputs["C"] = other_net
            # Store the composed truth table for tech mapping
            outer_cell.params["packed_lut_init"] = init
            outer_cell.params["packed"] = True

            # Mark inner cell for removal
            to_remove.add(inner_cell.name)
            packed += 1
            changed = True

    # Remove eliminated cells
    for name in to_remove:
        del mod.cells[name]

    return packed
