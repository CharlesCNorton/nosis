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
        """Walk the data structure, calling the visitor function."""
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
                    out = c.outputs.get(pname)  # type: ignore[assignment]
                    if out:
                        net_values[out.name] = val
            actual = net_values.get(net_name, 0) & 1
            if actual == (expected_val & 1):
                proven[net_name] = expected_val
            continue

        if n_inputs > 16:
            # Tseitin CNF encoding for larger cones
            try:
                from pysat.solvers import Glucose3
            except ImportError:
                continue

            _vctr = [1]
            def _nv():
                v = _vctr[0]
                _vctr[0] += 1
                return v

            _cls: list[list[int]] = []
            _bvars: dict[str, int] = {}
            for bname in sorted(boundary_nets):
                _bvars[bname] = _nv()

            _ovars: dict[str, int] = {}
            _encoding_ok = True

            def _get(n):
                if n.name in _ovars:
                    return _ovars[n.name]
                if n.name in _bvars:
                    return _bvars[n.name]
                if n.driver and n.driver.op == PrimOp.CONST:
                    v = _nv()
                    val = int(n.driver.params.get("value", 0)) & 1
                    _cls.append([v] if val else [-v])
                    _ovars[n.name] = v
                    return v
                v = _nv()
                _ovars[n.name] = v
                return v

            for c in cone_cells:
                outs = list(c.outputs.values())
                if not outs or outs[0].width != 1:
                    _encoding_ok = False
                    break
                o = _nv()
                _ovars[outs[0].name] = o
                inps = list(c.inputs.values())
                ivs = [_get(inp) for inp in inps]
                if c.op == PrimOp.AND and len(ivs) == 2:
                    _cls.append([-ivs[0], -ivs[1], o])
                    _cls.append([ivs[0], -o])
                    _cls.append([ivs[1], -o])
                elif c.op == PrimOp.OR and len(ivs) == 2:
                    _cls.append([ivs[0], ivs[1], -o])
                    _cls.append([-ivs[0], o])
                    _cls.append([-ivs[1], o])
                elif c.op == PrimOp.XOR and len(ivs) == 2:
                    _cls.append([-ivs[0], -ivs[1], -o])
                    _cls.append([ivs[0], ivs[1], -o])
                    _cls.append([ivs[0], -ivs[1], o])
                    _cls.append([-ivs[0], ivs[1], o])
                elif c.op == PrimOp.NOT and len(ivs) >= 1:
                    _cls.append([ivs[0], o])
                    _cls.append([-ivs[0], -o])
                elif c.op == PrimOp.MUX and len(ivs) == 3:
                    s, f, t = ivs[0], ivs[1], ivs[2]
                    _cls.append([-s, -t, o])
                    _cls.append([-s, t, -o])
                    _cls.append([s, -f, o])
                    _cls.append([s, f, -o])
                elif c.op == PrimOp.EQ and len(ivs) == 2:
                    _cls.append([-ivs[0], -ivs[1], o])
                    _cls.append([ivs[0], ivs[1], o])
                    _cls.append([ivs[0], -ivs[1], -o])
                    _cls.append([-ivs[0], ivs[1], -o])
                elif c.op == PrimOp.NE and len(ivs) == 2:
                    _cls.append([-ivs[0], -ivs[1], -o])
                    _cls.append([ivs[0], ivs[1], -o])
                    _cls.append([ivs[0], -ivs[1], o])
                    _cls.append([-ivs[0], ivs[1], o])
                else:
                    _encoding_ok = False
                    break

            if _encoding_ok and net_name in _ovars:
                target_var = _ovars[net_name]
                # Assert output != expected: if expected is 1, assert target=0
                if expected_val & 1:
                    _cls.append([-target_var])
                else:
                    _cls.append([target_var])
                solver = Glucose3()
                for cl in _cls:
                    solver.add_clause(cl)
                if not solver.solve():
                    proven[net_name] = expected_val
                solver.delete()
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
                    out = c.outputs.get(pname)  # type: ignore[assignment]
                    if out:
                        net_values[out.name] = val

            actual = net_values.get(net_name, 0) & 1
            if actual != (expected_val & 1):
                is_constant = False
                break

        if is_constant:
            proven[net_name] = expected_val

    return proven


def prove_equivalences_sat(
    mod: Module,
    candidates: list[tuple[str, str]],
    *,
    max_cone_inputs: int = 16,
) -> list[tuple[str, str]]:
    """Prove which candidate net pairs are provably equivalent.

    *candidates*: list of ``(net_a_name, net_b_name)`` pairs observed as
    equivalent during simulation.

    Returns the subset of pairs proven equivalent via exhaustive evaluation
    of the combined logic cones. Pairs that fail the proof (a distinguishing
    input exists) are excluded. Pairs where either net has an FF boundary
    in its cone are excluded (sequential feedback makes combinational proof
    unsound).
    """
    from nosis.eval import eval_cell

    proven: list[tuple[str, str]] = []

    for net_a_name, net_b_name in candidates:
        net_a = mod.nets.get(net_a_name)
        net_b = mod.nets.get(net_b_name)
        if net_a is None or net_b is None:
            continue
        if net_a.width != net_b.width:
            continue

        cone_a, boundary_a, has_ff_a = _collect_cone(mod, net_a)
        cone_b, boundary_b, has_ff_b = _collect_cone(mod, net_b)
        if has_ff_a or has_ff_b:
            continue

        # Combined boundary: union of both cones' inputs
        boundary = boundary_a | boundary_b
        if len(boundary) > max_cone_inputs:
            continue

        # Combined cells: deduplicate by name
        seen: set[str] = set()
        combined: list[Cell] = []
        for c in cone_a + cone_b:
            if c.name not in seen:
                seen.add(c.name)
                combined.append(c)

        n_inputs = len(boundary)
        if n_inputs == 0:
            # No inputs — evaluate once
            net_values: dict[str, int] = {}
            for c in mod.cells.values():
                if c.op == PrimOp.CONST:
                    for out in c.outputs.values():
                        net_values[out.name] = int(c.params.get("value", 0))
            for c in combined:
                results = eval_cell(c, net_values)
                for pname, val in results.items():
                    out = c.outputs.get(pname)
                    if out:
                        net_values[out.name] = val
            mask = (1 << net_a.width) - 1
            va = net_values.get(net_a_name, 0) & mask
            vb = net_values.get(net_b_name, 0) & mask
            if va == vb:
                proven.append((net_a_name, net_b_name))
            continue

        # Exhaustive: enumerate all 2^n input combinations
        boundary_list = sorted(boundary)
        is_equiv = True
        mask = (1 << net_a.width) - 1
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

            for c in combined:
                results = eval_cell(c, net_values)
                for pname, val in results.items():
                    out = c.outputs.get(pname)
                    if out:
                        net_values[out.name] = val

            va = net_values.get(net_a_name, 0) & mask
            vb = net_values.get(net_b_name, 0) & mask
            if va != vb:
                is_equiv = False
                break

        if is_equiv:
            proven.append((net_a_name, net_b_name))

    return proven
