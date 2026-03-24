"""Nosis DSP inference — recognize multiply patterns and emit MULT18X18D.

Scans the IR for MUL cells and determines whether they can be mapped
to ECP5 MULT18X18D hard multiplier blocks.

MULT18X18D:
  - 18x18 signed or unsigned multiply
  - Optional input and output registers
  - ECP5-25F has 28 available
"""

from __future__ import annotations

from nosis.ir import Cell, Module, PrimOp

__all__ = [
    "infer_dsps",
]


def infer_dsps(mod: Module) -> int:
    """Tag MUL cells that should become MULT18X18D instances.

    Adds ``dsp_config`` to cell params for multiplies that fit.
    Returns the number of multiplies tagged.
    """
    tagged = 0

    for cell in mod.cells.values():
        if cell.op != PrimOp.MUL:
            continue

        a_net = cell.inputs.get("A")
        b_net = cell.inputs.get("B")
        if a_net is None or b_net is None:
            continue

        a_width = a_net.width
        b_width = b_net.width

        # MULT18X18D handles up to 18x18
        if a_width <= 18 and b_width <= 18:
            cell.params["dsp_config"] = "MULT18X18D"
            cell.params["dsp_a_width"] = a_width
            cell.params["dsp_b_width"] = b_width
            cell.params["dsp_signed"] = False  # TODO: track signedness from IR
            tagged += 1
        elif a_width <= 36 and b_width <= 36:
            # Can be decomposed into 4x MULT18X18D with addition
            cell.params["dsp_config"] = "MULT18X18D_DECOMPOSED"
            cell.params["dsp_count"] = 4
            tagged += 1

    return tagged
