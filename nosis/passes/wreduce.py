"""Register width reduction (wreduce).

SystemVerilog `enum {...}` with no explicit base type is `int` (32 bits), and
integer temporaries are 32 bits, so a 4-state FSM register carries 32 flip-flops
and drives 32-bit-wide mux trees when 2 bits would do. This pass proves the high
bits of such a register are constant and prunes them: the flip-flop keeps only
the live low bits, and consumers read a reconstruction of the full width
(`{const_high, live_low}`). The now-unused high mux bits fall out as dead LUTs
during technology mapping.

Soundness: a register output bit `i` is constant `c` iff, in the mux tree that
feeds the register's D input, every leaf that is not the register's own hold
value is a constant with bit `i == c`. Then `D[i] ∈ {c, Q[i]}`, so `Q[i] = c` is
the only reachable value. Leaves that are anything other than a constant or the
hold make the bit variable, so the analysis never widens a live bit to a
constant.
"""

from __future__ import annotations

from nosis.ir import Module, PrimOp

__all__ = ["reduce_register_width"]


def _mux_leaves(d_net) -> list | None:
    """Return the data leaves of the mux tree rooted at ``d_net``.

    A leaf is any net whose driver is not a MUX. Returns ``None`` if the tree
    is malformed or implausibly large.
    """
    leaves = []
    stack = [d_net]
    seen_mux: set[int] = set()
    guard = 0
    while stack:
        guard += 1
        if guard > 200000:
            return None
        n = stack.pop()
        if n is None:
            return None
        dr = n.driver
        if dr is not None and dr.op == PrimOp.MUX:
            if id(dr) in seen_mux:
                continue
            seen_mux.add(id(dr))
            stack.append(dr.inputs.get("A"))
            stack.append(dr.inputs.get("B"))
        else:
            leaves.append(n)
    return leaves


def reduce_register_width(mod: Module) -> int:
    """Prune provably-constant high bits from flip-flop registers.

    Returns the number of flip-flop bits removed.
    """
    removed = 0
    ctr = 0
    ffs = [c for c in mod.cells.values() if c.op == PrimOp.FF]

    for cell in ffs:
        d = cell.inputs.get("D")
        outs = list(cell.outputs.values())
        if d is None or not outs:
            continue
        q = outs[0]
        width = q.width
        if width <= 1 or d.width != width:
            continue
        tgt = str(cell.params.get("ff_target", ""))

        leaves = _mux_leaves(d)
        if not leaves:
            continue

        def _is_hold(n) -> bool:
            return n is q or n.name == q.name or (bool(tgt) and n.name == tgt)

        # Classify each bit: constant value, or variable.
        const_val: dict[int, int] = {}
        variable = [False] * width
        for i in range(width):
            vals: set[int] = set()
            saw_nonhold = False
            var = False
            for lf in leaves:
                if _is_hold(lf):
                    continue
                saw_nonhold = True
                ld = lf.driver
                if ld is not None and ld.op == PrimOp.CONST:
                    vals.add((int(ld.params.get("value", 0)) >> i) & 1)
                elif ld is not None and ld.op == PrimOp.ZEXT:
                    # Zero-extension: bits at/above the source width are 0.
                    src = ld.inputs.get("A")
                    if src is not None and i >= src.width:
                        vals.add(0)
                    else:
                        var = True
                        break
                else:
                    var = True
                    break
            if var or not saw_nonhold or len(vals) != 1:
                variable[i] = True
            else:
                const_val[i] = next(iter(vals))

        # Only reduce a contiguous block of constant most-significant bits.
        vmax = -1
        for i in range(width):
            if variable[i]:
                vmax = i
        keep = vmax + 1
        if keep >= width or keep <= 0:
            continue
        if any(i not in const_val for i in range(keep, width)):
            continue

        high_w = width - keep
        high_val = sum(const_val[i] << (i - keep) for i in range(keep, width))

        ctr += 1
        nq = mod.add_net(f"$wr_q_{ctr}", keep)
        hc_net = mod.add_net(f"$wr_hc_{ctr}", high_w)
        hc = mod.add_cell(f"$wr_const_{ctr}", PrimOp.CONST, value=high_val, width=high_w)
        mod.connect(hc, "Y", hc_net, direction="output")
        full = mod.add_net(f"$wr_full_{ctr}", width)
        cat = mod.add_cell(f"$wr_cat_{ctr}", PrimOp.CONCAT, count=2)
        mod.connect(cat, "I0", nq)
        cat.params["I0_width"] = keep
        mod.connect(cat, "I1", hc_net)
        cat.params["I1_width"] = high_w
        mod.connect(cat, "Y", full, direction="output")

        # Redirect every reader of the old Q (including the hold leaves in this
        # register's own mux tree) to the reconstructed full-width value.
        for other in mod.cells.values():
            if other is cell or other is cat:
                continue
            for pn, nn in list(other.inputs.items()):
                if nn is q or nn.name == q.name or (tgt and nn.name == tgt):
                    other.inputs[pn] = full
        for pn, nn in list(mod.ports.items()):
            if nn is q or nn.name == q.name or (tgt and nn.name == tgt):
                mod.ports[pn] = full

        # Narrow the flip-flop itself. D stays the full mux (its high bits become
        # dead LUTs after mapping); only the live low bits are registered.
        cell.outputs.clear()
        mod.connect(cell, "Q", nq, direction="output")
        cell.params["init_value"] = int(cell.params.get("init_value", 0)) & ((1 << keep) - 1)
        removed += high_w

    return removed
