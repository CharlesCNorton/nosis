"""Nosis cut-based technology mapping — K-feasible cut enumeration for LUT4.

For each net in the IR, enumerates all subsets of ≤K input nets that can
compute that net's function. The smallest cut (fewest LUTs) that covers
the entire design is selected. This replaces multi-cell chains with
single LUT4 cells wherever the combined function fits in 4 inputs.

This is the technique used by ABC (Berkeley) and FlowMap. Our implementation
is simpler: we enumerate cuts bottom-up from the inputs and merge adjacent
cells greedily, without the full priority-cut optimization.
"""

from __future__ import annotations

from nosis.ir import Cell, Module, PrimOp

__all__ = [
    "cut_map_luts",
]

# Operations that can be absorbed into a LUT4 truth table
_LUT_OPS = {
    PrimOp.AND, PrimOp.OR, PrimOp.XOR, PrimOp.NOT,
    PrimOp.MUX, PrimOp.EQ, PrimOp.NE,
    PrimOp.REDUCE_AND, PrimOp.REDUCE_OR, PrimOp.REDUCE_XOR,
}


def _collect_cone_inputs(mod: Module, target_cell: Cell, max_depth: int = 5) -> set[str] | None:
    """Walk backward from a cell through combinational logic up to max_depth.

    Returns the set of input net names at the boundary (FF outputs, INPUT
    outputs, or depth limit). Returns None if the cone exceeds 4 unique inputs.
    """
    boundary: set[str] = set()
    visited_cells: set[str] = set()

    def walk(cell: Cell, depth: int) -> bool:
        """Walk the data structure, calling the visitor function."""
        if cell.name in visited_cells:
            return True
        visited_cells.add(cell.name)

        for net in cell.inputs.values():
            if net.driver is None:
                boundary.add(net.name)
                if len(boundary) > 4:
                    return False
            elif net.driver.op in (PrimOp.FF, PrimOp.INPUT, PrimOp.MEMORY):
                boundary.add(net.name)
                if len(boundary) > 4:
                    return False
            elif net.driver.op == PrimOp.CONST:
                pass  # constants don't count as inputs
            elif net.driver.op in _LUT_OPS and depth < max_depth:
                # Walk deeper into the cone
                if not walk(net.driver, depth + 1):
                    return False
            else:
                boundary.add(net.name)
                if len(boundary) > 4:
                    return False
        return True

    if walk(target_cell, 0):
        return boundary
    return None


def cut_map_luts(mod: Module) -> int:
    """Re-map IR cells using cut enumeration for better LUT4 packing.

    For each combinational cell, check if its logic cone (up to depth 5)
    fits in 4 inputs. If so, mark all intermediate cells in the cone
    for absorption — the target cell computes the entire cone's function,
    and the intermediate cells become dead (removed by DCE).

    This is run AFTER the standard optimization passes and BEFORE tech mapping.
    Returns the number of intermediate cells absorbed.
    """
    absorbed = 0

    # Build consumer count: how many cells read from each net
    net_consumers: dict[str, int] = {}
    for cell in mod.cells.values():
        for net in cell.inputs.values():
            net_consumers[net.name] = net_consumers.get(net.name, 0) + 1

    # Process cells in reverse topological order (outputs first)
    # to maximize absorption depth
    processed: set[str] = set()
    to_mark_dead: set[str] = set()

    for cell in list(mod.cells.values()):
        if cell.name in processed or cell.name in to_mark_dead:
            continue
        if cell.op not in _LUT_OPS:
            continue

        # Check if this cell's output has width 1 (single-bit function)
        out_nets = list(cell.outputs.values())
        if not out_nets or out_nets[0].width != 1:
            continue

        # Try to find a 4-input cut for this cell
        cone_inputs = _collect_cone_inputs(mod, cell, max_depth=5)
        if cone_inputs is None or len(cone_inputs) > 4:
            continue
        if len(cone_inputs) == 0:
            continue  # all-constant, handled by constant fold

        # Collect all cells in the cone (visited during the walk)
        cone_cells: set[str] = set()

        def _collect(c: Cell, depth: int) -> None:
            if c.name in cone_cells:
                return
            cone_cells.add(c.name)
            for net in c.inputs.values():
                if net.driver and net.driver.op in _LUT_OPS and depth < 5:
                    if net.name not in cone_inputs:
                        _collect(net.driver, depth + 1)

        _collect(cell, 0)

        if len(cone_cells) <= 1:
            continue  # no intermediate cells to absorb

        # Check that all intermediate cells' outputs are consumed ONLY
        # within the cone (single-fanout constraint). If an intermediate
        # cell's output feeds cells outside the cone, we can't absorb it.
        all_internal = True
        for cname in cone_cells:
            if cname == cell.name:
                continue
            c = mod.cells.get(cname)
            if c is None:
                all_internal = False
                break
            for out_net in c.outputs.values():
                consumers = net_consumers.get(out_net.name, 0)
                if consumers > 1:
                    all_internal = False
                    break
            if not all_internal:
                break

        if not all_internal:
            continue

        # Compute the composed truth table for the cone
        # Map cone inputs to LUT4 input indices
        input_list = sorted(cone_inputs)[:4]
        input_map = {name: idx for idx, name in enumerate(input_list)}

        init = 0
        valid = True
        for i in range(16):
            # Set input values based on the LUT4 index
            net_values: dict[str, int] = {}
            for name, idx in input_map.items():
                net_values[name] = (i >> idx) & 1

            # Evaluate the cone
            from nosis.eval import eval_cell
            # Need to evaluate all cells in the cone in dependency order
            eval_order: list[Cell] = []
            eval_visited: set[str] = set()

            def _topo(c: Cell) -> None:
                if c.name in eval_visited:
                    return
                eval_visited.add(c.name)
                for net in c.inputs.values():
                    if net.driver and net.driver.name in cone_cells:
                        _topo(net.driver)
                eval_order.append(c)

            _topo(cell)

            for c in eval_order:
                results = eval_cell(c, net_values)
                for port_name, val in results.items():
                    out_net = c.outputs.get(port_name)  # type: ignore[assignment]
                    if out_net:
                        net_values[out_net.name] = val

            # Get the output value
            out_val = 0
            for out_net in cell.outputs.values():
                out_val = net_values.get(out_net.name, 0) & 1
                break

            if out_val:
                init |= (1 << i)

        if not valid:
            continue

        # Store the composed truth table and mark intermediates for absorption
        cell.params["packed_lut_init"] = init
        cell.params["packed_lut_inputs"] = input_list
        cell.params["packed"] = True

        # Rewire cell inputs to the cone boundary inputs
        cell.inputs.clear()
        for idx, name in enumerate(input_list):
            port_name = ["A", "B", "C", "D"][idx]
            net = mod.nets.get(name)  # type: ignore[assignment]
            if net:
                cell.inputs[port_name] = net

        # Mark intermediate cells as dead
        for cname in cone_cells:
            if cname != cell.name:
                to_mark_dead.add(cname)
                absorbed += 1

        processed.add(cell.name)

    # Clear dead cells (they'll be removed by DCE)
    for cname in to_mark_dead:
        if cname in mod.cells:
            c = mod.cells[cname]
            c.inputs.clear()
            c.outputs.clear()
            c.op = PrimOp.CONST
            c.params = {"value": 0, "width": 1, "_dead": True}

    return absorbed
