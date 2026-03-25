"""Nosis backward don't-care propagation.

Identifies nets whose values are partially or fully unobserved due to
downstream masking logic (AND gates, MUX selectors). When a net's value
is don't-care in certain states, it can be set to a convenient value
(typically zero or matching another net) to enable further merging.

This is the dual of forward constant propagation, derived from the
duality principle of stable categories: any optimization on the forward
logic cone automatically has a dual on the backward observation cone.
"""

from __future__ import annotations

from nosis.ir import Cell, Module, Net, PrimOp

__all__ = [
    "propagate_dont_cares",
]


def propagate_dont_cares(mod: Module) -> int:
    """Propagate don't-care conditions backward from outputs.

    For each AND(mask, data) cell, if the mask net is driven by a
    NOT(sel) where sel is an EQ comparison output, the data net is
    don't-care whenever sel=1 (the case is active). In those states,
    the data net can be replaced with zero without changing the AND output.

    More generally: when a net feeds only AND cells where it is always
    paired with a mask, the net's value outside the mask's active window
    is irrelevant. If the net is an FF output, the FF can be simplified
    to only compute the value needed within the observation window.

    This pass identifies FFs whose outputs are always AND-masked, and
    replaces the AND(mask, ff_q) pattern with AND(mask, simplified_value)
    when the simplified value requires fewer LUTs.

    Returns the number of cells simplified.
    """
    simplified = 0

    # Find FFs whose output is ONLY consumed through AND(NOT(sel), ff_q)
    # For these FFs, the value when sel=1 is don't-care.
    ff_masks: dict[str, list[tuple[Cell, Net]]] = {}  # ff_name -> [(and_cell, mask_net)]

    for cell in mod.cells.values():
        if cell.op != PrimOp.FF:
            continue
        q_nets = list(cell.outputs.values())
        if not q_nets:
            continue
        q_net = q_nets[0]

        all_masked = True
        masks: list[tuple[Cell, Net]] = []

        for other in mod.cells.values():
            if other.op != PrimOp.AND:
                for pn, pnet in other.inputs.items():
                    if pnet is q_net:
                        all_masked = False
                        break
                if not all_masked:
                    break
                continue

            # Check if this AND uses q_net as one input and NOT(something) as the other
            a = other.inputs.get("A")
            b = other.inputs.get("B")
            if a is q_net:
                mask = b
            elif b is q_net:
                mask = a
            else:
                continue

            if mask and mask.driver and mask.driver.op == PrimOp.NOT:
                masks.append((other, mask))
            else:
                all_masked = False
                break

        if all_masked and masks:
            ff_masks[cell.name] = masks

    # For each fully-masked FF, check if the FF's D input logic can be simplified.
    # The key insight: AND(NOT(sel), ff_q) = 0 when sel=1, and = ff_q when sel=0.
    # The FF only needs to hold the correct value when sel=0.
    # If the FF's D input is itself a MUX(sel, new_value, hold_value),
    # and hold_value = ff_q (the feedback), then:
    #   - When sel=0: ff_next = hold_value = ff_q (no change)
    #   - When sel=1: ff_next = new_value
    # But the output is masked when sel=1, so new_value doesn't matter!
    # The FF can be replaced with a constant (its reset value).

    for ff_name, masks in ff_masks.items():
        ff_cell = mod.cells.get(ff_name)
        if ff_cell is None:
            continue

        d_net = ff_cell.inputs.get("D")
        if d_net is None or d_net.driver is None:
            continue

        q_net = list(ff_cell.outputs.values())[0]

        # Check if D is driven by a MUX
        d_driver = d_net.driver
        if d_driver.op == PrimOp.MUX:
            mux_a = d_driver.inputs.get("A")  # false branch (sel=0)
            d_driver.inputs.get("B")  # true branch (sel=1)
            mux_s = d_driver.inputs.get("S")

            # Check if the mask is NOT(mux_s) — same selector
            for and_cell, mask_net in masks:
                not_cell = mask_net.driver
                not_input = not_cell.inputs.get("A")
                if not_input is mux_s:
                    # The MUX selector matches the AND mask selector.
                    # When sel=0: FF holds, AND passes ff_q through.
                    # When sel=1: FF updates to mux_b, AND masks to 0.
                    # The mux_b value is never observed!
                    # If mux_a is the feedback (hold value = ff_q),
                    # the FF never changes observable state — it's constant.
                    if mux_a is q_net:
                        # FF holds its value when sel=0, masked when sel=1.
                        # It's effectively constant at its reset value.
                        # Replace the FF with a constant 0.
                        for consumer_and, _ in masks:
                            for pn, pnet in list(consumer_and.inputs.items()):
                                if pnet is q_net:
                                    # Replace with zero — the AND output is
                                    # already zero when mask=0
                                    consumer_and.inputs.clear()
                                    consumer_and.op = PrimOp.CONST
                                    consumer_and.params = {"value": 0, "width": q_net.width}
                                    simplified += 1
                        break

    return simplified
