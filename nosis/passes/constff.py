"""Remove FFs with constant D inputs."""

from __future__ import annotations

from nosis.ir import Module, PrimOp

__all__ = ["remove_const_ffs"]


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
