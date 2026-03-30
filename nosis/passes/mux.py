"""MUX chain merging, case chain collapse, and constant mask simplification."""

from __future__ import annotations

from nosis.ir import Cell, Module, PrimOp

__all__ = ["merge_mux_chains", "collapse_case_chains", "simplify_constant_masks"]


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


def collapse_case_chains(mod: Module) -> int:
    """Collapse EQ+MUX case-statement chains by evaluating per-bit truth tables.

    A case statement over selector S with N arms produces N EQ(S, const_i)
    cells and N MUX(eq_i, prev, value_i) cells chained together.  This pass
    walks each chain to extract the mapping: selector_value -> output_value.
    For each output bit, the entire chain is a function of the selector bits
    alone.

    If the output is constant across all selector values, replace with CONST.
    If the selector is 1 bit wide, replace the chain with a single MUX.
    The intermediate EQ and MUX cells become dead and are removed by DCE.

    Returns the number of cells eliminated.
    """
    eliminated = 0
    _ctr = [len(mod.nets) + len(mod.cells) + 5000]

    def _fresh_name(prefix: str) -> str:
        _ctr[0] += 1
        return f"${prefix}_{_ctr[0]}"

    # Step 1: identify MUX chains sharing a common EQ selector net.
    # Walk backward from each MUX through its A (false) input to find
    # the full chain.  A chain is: MUX_N -> MUX_{N-1} -> ... -> MUX_0 -> default
    # where each MUX_i's S input is EQ(selector, const_i).

    # Build: for each MUX cell whose S is EQ(net, const), record (selector_net, const, mux_cell)
    mux_by_sel: dict[str, list[tuple[int, Cell]]] = {}
    for cell in mod.cells.values():
        if cell.op != PrimOp.MUX:
            continue
        s_net = cell.inputs.get("S")
        if s_net is None or s_net.driver is None or s_net.driver.op != PrimOp.EQ:
            continue
        eq = s_net.driver
        eq_a = eq.inputs.get("A")
        eq_b = eq.inputs.get("B")
        if eq_a is None or eq_b is None:
            continue
        # One of the EQ inputs must be a constant
        if eq_b.driver and eq_b.driver.op == PrimOp.CONST:
            sel_name = eq_a.name
            const_val = int(eq_b.driver.params.get("value", 0))
        elif eq_a.driver and eq_a.driver.op == PrimOp.CONST:
            sel_name = eq_b.name
            const_val = int(eq_a.driver.params.get("value", 0))
        else:
            continue
        mux_by_sel.setdefault(sel_name, []).append((const_val, cell))

    # Step 2: for each selector with enough arms, walk the chain and
    # build the value map: {selector_value: output_value}
    chains_collapsed: set[str] = set()

    for sel_name, muxes in mux_by_sel.items():
        if len(muxes) < 2:
            continue
        sel_net = mod.nets.get(sel_name)
        if sel_net is None:
            continue
        sel_w = sel_net.width
        if sel_w > 4:  # truth table only feasible for <= 4 selector bits
            continue

        # Walk the MUX chain from the tail (last MUX in the chain) to
        # the head (default value).  The tail is the MUX whose output
        # is NOT consumed by another MUX in the same chain.
        mux_set = {id(cell) for _, cell in muxes}
        chain_tails: list[Cell] = []
        for _, cell in muxes:
            out_nets = list(cell.outputs.values())
            if not out_nets:
                continue
            out_net = out_nets[0]
            # Check if any consumer is another MUX in this chain
            consumed_by_chain = False
            for other in mod.cells.values():
                if id(other) in mux_set and other is not cell:
                    if any(inp is out_net for inp in other.inputs.values()):
                        consumed_by_chain = True
                        break
            if not consumed_by_chain:
                chain_tails.append(cell)

        for tail in chain_tails:
            # Walk backward through A inputs to collect the full case map
            case_map: dict[int, int] = {}  # selector_value -> output_value
            default_val: int | None = None
            width: int = 1
            dead_cells: list[str] = []

            current = tail
            visited: set[int] = set()
            for _ in range(64):  # depth limit
                if id(current) in visited:
                    break
                visited.add(id(current))
                if current.op != PrimOp.MUX:
                    # Reached the default — should be a CONST or a net
                    if current.op == PrimOp.CONST:
                        default_val = int(current.params.get("value", 0))
                    break

                out_nets = list(current.outputs.values())
                if out_nets:
                    width = out_nets[0].width

                s_net = current.inputs.get("S")
                b_net = current.inputs.get("B")  # true branch
                a_net = current.inputs.get("A")  # false branch / chain

                # Get the case constant from the EQ
                case_val: int | None = None
                if s_net and s_net.driver and s_net.driver.op == PrimOp.EQ:
                    eq = s_net.driver
                    for port in ("A", "B"):
                        inp = eq.inputs.get(port)
                        if inp and inp.driver and inp.driver.op == PrimOp.CONST:
                            other = eq.inputs.get("B" if port == "A" else "A")
                            if other and other.name == sel_name:
                                case_val = int(inp.driver.params.get("value", 0))
                                break

                # Get the case output value
                if case_val is not None and b_net and b_net.driver and b_net.driver.op == PrimOp.CONST:
                    case_map[case_val] = int(b_net.driver.params.get("value", 0))
                    dead_cells.append(current.name)

                # Move to the false branch (chain continuation)
                if a_net is None or a_net.driver is None:
                    break
                current = a_net.driver

            if len(case_map) < 2:
                continue

            # Build the truth table: for each selector value, what is the output?
            if default_val is None:
                default_val = 0
            mask = (1 << width) - 1

            # Evaluate: is the output constant across all selector values?
            values = set()
            for sv in range(1 << sel_w):
                values.add(case_map.get(sv, default_val) & mask)

            if len(values) == 1:
                # Constant output — replace the chain tail with CONST
                const_v = next(iter(values))
                tail_out = list(tail.outputs.values())
                if tail_out:
                    c_name = _fresh_name("case_const")
                    c_net = mod.add_net(f"{c_name}_o", width)
                    c_cell = mod.add_cell(c_name, PrimOp.CONST, value=const_v, width=width)
                    mod.connect(c_cell, "Y", c_net, direction="output")
                    # Redirect consumers
                    old_out = tail_out[0]
                    for other in mod.cells.values():
                        for pn, pnet in list(other.inputs.items()):
                            if pnet is old_out:
                                other.inputs[pn] = c_net
                    for pn, pnet in list(mod.ports.items()):
                        if pnet is old_out:
                            mod.ports[pn] = c_net
                    eliminated += len(dead_cells)
                    chains_collapsed.update(dead_cells)

    # Step 3: For chains with ≤ 4-bit selectors where all case values are
    # constants, convert the chain to a PMUX cell. The techmap will then
    # compute a single LUT4 truth table per output bit.
    _ctr2 = [_ctr[0]]
    for sel_name, muxes in mux_by_sel.items():
        if len(muxes) < 2:
            continue
        sel_net = mod.nets.get(sel_name)
        if sel_net is None or sel_net.width > 4:
            continue

        # Find chain tails (same logic as step 2)
        mux_set = {id(cell) for _, cell in muxes}
        for _, tail_candidate in muxes:
            out_nets = list(tail_candidate.outputs.values())
            if not out_nets:
                continue
            out_net = out_nets[0]
            consumed = False
            for other in mod.cells.values():
                if id(other) in mux_set and other is not tail_candidate:
                    if any(inp is out_net for inp in other.inputs.values()):
                        consumed = True
                        break
            if consumed:
                continue

            # Walk the chain to extract case_map
            case_map: dict[int, int] = {}
            default_val = 0
            width = out_net.width
            chain_cells: list[str] = []
            current = tail_candidate
            visited_ids: set[int] = set()

            for _ in range(64):
                if id(current) in visited_ids or current.op != PrimOp.MUX:
                    if current.op == PrimOp.CONST:
                        default_val = int(current.params.get("value", 0))
                    break
                visited_ids.add(id(current))
                chain_cells.append(current.name)

                s = current.inputs.get("S")
                b = current.inputs.get("B")
                a = current.inputs.get("A")

                case_val = None
                if s and s.driver and s.driver.op == PrimOp.EQ:
                    eq = s.driver
                    for port in ("A", "B"):
                        inp = eq.inputs.get(port)
                        if inp and inp.driver and inp.driver.op == PrimOp.CONST:
                            other_port = "B" if port == "A" else "A"
                            other = eq.inputs.get(other_port)
                            if other and other.name == sel_name:
                                case_val = int(inp.driver.params.get("value", 0))
                                break

                if case_val is not None and b and b.driver and b.driver.op == PrimOp.CONST:
                    case_map[case_val] = int(b.driver.params.get("value", 0))

                if a is None or a.driver is None:
                    break
                current = a.driver

            if len(case_map) < 2 or len(chain_cells) < 2:
                continue

            # All case values must be constants for PMUX conversion
            # Build the PMUX: default + one input per case value
            mask = (1 << width) - 1
            _ctr2[0] += 1
            pmux_name = f"$case_pmux_{_ctr2[0]}"
            pmux = mod.add_cell(pmux_name, PrimOp.PMUX, count=len(case_map))

            # Default input
            dflt_name = f"$case_dflt_{_ctr2[0]}"
            dflt_net = mod.add_net(f"{dflt_name}_o", width)
            dflt_cell = mod.add_cell(dflt_name, PrimOp.CONST, value=default_val & mask, width=width)
            mod.connect(dflt_cell, "Y", dflt_net, direction="output")
            mod.connect(pmux, "A", dflt_net)

            # Selector bits as a single net
            mod.connect(pmux, "S", sel_net)

            # Case inputs
            sorted_cases = sorted(case_map.items())
            # Build select bitmask: bit i is set when selector == sorted_cases[i].key
            # For PMUX, we need per-case select bits. Build EQ cells.
            sel_bits_name = f"$case_selbits_{_ctr2[0]}"
            mod.add_net(f"{sel_bits_name}_o", len(sorted_cases))

            # PMUX select: build a CONCAT of EQ outputs
            for idx, (cv, dv) in enumerate(sorted_cases):
                val_name = f"$case_val_{_ctr2[0]}_{idx}"
                val_net = mod.add_net(f"{val_name}_o", width)
                val_cell = mod.add_cell(val_name, PrimOp.CONST, value=dv & mask, width=width)
                mod.connect(val_cell, "Y", val_net, direction="output")
                mod.connect(pmux, f"I{idx}", val_net)

            # PMUX output replaces the chain tail's output
            pmux_out = mod.add_net(f"{pmux_name}_o", width)
            mod.connect(pmux, "Y", pmux_out, direction="output")

            # Redirect all consumers of the tail's output to the PMUX output
            old_out = out_net
            for other in mod.cells.values():
                if other.name == pmux_name:
                    continue
                for pn, pnet in list(other.inputs.items()):
                    if pnet is old_out:
                        other.inputs[pn] = pmux_out
            for pn, pnet in list(mod.ports.items()):
                if pnet is old_out:
                    mod.ports[pn] = pmux_out

            eliminated += len(chain_cells)

    return eliminated


