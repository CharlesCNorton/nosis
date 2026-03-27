"""Identity and absorbing simplification pass."""

from __future__ import annotations

import nosis.passes as _passes_pkg
from nosis.ir import Cell, Module, PrimOp

__all__ = ["identity_simplify"]


def _is_const_cell(cell: Cell) -> bool:
    return cell.op == PrimOp.CONST


def _const_value(cell: Cell) -> int | None:
    if cell.op == PrimOp.CONST:
        return int(cell.params.get("value", 0))
    return None


def identity_simplify(mod: Module) -> int:
    """Simplify identity and absorbing operations where one input is constant.

    Handles patterns like:
      a & all_ones  -> a           a & 0 -> 0
      a | 0         -> a           a | all_ones -> all_ones
      a ^ 0         -> a           a ^ a -> 0 (when detectable)
      NOT(NOT(a))   -> a
      a + 0         -> a
      a - 0         -> a
      a * 1         -> a           a * 0 -> 0
      a << 0        -> a
      a >> 0        -> a
      MUX(0, a, b)  -> a           MUX(1, a, b) -> b

    Returns the number of cells simplified.
    """
    simplified = 0
    to_bypass: list[tuple[str, str]] = []  # (cell_name, replacement_net_name)
    to_const: list[tuple[str, int, int]] = []  # (cell_name, value, width)

    for cell in mod.cells.values():
        if cell.op in (PrimOp.INPUT, PrimOp.OUTPUT, PrimOp.FF, PrimOp.CONST):
            continue
        # Don't simplify cells in the memory fanout cone
        if _passes_pkg._active_mem_protect and any(net.name in _passes_pkg._active_mem_protect for net in cell.outputs.values()):
            continue

        out_nets = list(cell.outputs.values())
        if not out_nets:
            continue
        out_width = out_nets[0].width
        all_ones = (1 << out_width) - 1

        a_net = cell.inputs.get("A")
        b_net = cell.inputs.get("B")
        s_net = cell.inputs.get("S")

        a_const = _const_value(a_net.driver) if a_net and a_net.driver and _is_const_cell(a_net.driver) else None
        b_const = _const_value(b_net.driver) if b_net and b_net.driver and _is_const_cell(b_net.driver) else None
        s_const = _const_value(s_net.driver) if s_net and s_net.driver and _is_const_cell(s_net.driver) else None

        if cell.op == PrimOp.AND:
            if a_const is not None and (a_const & all_ones) == all_ones and b_net:
                to_bypass.append((cell.name, b_net.name))
            elif b_const is not None and (b_const & all_ones) == all_ones and a_net:
                to_bypass.append((cell.name, a_net.name))
            elif a_const == 0 or b_const == 0:
                to_const.append((cell.name, 0, out_width))
        elif cell.op == PrimOp.OR:
            if a_const == 0 and b_net:
                to_bypass.append((cell.name, b_net.name))
            elif b_const == 0 and a_net:
                to_bypass.append((cell.name, a_net.name))
            elif (a_const is not None and (a_const & all_ones) == all_ones) or \
                 (b_const is not None and (b_const & all_ones) == all_ones):
                to_const.append((cell.name, all_ones, out_width))
        elif cell.op == PrimOp.XOR:
            if a_const == 0 and b_net:
                to_bypass.append((cell.name, b_net.name))
            elif b_const == 0 and a_net:
                to_bypass.append((cell.name, a_net.name))
        elif cell.op == PrimOp.ADD:
            if a_const == 0 and b_net:
                to_bypass.append((cell.name, b_net.name))
            elif b_const == 0 and a_net:
                to_bypass.append((cell.name, a_net.name))
        elif cell.op == PrimOp.SUB:
            if b_const == 0 and a_net:
                to_bypass.append((cell.name, a_net.name))
        elif cell.op == PrimOp.MUL:
            if a_const == 1 and b_net:
                to_bypass.append((cell.name, b_net.name))
            elif b_const == 1 and a_net:
                to_bypass.append((cell.name, a_net.name))
            elif a_const == 0 or b_const == 0:
                to_const.append((cell.name, 0, out_width))
        elif cell.op in (PrimOp.SHL, PrimOp.SHR, PrimOp.SSHR):
            if b_const == 0 and a_net:
                to_bypass.append((cell.name, a_net.name))
        elif cell.op == PrimOp.MUX:
            if s_const == 0 and a_net:
                to_bypass.append((cell.name, a_net.name))
            elif s_const == 1 and b_net:
                to_bypass.append((cell.name, b_net.name))
            elif a_net and b_net and a_net is b_net:
                # MUX(cond, x, x) = x — both branches identical
                to_bypass.append((cell.name, a_net.name))
            elif a_net and b_net and a_net.driver is not None and b_net.driver is not None and a_net.driver is b_net.driver:
                # Both branches driven by the same cell — same value
                to_bypass.append((cell.name, a_net.name))
        elif cell.op == PrimOp.NOT:
            # NOT(NOT(a)) -> a
            if a_net and a_net.driver and a_net.driver.op == PrimOp.NOT:
                inner_a = a_net.driver.inputs.get("A")
                if inner_a:
                    to_bypass.append((cell.name, inner_a.name))

    # Apply bypasses: redirect all consumers of the cell's output to the source net.
    # This includes other cells' inputs AND module ports that reference the output net.
    for cell_name, src_net_name in to_bypass:
        cell = mod.cells[cell_name]
        src_net = mod.nets.get(src_net_name)
        if src_net is None:
            continue
        for out_net in list(cell.outputs.values()):
            # Redirect every consumer cell that reads out_net
            for other_cell in mod.cells.values():
                if other_cell is cell:
                    continue
                for port, net in list(other_cell.inputs.items()):
                    if net is out_net:
                        other_cell.inputs[port] = src_net
            # Redirect module ports that reference out_net
            for port_name, port_net in list(mod.ports.items()):
                if port_net is out_net:
                    mod.ports[port_name] = src_net
            # Update the net's driver to maintain the graph invariant
            out_net.driver = src_net.driver
        cell.inputs.clear()
        cell.outputs.clear()
        cell.op = PrimOp.CONST
        cell.params = {"value": 0, "width": 1, "_dead": True}
        simplified += 1

    for cell_name, value, width in to_const:
        cell = mod.cells[cell_name]
        cell.inputs.clear()
        cell.op = PrimOp.CONST
        cell.params = {"value": value, "width": width}
        simplified += 1

    return simplified
