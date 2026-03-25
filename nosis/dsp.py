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
    "detect_mac",
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
            # Signedness: check if the multiply inputs come from SEXT cells
            a_signed = False
            b_signed = False
            if a_net.driver and a_net.driver.op == PrimOp.SEXT:
                a_signed = True
            if b_net.driver and b_net.driver.op == PrimOp.SEXT:
                b_signed = True
            cell.params["dsp_signed_a"] = a_signed
            cell.params["dsp_signed_b"] = b_signed
            tagged += 1
        elif a_width <= 36 and b_width <= 36:
            # Can be decomposed into 4x MULT18X18D with addition
            cell.params["dsp_config"] = "MULT18X18D_DECOMPOSED"
            cell.params["dsp_count"] = 4
            tagged += 1

    return tagged


def detect_mac(mod: Module) -> int:
    """Detect multiply-accumulate patterns: acc += a * b.

    A MAC pattern is a MUL cell whose output feeds an ADD cell, where
    the ADD's other input comes from an FF that feeds back from the
    ADD output. This pattern maps to ALU54B on ECP5 (multiply + accumulate
    in a single DSP tile).

    Tags the MUL cell with ``dsp_mac=True`` and ``dsp_acc_add`` and
    ``dsp_acc_ff`` params. Returns the number of MAC patterns detected.
    """
    detected = 0

    # Build consumer map: net_name -> [(cell, port)]
    net_consumers: dict[str, list[tuple[Cell, str]]] = {}
    for cell in mod.cells.values():
        for port, net in cell.inputs.items():
            if net.name not in net_consumers:
                net_consumers[net.name] = []
            net_consumers[net.name].append((cell, port))

    for cell in mod.cells.values():
        if cell.op != PrimOp.MUL:
            continue

        # MUL output must feed exactly one ADD
        mul_outs = list(cell.outputs.values())
        if not mul_outs:
            continue
        mul_out = mul_outs[0]

        consumers = net_consumers.get(mul_out.name, [])
        add_consumers = [(c, p) for c, p in consumers if c.op == PrimOp.ADD]
        if len(add_consumers) != 1:
            continue

        add_cell, add_port = add_consumers[0]
        # The other ADD input should come from an FF whose D is the ADD output
        other_port = "B" if add_port == "A" else "A"
        other_net = add_cell.inputs.get(other_port)
        if other_net is None:
            continue

        # Check if other_net is driven by an FF
        if other_net.driver is None or other_net.driver.op != PrimOp.FF:
            continue
        acc_ff = other_net.driver

        # The FF's D input should be the ADD output (feedback loop)
        ff_d = acc_ff.inputs.get("D")
        add_outs = list(add_cell.outputs.values())
        if not add_outs or ff_d is None:
            continue

        add_out = add_outs[0]
        if ff_d.name != add_out.name:
            continue

        # MAC pattern confirmed: MUL -> ADD -> FF -> (back to ADD)
        cell.params["dsp_mac"] = True
        cell.params["dsp_acc_add"] = add_cell.name
        cell.params["dsp_acc_ff"] = acc_ff.name
        detected += 1

    return detected