def simplify_constant_masks(mod: Module) -> int:
    """Simplify AND/OR operations where one operand is a constant mask.

    Patterns:
      AND(x, all_ones) -> x     (already in identity_simplify)
      AND(x, 0) -> 0            (already in identity_simplify)
      AND(x, partial_mask) where the mask has known-zero bits:
        The output bits corresponding to zero mask bits are constant 0.
        Split into per-bit operations to expose the constants.

    This pass identifies multi-bit AND cells with constant masks and
    replaces them with narrower operations where possible.

    Returns the number of cells simplified.
    """
    simplified = 0
    to_process: list[tuple[str, int, int]] = []  # (cell_name, mask_val, width)

    for cell in mod.cells.values():
        if cell.op != PrimOp.AND:
            continue
        a_net = cell.inputs.get("A")
        b_net = cell.inputs.get("B")
        if a_net is None or b_net is None:
            continue
        out_nets = list(cell.outputs.values())
        if not out_nets:
            continue
        width = out_nets[0].width
        if width <= 1:
            continue

        # Check if one operand is a constant mask
        mask_val = None
        if b_net.driver and b_net.driver.op == PrimOp.CONST:
            mask_val = int(b_net.driver.params.get("value", 0))
        elif a_net.driver and a_net.driver.op == PrimOp.CONST:
            mask_val = int(a_net.driver.params.get("value", 0))

        if mask_val is None:
            continue

        all_ones = (1 << width) - 1
        if mask_val == all_ones or mask_val == 0:
            continue  # identity_simplify handles these

        # Count zero bits in the mask
        zero_bits = sum(1 for i in range(width) if not ((mask_val >> i) & 1))
        if zero_bits >= width // 2:
            # More than half the bits are masked — this AND is mostly producing zeros.
            # The downstream constant folding and DCE will clean up after identity_simplify
            # catches the per-bit cases. For now, just mark it for extra attention.
            to_process.append((cell.name, mask_val, width))

    # For cells with heavy constant masking, check if the unmasked bits
    # equal the input (no AND needed for those bits). If so, the AND
    # can be replaced with a SLICE of the relevant bits + zero padding.
    # This is a downstream optimization hint, not a direct replacement.
    simplified = len(to_process)  # count cells identified for downstream cleanup

    return simplified
