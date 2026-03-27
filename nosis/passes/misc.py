"""Miscellaneous optimization passes: EQ width narrowing, carry annotation."""

from __future__ import annotations

from nosis.ir import Module, PrimOp

__all__ = ["annotate_eq_carry"]


def _narrow_eq_width(mod: Module) -> int:
    """Reduce EQ comparison width when comparing against constants with leading zeros.

    EQ(6-bit A, 6-bit 3) = EQ(2-bit A[1:0], 2-bit 3) AND NOR(A[5:2]).
    The 2-bit EQ uses 1 LUT. The 4-bit NOR uses 1 LUT. The AND uses 0
    extra LUTs (absorbed into the consumer). Total: 2 LUTs instead of 3.

    For the general case: split into a narrow EQ on the significant bits
    plus a REDUCE_OR (NOR) on the upper zero bits, ANDed together.
    """
    narrowed = 0
    _counter = [len(mod.nets) + len(mod.cells) + 100]

    def _name(prefix: str) -> str:
        n = f"${prefix}_{_counter[0]}"
        _counter[0] += 1
        return n

    for cell in list(mod.cells.values()):
        if cell.op != PrimOp.EQ:
            continue
        a_net = cell.inputs.get("A")
        b_net = cell.inputs.get("B")
        if not a_net or not b_net:
            continue
        if not b_net.driver or b_net.driver.op != PrimOp.CONST:
            continue

        bv = int(b_net.driver.params.get("value", 0))
        w = a_net.width
        if w <= 2:
            continue  # already minimal

        effective_w = max(bv.bit_length(), 1)
        # Only profitable when we save at least 2 LUTs from the EQ width reduction
        # minus the 3 new cells (SLICE, REDUCE_OR+NOT, AND).
        # EQ savings: ceil(w/2) - ceil(effective_w/2)
        # Cost: 3 cells (SLICE is wiring-only, REDUCE_OR ~1 LUT, NOT ~1 LUT, AND ~0 shared)
        # So: ceil(w/2) - ceil(effective_w/2) > 2
        saved_luts = (w + 1) // 2 - (effective_w + 1) // 2
        if saved_luts <= 3:
            continue  # not enough savings

        # Split: narrow EQ on low bits + REDUCE_OR on high bits
        # The narrow EQ checks A[effective_w-1:0] == bv[effective_w-1:0]
        # The high check verifies A[w-1:effective_w] == 0 (all zero)

        # Create SLICE for low bits
        low_net = mod.add_net(_name("eq_lo"), effective_w)
        low_slice = mod.add_cell(_name("eq_lo_sl"), PrimOp.SLICE, offset=0, width=effective_w)
        mod.connect(low_slice, "A", a_net)
        mod.connect(low_slice, "Y", low_net, direction="output")

        # Create CONST for low comparison value
        low_const_net = mod.add_net(_name("eq_lo_c"), effective_w)
        low_const = mod.add_cell(_name("eq_lo_cc"), PrimOp.CONST,
                                 value=bv & ((1 << effective_w) - 1), width=effective_w)
        mod.connect(low_const, "Y", low_const_net, direction="output")

        # Narrow EQ
        low_eq_net = mod.add_net(_name("eq_lo_r"), 1)
        low_eq = mod.add_cell(_name("eq_lo_eq"), PrimOp.EQ)
        mod.connect(low_eq, "A", low_net)
        mod.connect(low_eq, "B", low_const_net)
        mod.connect(low_eq, "Y", low_eq_net, direction="output")

        # Create SLICE for high bits
        high_w = w - effective_w
        high_net = mod.add_net(_name("eq_hi"), high_w)
        high_slice = mod.add_cell(_name("eq_hi_sl"), PrimOp.SLICE, offset=effective_w, width=high_w)
        mod.connect(high_slice, "A", a_net)
        mod.connect(high_slice, "Y", high_net, direction="output")

        # REDUCE_OR on high bits (should be 0 → NOR result is 1)
        high_or_net = mod.add_net(_name("eq_hi_or"), 1)
        high_or = mod.add_cell(_name("eq_hi_or"), PrimOp.REDUCE_OR)
        mod.connect(high_or, "A", high_net)
        mod.connect(high_or, "Y", high_or_net, direction="output")

        # NOT the REDUCE_OR (we want all-zero = 1)
        high_nor_net = mod.add_net(_name("eq_hi_nor"), 1)
        high_nor = mod.add_cell(_name("eq_hi_nor"), PrimOp.NOT)
        mod.connect(high_nor, "A", high_or_net)
        mod.connect(high_nor, "Y", high_nor_net, direction="output")

        # AND low_eq and high_nor
        out_nets = list(cell.outputs.values())
        if not out_nets:
            continue

        # Rewrite the original EQ cell as AND(low_eq, high_nor)
        cell.op = PrimOp.AND
        cell.inputs.clear()
        cell.inputs["A"] = low_eq_net
        cell.inputs["B"] = high_nor_net
        narrowed += 1

    return narrowed


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
