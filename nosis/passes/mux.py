"""MUX chain merging."""

from __future__ import annotations

from nosis.ir import Cell, Module, PrimOp

__all__ = ["merge_mux_chains"]


def merge_mux_chains(mod: Module) -> int:
    """Deduplicate EQ cells that share the same (selector, constant) pair.

    In case statements, the lowering often produces duplicate EQ cells
    for the same comparison across different target registers. CSE
    catches exact duplicates, but after optimization the structure may
    have diverged enough that CSE misses them.

    Also eliminates MUX cells where both branches are identical.

    Returns the number of cells eliminated.
    """
    eliminated = 0
    from collections import defaultdict

    # Group EQs by (A_net, B_const_value)
    eq_groups: dict[tuple[str, int], list[Cell]] = defaultdict(list)
    for cell in mod.cells.values():
        if cell.op != PrimOp.EQ:
            continue
        a = cell.inputs.get("A")
        b = cell.inputs.get("B")
        if a is None or b is None:
            continue
        if b.driver is None or b.driver.op != PrimOp.CONST:
            continue
        b_val = int(b.driver.params.get("value", 0))
        eq_groups[(a.name, b_val)].append(cell)

    # Build consumer index for efficient redirect
    _consumer_idx: dict[int, list[tuple[Cell, str]]] = {}
    for cell in mod.cells.values():
        for pname, pnet in cell.inputs.items():
            _consumer_idx.setdefault(id(pnet), []).append((cell, pname))

    to_remove: set[str] = set()
    for key, cells in eq_groups.items():
        if len(cells) < 2:
            continue
        keeper = cells[0]
        keeper_out = list(keeper.outputs.values())
        if not keeper_out:
            continue
        keeper_out_net = keeper_out[0]

        for dup in cells[1:]:
            dup_out = list(dup.outputs.values())
            if not dup_out:
                continue
            dup_out_net = dup_out[0]
            for consumer, pname in _consumer_idx.get(id(dup_out_net), []):
                if consumer is not dup:
                    consumer.inputs[pname] = keeper_out_net
            to_remove.add(dup.name)
            eliminated += 1

    for name in to_remove:
        if name in mod.cells:
            del mod.cells[name]

    # Second pass: eliminate MUX cells where both branches are identical
    to_bypass: list[tuple[str, str]] = []
    for cell in mod.cells.values():
        if cell.op != PrimOp.MUX:
            continue
        a_net = cell.inputs.get("A")
        b_net = cell.inputs.get("B")
        if a_net and b_net and a_net is b_net:
            out_nets = list(cell.outputs.values())
            if out_nets:
                to_bypass.append((cell.name, a_net.name))

    # Rebuild index after EQ dedup (cells changed)
    _bypass_idx: dict[int, list[tuple[Cell, str]]] = {}
    for cell in mod.cells.values():
        for pn, pnet in cell.inputs.items():
            _bypass_idx.setdefault(id(pnet), []).append((cell, pn))

    for cell_name, src_name in to_bypass:
        cell = mod.cells[cell_name]
        src_net = mod.nets.get(src_name)
        if src_net is None:
            continue
        for out_net in list(cell.outputs.values()):
            for consumer, pn in _bypass_idx.get(id(out_net), []):
                if consumer is not cell:
                    consumer.inputs[pn] = src_net
            for port_name, port_net in list(mod.ports.items()):
                if port_net is out_net:
                    mod.ports[port_name] = src_net
            out_net.driver = src_net.driver
        cell.inputs.clear()
        cell.outputs.clear()
        cell.op = PrimOp.CONST
        cell.params = {"value": 0, "width": 1, "_dead": True}
        eliminated += 1

    return eliminated
