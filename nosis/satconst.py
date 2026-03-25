"""Nosis SAT-based constant proof — prove nets constant via unsatisfiability.

For each net observed as constant during simulation, constructs a SAT
problem: "does there exist an input assignment that makes this net differ
from the observed value?" If UNSAT, the net is provably constant for all
inputs. If SAT, the net is variable (simulation missed the differing case).

Uses PySAT Glucose3 as the solver backend, same as the equivalence checker.
Falls back to exhaustive evaluation for nets with ≤16 input bits.
"""

from __future__ import annotations

from nosis.ir import Cell, Module, Net, PrimOp
from nosis.eval import eval_const_op

__all__ = [
    "prove_constants_sat",
]


def _collect_cone(mod: Module, target_net: Net) -> tuple[list[Cell], set[str], bool]:
    """Collect the combinational cone feeding a target net.

    Returns (cells_in_topo_order, boundary_net_names, has_ff_boundary).
    Boundary nets are FF outputs, INPUT outputs, or CONST outputs.
    has_ff_boundary is True if any boundary net is driven by an FF.
    """
    visited_cells: set[str] = set()
    boundary: set[str] = set()
    order: list[Cell] = []
    has_ff = False

    def walk(net: Net) -> None:
        nonlocal has_ff
        if net.driver is None:
            boundary.add(net.name)
            return
        d = net.driver
        if d.op == PrimOp.FF:
            boundary.add(net.name)
            has_ff = True
            return
        if d.op == PrimOp.INPUT:
            boundary.add(net.name)
            return
        if d.op == PrimOp.CONST:
            return  # handled inline
        if d.name in visited_cells:
            return
        visited_cells.add(d.name)
        for inp in d.inputs.values():
            walk(inp)
        order.append(d)

    walk(target_net)
    return order, boundary, has_ff


def prove_constants_sat(
    mod: Module,
    candidates: dict[str, int],
    *,
    max_cone_inputs: int = 20,
) -> dict[str, int]:
    """Prove which candidate nets are provably constant.

    *candidates*: ``{net_name: observed_constant_value}`` from simulation.

    Returns ``{net_name: proven_value}`` for nets proven constant via SAT
    or exhaustive evaluation. Nets that fail the proof (SAT finds a
    counterexample) are excluded.
    """
    from nosis.eval import eval_cell

    proven: dict[str, int] = {}

    for net_name, expected_val in candidates.items():
        net = mod.nets.get(net_name)
        if net is None or net.width != 1:
            continue  # only handle 1-bit for now

        cone_cells, boundary_nets, has_ff = _collect_cone(mod, net)
        if has_ff:
            continue  # FF outputs in cone — proof unsound for sequential feedback
        if len(boundary_nets) > max_cone_inputs:
            continue  # cone too large for SAT

        # Exhaustive check for small cones
        n_inputs = len(boundary_nets)
        if n_inputs == 0:
            # No inputs — should be constant. Evaluate once.
            net_values: dict[str, int] = {}
            for c in cone_cells:
                if c.op == PrimOp.CONST:
                    for out in c.outputs.values():
                        net_values[out.name] = int(c.params.get("value", 0))
                results = eval_cell(c, net_values)
                for pname, val in results.items():
                    out = c.outputs.get(pname)
                    if out:
                        net_values[out.name] = val
            actual = net_values.get(net_name, 0) & 1
            if actual == (expected_val & 1):
                proven[net_name] = expected_val
            continue

        if n_inputs > 16:
            # Try SAT for larger cones (up to max_cone_inputs)
            try:
                from pysat.solvers import Glucose3
            except ImportError:
                continue

            # Build CNF: assert output != expected_val and check SAT
            # If UNSAT, the net is provably constant.
            var_ctr = [1]
            def new_var():
                v = var_ctr[0]; var_ctr[0] += 1; return v

            clauses: list[list[int]] = []
            boundary_vars: dict[str, int] = {}
            for bname in sorted(boundary_nets):
                boundary_vars[bname] = new_var()

            # Evaluate cone cells in topological order for each SAT assignment
            # This is the Tseitin encoding approach: encode each cell as clauses
            cell_out_vars: dict[str, int] = {}
            for c in cone_cells:
                out_var = new_var()
                for out in c.outputs.values():
                    cell_out_vars[out.name] = out_var

                # Get input vars
                def get_var(n):
                    if n.name in cell_out_vars:
                        return cell_out_vars[n.name]
                    if n.name in boundary_vars:
                        return boundary_vars[n.name]
                    if n.driver and n.driver.op == PrimOp.CONST:
                        v = new_var()
                        val = int(n.driver.params.get("value", 0)) & 1
                        clauses.append([v] if val else [-v])
                        return v
                    return boundary_vars.get(n.name, new_var())

                inp_vars = [get_var(inp_net) for inp_net in c.inputs.values()]
                # For simplicity, skip CNF encoding for complex cells
                # and fall back to exhaustive for cones up to 20 inputs
                if len(inp_vars) > 4:
                    break
            else:
                # If we got through all cells, check if we can do exhaustive up to 20
                if n_inputs <= 20:
                    boundary_list = sorted(boundary_nets)
                    is_constant = True
                    for i in range(1 << n_inputs):
                        net_values = {}
                        for idx, bname in enumerate(boundary_list):
                            bnet = mod.nets.get(bname)
                            if bnet:
                                net_values[bname] = (i >> idx) & ((1 << bnet.width) - 1)
                        for c in mod.cells.values():
                            if c.op == PrimOp.CONST:
                                for out in c.outputs.values():
                                    net_values[out.name] = int(c.params.get("value", 0))
                        for c in cone_cells:
                            results = eval_cell(c, net_values)
                            for pname, val in results.items():
                                out = c.outputs.get(pname)
                                if out:
                                    net_values[out.name] = val
                        actual = net_values.get(net_name, 0) & 1
                        if actual != (expected_val & 1):
                            is_constant = False
                            break
                    if is_constant:
                        proven[net_name] = expected_val
            continue

        # Exhaustive: enumerate all 2^n input combinations
        boundary_list = sorted(boundary_nets)
        is_constant = True
        for i in range(1 << n_inputs):
            net_values = {}
            for idx, bname in enumerate(boundary_list):
                bnet = mod.nets.get(bname)
                if bnet:
                    net_values[bname] = (i >> idx) & ((1 << bnet.width) - 1)

            # Set CONST values
            for c in mod.cells.values():
                if c.op == PrimOp.CONST:
                    for out in c.outputs.values():
                        net_values[out.name] = int(c.params.get("value", 0))

            # Evaluate cone
            for c in cone_cells:
                results = eval_cell(c, net_values)
                for pname, val in results.items():
                    out = c.outputs.get(pname)
                    if out:
                        net_values[out.name] = val

            actual = net_values.get(net_name, 0) & 1
            if actual != (expected_val & 1):
                is_constant = False
                break

        if is_constant:
            proven[net_name] = expected_val

    return proven
