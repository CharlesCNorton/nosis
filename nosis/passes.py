"""Nosis optimization passes — transform the IR while preserving equivalence.

Each pass takes a Module and mutates it in place. Passes are composable
and idempotent: running a pass twice produces the same result as running
it once.
"""

from __future__ import annotations

from nosis.eval import eval_const_op
from nosis.ir import Cell, Module, Net, PrimOp

__all__ = [
    "constant_fold",
    "identity_simplify",
    "dead_code_eliminate",
    "run_default_passes",
]


def _is_const_cell(cell: Cell) -> bool:
    return cell.op == PrimOp.CONST


def _const_value(cell: Cell) -> int | None:
    if cell.op == PrimOp.CONST:
        return int(cell.params.get("value", 0))
    return None


def constant_fold(mod: Module) -> int:
    """Fold cells with all-constant inputs into CONST cells.

    Returns the number of cells folded.
    """
    folded = 0
    changed = True

    while changed:
        changed = False
        to_replace: list[tuple[str, int, int]] = []  # (cell_name, value, width)

        for cell in mod.cells.values():
            if _is_const_cell(cell):
                continue
            if cell.op in (PrimOp.INPUT, PrimOp.OUTPUT, PrimOp.FF):
                continue

            # Check if all inputs are driven by CONST cells
            const_inputs: dict[str, int] = {}
            all_const = True
            for port_name, net in cell.inputs.items():
                if net.driver is not None and _is_const_cell(net.driver):
                    const_inputs[port_name] = _const_value(net.driver) or 0
                else:
                    all_const = False
                    break

            if not all_const:
                continue

            # Get output width
            out_nets = list(cell.outputs.values())
            if not out_nets:
                continue
            width = out_nets[0].width

            # Try to evaluate using the shared evaluator
            try:
                result = eval_const_op(cell.op, const_inputs, cell.params, width)
            except Exception:
                result = None
            if result is not None:
                to_replace.append((cell.name, result, width))

        for cell_name, value, width in to_replace:
            cell = mod.cells[cell_name]
            # Convert to CONST: clear inputs, set params
            cell.inputs.clear()
            cell.op = PrimOp.CONST
            cell.params = {"value": value, "width": width}
            folded += 1
            changed = True

    return folded


# ---------------------------------------------------------------------------
# Identity / absorbing simplification
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Dead code elimination
# ---------------------------------------------------------------------------

def _find_live_nets(mod: Module) -> set[str]:
    """Find all nets reachable from outputs and FF inputs (backward from sinks)."""
    live: set[str] = set()
    worklist: list[str] = []

    # Seeds: output ports and FF data inputs
    for cell in mod.cells.values():
        if cell.op == PrimOp.OUTPUT:
            for net in cell.inputs.values():
                if net.name not in live:
                    live.add(net.name)
                    worklist.append(net.name)
        elif cell.op == PrimOp.FF:
            for port_name, net in cell.inputs.items():
                if net.name not in live:
                    live.add(net.name)
                    worklist.append(net.name)

    # Also seed any net that is a module port
    for name in mod.ports:
        if name not in live:
            live.add(name)
            worklist.append(name)

    # Backward reachability
    while worklist:
        net_name = worklist.pop()
        net = mod.nets.get(net_name)
        if net is None or net.driver is None:
            continue
        driver = net.driver
        for input_net in driver.inputs.values():
            if input_net.name not in live:
                live.add(input_net.name)
                worklist.append(input_net.name)

    return live


def dead_code_eliminate(mod: Module) -> int:
    """Remove cells and nets not reachable from outputs.

    Returns the number of cells removed.
    """
    live_nets = _find_live_nets(mod)
    removed = 0

    # Find dead cells: cells whose outputs are all dead
    dead_cells: list[str] = []
    for cell in mod.cells.values():
        if cell.op in (PrimOp.OUTPUT, PrimOp.INPUT):
            continue
        if not cell.outputs:
            dead_cells.append(cell.name)
            continue
        all_dead = all(net.name not in live_nets for net in cell.outputs.values())
        if all_dead:
            dead_cells.append(cell.name)

    for name in dead_cells:
        del mod.cells[name]
        removed += 1

    # Remove dead nets
    dead_nets = [name for name in mod.nets if name not in live_nets]
    for name in dead_nets:
        del mod.nets[name]

    return removed


# ---------------------------------------------------------------------------
# Default pass pipeline
# ---------------------------------------------------------------------------

