"""ROM extraction: collapse constant-case mux chains into lookup tables.

A dense `case` over a w-bit selector with constant arm values (an S-box, a
sine table) lowers to a linear chain of `MUX(EQ(sel, k), rest, const_k)`
cells — 2^w EQ + 2^w MUX cells per lookup, which the optimizer then grinds
on and the mapper emits as a LUT chain thousands of cells long. This pass
recognizes such chains and replaces each with a single ROM cell
(`A = sel -> Y`, `params["values"]` holding the table), which the mapper
emits as the balanced form: one leaf LUT4 per output bit per low-selector
nibble, muxed by the high selector bits.

Chains are accepted when every link's select is `EQ(sel, const)` on one
common selector, every data leaf is constant, the selector is at most
ROM_MAX_SEL bits, and the chain is long enough that a table beats discrete
logic. Entries not covered by an arm take the chain's tail (default) value,
which must itself be constant.
"""

from __future__ import annotations

from nosis.ir import Cell, Module, Net, PrimOp

__all__ = ["extract_roms"]

ROM_MAX_SEL = 10       # tables up to 1024 entries
ROM_MIN_CHAIN = 32     # below this, plain logic is fine


def _const_of(net: Net | None) -> int | None:
    if net is None:
        return None
    dr = net.driver
    if dr is not None and dr.op == PrimOp.CONST:
        return int(dr.params.get("value", 0))
    return None


def extract_roms(mod: Module) -> int:
    """Replace constant-case mux chains with ROM cells.

    Returns the number of ROM cells created.
    """
    # Identify chain links: MUX whose S is EQ(sel, const) and whose B leaf
    # is a constant. links maps the mux's output net id -> details.
    links: dict[int, tuple[Cell, Net, int, int, Net]] = {}
    for cell in mod.cells.values():
        if cell.op != PrimOp.MUX:
            continue
        s = cell.inputs.get("S")
        a = cell.inputs.get("A")
        b = cell.inputs.get("B")
        if s is None or a is None or b is None:
            continue
        eq = s.driver
        if eq is None or eq.op != PrimOp.EQ:
            continue
        ea, eb = eq.inputs.get("A"), eq.inputs.get("B")
        if ea is None or eb is None:
            continue
        k = _const_of(eb)
        sel = ea
        if k is None:
            k = _const_of(ea)
            sel = eb
        if k is None or sel.width > ROM_MAX_SEL:
            continue
        v = _const_of(b)
        if v is None:
            continue
        out = next(iter(cell.outputs.values()), None)
        if out is None:
            continue
        links[id(out)] = (cell, sel, k, v, a)

    if not links:
        return 0

    # Heads: links whose output is not the A-continuation of another link.
    a_ids = {id(entry[4]) for entry in links.values()}
    heads = [oid for oid in links if oid not in a_ids]

    # Consumer index for rewiring.
    consumers: dict[int, list[tuple[Cell, str]]] = {}
    for cell in mod.cells.values():
        for pn, pnet in cell.inputs.items():
            consumers.setdefault(id(pnet), []).append((cell, pn))

    created = 0
    for head_id in heads:
        head_cell, sel, _, _, _ = links[head_id]
        chain: list[tuple[Cell, int, int]] = []
        cur = head_id
        tail_net: Net | None = None
        while cur in links:
            cell, lsel, k, v, a = links[cur]
            if lsel is not sel:
                break  # selector changed mid-chain: stop, tail = this link's output
            chain.append((cell, k, v))
            tail_net = a
            cur = id(a)
        if len(chain) < ROM_MIN_CHAIN or tail_net is None:
            continue
        default = _const_of(tail_net)
        if default is None:
            continue
        depth = 1 << sel.width
        seen: set[int] = set()
        ok = True
        for _, k, _ in chain:
            if k >= depth or k in seen:
                ok = False
                break
            seen.add(k)
        if not ok:
            continue

        head_out = next(iter(links[head_id][0].outputs.values()))
        width = head_out.width
        values = [default & ((1 << width) - 1)] * depth
        # The chain is priority-ordered head-first; entries are distinct, so
        # order does not matter.
        for _, k, v in chain:
            values[k] = v & ((1 << width) - 1)

        created += 1
        rom = mod.add_cell(f"$rom_{created}_{head_cell.name}", PrimOp.ROM,
                           values=values, sel_width=sel.width)
        rom_out = mod.add_net(f"$rom_out_{created}_{head_cell.name}", width)
        mod.connect(rom, "A", sel)
        mod.connect(rom, "Y", rom_out, direction="output")

        for cell, pn in consumers.get(id(head_out), []):
            if cell is rom:
                continue
            cell.inputs[pn] = rom_out
        for pn, pnet in list(mod.ports.items()):
            if pnet is head_out:
                mod.ports[pn] = rom_out
        head_out.driver = None  # its mux is deleted below

        # Delete the chain muxes; their EQ/CONST feeders become dead and are
        # swept by dead-code elimination.
        for cell, _, _ in chain:
            if cell.name in mod.cells:
                del mod.cells[cell.name]

    return created
