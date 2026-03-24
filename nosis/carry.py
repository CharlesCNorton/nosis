"""Nosis carry chain inference — recognize addition/subtraction for CCU2C mapping.

ECP5 CCU2C is a 2-bit carry chain unit integrated into TRELLIS_SLICE.
Each CCU2C contains two full adders with carry propagation. An N-bit
adder uses ceil(N/2) CCU2C cells chained together.

This pass identifies ADD and SUB cells in the IR and tags them for
carry chain mapping instead of per-bit LUT decomposition.
"""

from __future__ import annotations

from nosis.ir import Cell, Module, PrimOp

__all__ = [
    "infer_carry_chains",
]


def infer_carry_chains(mod: Module) -> int:
    """Tag ADD and SUB cells for CCU2C carry chain mapping.

    Adds ``carry_config`` to cell params.
    Returns the number of cells tagged.
    """
    tagged = 0

    for cell in mod.cells.values():
        if cell.op not in (PrimOp.ADD, PrimOp.SUB):
            continue

        a_net = cell.inputs.get("A")
        b_net = cell.inputs.get("B")
        if a_net is None or b_net is None:
            continue

        width = max(a_net.width, b_net.width)
        out_nets = list(cell.outputs.values())
        if out_nets:
            width = max(width, out_nets[0].width)

        if width < 2:
            continue  # single-bit: LUT is fine

        ccu2c_count = (width + 1) // 2  # 2 bits per CCU2C
        cell.params["carry_config"] = "CCU2C"
        cell.params["carry_width"] = width
        cell.params["carry_ccu2c_count"] = ccu2c_count
        cell.params["carry_is_sub"] = (cell.op == PrimOp.SUB)
        tagged += 1

    return tagged
