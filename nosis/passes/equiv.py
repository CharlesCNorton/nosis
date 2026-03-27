"""Functional identity, HIT equivalence, don't-care, and MUX-to-AND passes."""

from __future__ import annotations

import nosis.passes as _passes_pkg
from nosis.ir import Cell, Module, PrimOp

__all__ = [
    "_simplify_mux_with_zero",
    "_eliminate_functional_identities",
    "_eliminate_dont_care_inputs",
    "_merge_hit_equivalent",
]


def _simplify_mux_with_zero(mod: Module) -> int:
    """Replace MUX(sel, A, 0) with AND(NOT(sel), A) and MUX(sel, 0, B) with AND(sel, B).

    These substitutions reduce the LUT input count from 3 to 2 per bit,
    improving dual-LUT packing efficiency. A 2-input AND can share a
    slice with another 2-input operation, where a 3-input MUX cannot.

    Returns the number of MUX cells replaced.
    """
    replaced = 0
    _cell_counter = [len(mod.nets) + len(mod.cells) + 1]

    def _fresh(prefix: str) -> str:
        name = f"${prefix}_{_cell_counter[0]}"
        _cell_counter[0] += 1
        return name


    for cell in list(mod.cells.values()):
        if cell.op != PrimOp.MUX:
            continue
        # Don't touch MUX cells in the memory/PMUX protection cone
        if _passes_pkg._active_mem_protect and any(n.name in _passes_pkg._active_mem_protect for n in cell.inputs.values()):
            continue
        a_net = cell.inputs.get("A")
        b_net = cell.inputs.get("B")
        s_net = cell.inputs.get("S")
        if not a_net or not b_net or not s_net:
            continue

        out_nets = list(cell.outputs.values())
        if not out_nets:
            continue
        out_net = out_nets[0]

        b_is_zero = (b_net.driver and b_net.driver.op == PrimOp.CONST
                     and int(b_net.driver.params.get("value", -1)) == 0)
        a_is_zero = (a_net.driver and a_net.driver.op == PrimOp.CONST
                     and int(a_net.driver.params.get("value", -1)) == 0)

        if b_is_zero:
            # MUX(sel, A, 0) = ~sel & A per bit
            # Create NOT(sel) then AND(NOT_sel, A)
            not_name = _fresh("mux_not")
            not_out = mod.add_net(f"{not_name}_o", s_net.width)
            not_cell = mod.add_cell(not_name, PrimOp.NOT)
            mod.connect(not_cell, "A", s_net)
            mod.connect(not_cell, "Y", not_out, direction="output")

            # Rewrite the MUX cell as AND
            cell.op = PrimOp.AND
            cell.inputs.clear()
            cell.inputs["A"] = not_out
            cell.inputs["B"] = a_net
            replaced += 1

        elif a_is_zero:
            # MUX(sel, 0, B) = sel & B per bit
            cell.op = PrimOp.AND
            cell.inputs.clear()
            cell.inputs["A"] = s_net
            cell.inputs["B"] = b_net
            replaced += 1
            continue

        # MUX(sel, A, all_ones) = OR(sel, A) per bit
        b_is_ones = False
        if b_net.driver and b_net.driver.op == PrimOp.CONST:
            bv = int(b_net.driver.params.get("value", 0))
            mask = (1 << out_net.width) - 1
            if (bv & mask) == mask:
                b_is_ones = True
        if a_net.driver and a_net.driver.op == PrimOp.CONST:
            av = int(a_net.driver.params.get("value", 0))
            mask = (1 << out_net.width) - 1
            if (av & mask) == mask:
                pass

        if b_is_ones:
            # MUX(sel, A, ones) = sel ? ones : A = OR(sel, A) per bit
            cell.op = PrimOp.OR
            cell.inputs.clear()
            cell.inputs["A"] = s_net
            cell.inputs["B"] = a_net
            replaced += 1
        # a_is_ones case (MUX(sel, ones, B)) would need NOT(sel)+OR,
        # which adds a cell — not beneficial, so skip it.

    return replaced


def _eliminate_functional_identities(mod: Module) -> int:
    """Eliminate cells whose output is functionally identical to one of their inputs.

    For each combinational cell with ≤4 inputs and 1-bit output, exhaustively
    evaluate the truth table. If the output equals input[i] for all combinations
    of the other inputs, the cell is an identity — replace with a wire to input[i].
    If the output equals NOT(input[i]), replace with a NOT cell.

    This catches algebraic identities that survive structural optimization:
    AND(a, OR(a, b)) = a, MUX(sel, a, a) = a (already handled), and more
    complex tautologies involving 3-4 variables.

    Provable: verified by exhaustive truth table evaluation.
    Returns the number of cells eliminated.
    """
    from nosis.eval import eval_cell

    eliminated = 0
    _ctr = [len(mod.nets) + len(mod.cells) + 200]

    for cell in list(mod.cells.values()):
        if cell.op in (PrimOp.INPUT, PrimOp.OUTPUT, PrimOp.FF, PrimOp.CONST, PrimOp.MEMORY):
            continue
        out_nets = list(cell.outputs.values())
        if not out_nets or out_nets[0].width != 1:
            continue

        input_nets = list(cell.inputs.values())
        n_inputs = len(input_nets)
        if n_inputs == 0 or n_inputs > 4:
            continue

        out_net = out_nets[0]

        # Evaluate truth table
        for inp_idx, inp_net in enumerate(input_nets):
            is_identity = True
            is_negation = True
            for i in range(1 << n_inputs):
                net_values = {}
                for idx, net in enumerate(input_nets):
                    net_values[net.name] = (i >> idx) & 1
                results = eval_cell(cell, net_values)
                val = 0
                for v in results.values():
                    val = v & 1
                    break
                inp_val = (i >> inp_idx) & 1
                if val != inp_val:
                    is_identity = False
                if val != (1 - inp_val):
                    is_negation = False
                if not is_identity and not is_negation:
                    break

            if is_identity:
                # Replace cell with wire: redirect consumers of out_net to inp_net
                for other in mod.cells.values():
                    if other is cell:
                        continue
                    for pn, pnet in list(other.inputs.items()):
                        if pnet is out_net:
                            other.inputs[pn] = inp_net
                for pn, pnet in list(mod.ports.items()):
                    if pnet is out_net:
                        mod.ports[pn] = inp_net
                out_net.driver = inp_net.driver
                cell.inputs.clear()
                cell.outputs.clear()
                cell.op = PrimOp.CONST
                cell.params = {"value": 0, "width": 1, "_dead": True}
                eliminated += 1
                break

            # Negation conversions add a NOT cell — skip unless the cell
            # currently uses more inputs than NOT (which always uses 1).
            elif is_negation and n_inputs > 1:
                cell.op = PrimOp.NOT
                cell.inputs.clear()
                cell.inputs["A"] = inp_net
                eliminated += 1
                break

    return eliminated


