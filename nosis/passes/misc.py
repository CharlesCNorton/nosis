"""Carry chain annotation for EQ comparisons."""

from __future__ import annotations

from nosis.ir import Module, PrimOp

__all__ = ["annotate_eq_carry"]


def annotate_eq_carry(mod: Module) -> int:
    """Annotate EQ comparisons against constants for carry chain mapping.

    Each ``state == 4'd3`` comparison can use a CCU2C equality chain
    (2 bits per cell) instead of N LUT4 XOR cells + reduce-AND tree.
    This pass adds ``eq_carry=True`` to EQ cells that compare a
    multi-bit net against a constant, enabling techmap to use CCU2C.

    Returns the number of EQ cells annotated.
    """
    annotated = 0
    for cell in mod.cells.values():
        if cell.op != PrimOp.EQ:
            continue
        a_net = cell.inputs.get("A")
        b_net = cell.inputs.get("B")
        if a_net is None or b_net is None:
            continue
        # One operand must be a constant
        is_const_b = b_net.driver and b_net.driver.op == PrimOp.CONST
        is_const_a = a_net.driver and a_net.driver.op == PrimOp.CONST
        if not (is_const_a or is_const_b):
            continue
        # The non-constant operand must be multi-bit
        var_net = a_net if is_const_b else b_net
        if var_net.width < 4:  # CCU2C needs at least 4 bits to be worthwhile
            continue
        cell.params["eq_carry"] = True
        cell.params["eq_carry_width"] = var_net.width
        annotated += 1

    return annotated