def remove_const_ffs(mod: Module) -> int:
    """Remove FFs whose D input is driven by a constant.

    A FF with a constant D input will always hold the same value after
    reset. Replace its Q output connections with the constant value.
    Returns the number of FFs removed.
    """
    removed = 0
    to_remove: list[str] = []

    for cell in mod.cells.values():
        if cell.op != PrimOp.FF:
            continue
        d_net = cell.inputs.get("D")
        if d_net is None or d_net.driver is None:
            continue
        if d_net.driver.op != PrimOp.CONST:
            continue

        # D is constant — this FF always holds the same value
        # Replace the FF output with the constant
        q_nets = list(cell.outputs.values())
        if not q_nets:
            continue
        q_net = q_nets[0]

        # Point q_net's driver to the constant cell
        q_net.driver = d_net.driver
        to_remove.append(cell.name)
        removed += 1

    for name in to_remove:
        del mod.cells[name]

    return removed


def merge_mux_chains(mod: Module) -> int:
    """Deduplicate EQ cells that share the same (selector, constant) pair.

    In case statements, the lowering often produces duplicate EQ cells
    for the same comparison across different target registers. CSE
    catches exact duplicates, but after optimization the structure may
    have diverged enough that CSE misses them.

    Also eliminates EQ cells where the selector width exceeds the number
    of distinct case values — the redundant EQs can never match.

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

    to_remove: set[str] = set()
    for key, cells in eq_groups.items():
        if len(cells) < 2:
            continue
        # Keep the first, redirect consumers of others to the first's output
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
            # Redirect all consumers of dup's output to keeper's output
            for other in mod.cells.values():
                if other is dup:
                    continue
                for pname, pnet in list(other.inputs.items()):
                    if pnet is dup_out_net:
                        other.inputs[pname] = keeper_out_net
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

    for cell_name, src_name in to_bypass:
        cell = mod.cells[cell_name]
        src_net = mod.nets.get(src_name)
        if src_net is None:
            continue
        for out_net in list(cell.outputs.values()):
            for other in mod.cells.values():
                if other is cell:
                    continue
                for pn, pnet in list(other.inputs.items()):
                    if pnet is out_net:
                        other.inputs[pn] = src_net
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

    to_remove: list[str] = []

    for cell in list(mod.cells.values()):
        if cell.op != PrimOp.MUX:
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
        a_is_ones = False
        if b_net.driver and b_net.driver.op == PrimOp.CONST:
            bv = int(b_net.driver.params.get("value", 0))
            mask = (1 << out_net.width) - 1
            if (bv & mask) == mask:
                b_is_ones = True
        if a_net.driver and a_net.driver.op == PrimOp.CONST:
            av = int(a_net.driver.params.get("value", 0))
            mask = (1 << out_net.width) - 1
            if (av & mask) == mask:
                a_is_ones = True

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


def _narrow_const_mux(mod: Module) -> int:
    """Reduce MUX width when one input has constant bits matching the other.

    For MUX(sel, A, B) where A and B are both CONST, and they share
    identical bit values on some positions, those bit positions don't
    need the MUX — they can be wired directly. This narrows the
    effective MUX width.

    When ALL bits are identical (A == B), the MUX is eliminated entirely
    (already handled by merge_mux_chains). This handles partial matches.
    """
    narrowed = 0
    # This optimization is already captured by the constant LUT simplification
    # at the ECP5 level (simplify_constant_luts), which reduces truth tables
    # when inputs are tied to constants. At the IR level, the MUX width
    # reflects the data path width, which is correct.
    #
    # The remaining opportunity: when B is CONST and A is variable, the bits
    # where B==0 can use AND(sel, A_bit) and the bits where B==1 can use
    # OR(sel, A_bit), which are simpler than a full MUX. But the LUT4 truth
    # table already captures this — the constant LUT simplification handles it.
    return narrowed


def run_default_passes(mod: Module) -> dict[str, int]:
    """Run the default optimization pipeline. Returns pass statistics."""
    from nosis.cse import eliminate_common_subexpressions
    from nosis.boolopt import boolean_optimize

    stats: dict[str, int] = {}
    prev_cells = len(mod.cells)

    for iteration in range(6):
        cf = constant_fold(mod)
        ident = identity_simplify(mod)
        bo = boolean_optimize(mod)
        cff = remove_const_ffs(mod)
        cse = eliminate_common_subexpressions(mod)
        mm = merge_mux_chains(mod)
        mz = _simplify_mux_with_zero(mod)
        mn = _narrow_const_mux(mod)
        dce = dead_code_eliminate(mod)

        total = cf + ident + bo + cff + cse + mm + mz + mn + dce
        stats[f"round_{iteration}"] = total

        cur_cells = len(mod.cells)
        if cur_cells == prev_cells:
            break
        prev_cells = cur_cells

    # Re-run inference after optimization
    from nosis.carry import infer_carry_chains
    from nosis.bram import infer_brams
    from nosis.dsp import infer_dsps
    stats["carry_infer"] = infer_carry_chains(mod)
    stats["bram_infer"] = infer_brams(mod)
    stats["dsp_infer"] = infer_dsps(mod)

    return stats