def _eliminate_dont_care_inputs(mod: Module) -> int:
    """Remove inputs that don't affect a cell's output (don't-care inputs).

    If cell f(a,b,c) produces the same output regardless of c (truth table
    is symmetric under c), then c is a don't-care input. The cell can be
    simplified to f(a,b) — fewer inputs means the tech mapper produces
    fewer LUT4 cells and the result packs better.

    This is the encode-decode method from HoTT: we build a map from the
    3-input function to a 2-input function (by dropping c) and verify
    it's an equivalence (same output for all inputs).
    """
    from nosis.eval import eval_cell

    eliminated = 0
    for cell in list(mod.cells.values()):
        if cell.op in (PrimOp.INPUT, PrimOp.OUTPUT, PrimOp.FF, PrimOp.CONST, PrimOp.MEMORY):
            continue
        outs = list(cell.outputs.values())
        if not outs or outs[0].width != 1:
            continue
        inp_items = list(cell.inputs.items())
        n = len(inp_items)
        if n < 2 or n > 4:
            continue

        inp_names = [name for name, _ in inp_items]

        # Compute truth table
        tt = 0
        for i in range(1 << n):
            nv = {}
            for idx, (_, net) in enumerate(inp_items):
                nv[net.name] = (i >> idx) & 1
            results = eval_cell(cell, nv)
            val = next(iter(results.values()), 0) & 1
            if val:
                tt |= (1 << i)

        # Check each input for don't-care
        for drop_idx in range(n):
            independent = True
            for i in range(1 << n):
                partner = i ^ (1 << drop_idx)
                if ((tt >> i) & 1) != ((tt >> partner) & 1):
                    independent = False
                    break
            if independent:
                # Drop this input
                drop_name = inp_names[drop_idx]
                del cell.inputs[drop_name]
                eliminated += 1
                break  # only drop one input per cell per pass

    return eliminated


def _merge_hit_equivalent(mod: Module) -> int:
    """Merge cells with identical truth tables but different structure (HIT equivalence).

    Two cells with the same input net set that compute the same Boolean function
    are equivalent regardless of their internal structure. This is the Higher
    Inductive Type principle: a function is defined by its action on inputs
    (the truth table), not its syntactic form (the cell structure).

    Goes beyond CSE which requires structural identity (same op, same params).
    """
    from nosis.eval import eval_cell
    from collections import defaultdict

    input_groups: dict[tuple, list[Cell]] = defaultdict(list)
    for cell in mod.cells.values():
        if cell.op in (PrimOp.INPUT, PrimOp.OUTPUT, PrimOp.FF, PrimOp.CONST, PrimOp.MEMORY):
            continue
        outs = list(cell.outputs.values())
        if not outs or outs[0].width != 1:
            continue
        inp_names = tuple(sorted(n.name for n in cell.inputs.values()))
        if len(inp_names) == 0 or len(inp_names) > 4:
            continue
        input_groups[inp_names].append(cell)

    merged = 0
    for inp_names, cells in input_groups.items():
        if len(cells) < 2:
            continue
        n = len(inp_names)
        tt_to_cells: dict[int, list[Cell]] = defaultdict(list)
        for c in cells:
            tt = 0
            for i in range(1 << n):
                nv = {name: (i >> idx) & 1 for idx, name in enumerate(inp_names)}
                results = eval_cell(c, nv)
                val = next(iter(results.values()), 0) & 1
                if val:
                    tt |= (1 << i)
            tt_to_cells[tt].append(c)

        for tt, equiv in tt_to_cells.items():
            if len(equiv) < 2:
                continue
            keeper = equiv[0]
            keeper_out = list(keeper.outputs.values())[0]
            for dup in equiv[1:]:
                dup_out = list(dup.outputs.values())[0]
                for other in mod.cells.values():
                    if other is dup:
                        continue
                    for pn, pnet in list(other.inputs.items()):
                        if pnet is dup_out:
                            other.inputs[pn] = keeper_out
                for pn, pnet in list(mod.ports.items()):
                    if pnet is dup_out:
                        mod.ports[pn] = keeper_out
                dup_out.driver = keeper_out.driver
                dup.inputs.clear()
                dup.outputs.clear()
                dup.op = PrimOp.CONST
                dup.params = {"value": 0, "width": 1, "_dead": True}
                merged += 1

    return merged
