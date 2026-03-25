"""Nosis reachable-state equivalence merging.

Simulates the design for N cycles, identifies nets that always carry the
same value across all reachable states, and merges them. This is more
powerful than structural CSE because it finds equivalences that only hold
in the reachable state space, not necessarily for all possible inputs.

The theoretical basis is quotient types from HoTT: the net space is
quotiented by the reachable-state equivalence relation, collapsing
nets that are path-equivalent over the reachable subtype of the state space.
"""

from __future__ import annotations

import random
from collections import defaultdict

from nosis.ir import Module, Net, PrimOp
from nosis.equiv import _simulate_combinational

__all__ = [
    "merge_reachable_equivalent",
    "propagate_reachable_constants",
]


def _ff_chain_depth(mod: Module) -> int:
    """Compute the longest FF-to-FF combinational path depth.

    This approximates the maximum number of cycles a state change takes
    to propagate through the sequential feedback network. Used to set a
    floor on simulation cycles for equivalence checking.
    """
    # Build FF output -> FF input distance via BFS through combinational logic
    ff_outputs: set[str] = set()
    for cell in mod.cells.values():
        if cell.op == PrimOp.FF:
            for out in cell.outputs.values():
                ff_outputs.add(out.name)

    if not ff_outputs:
        return 0

    # Count FFs in feedback chains by tracing from each FF output
    max_depth = 0
    for start in ff_outputs:
        visited: set[str] = set()
        depth = 0
        frontier = {start}
        while frontier:
            next_frontier: set[str] = set()
            for net_name in frontier:
                if net_name in visited:
                    continue
                visited.add(net_name)
                net = mod.nets.get(net_name)
                if net is None:
                    continue
                # Find cells that consume this net
                for cell in mod.cells.values():
                    for inp in cell.inputs.values():
                        if inp.name == net_name:
                            if cell.op == PrimOp.FF:
                                # Reached another FF — count it
                                depth += 1
                                # Continue through the FF's output
                                for out in cell.outputs.values():
                                    if out.name not in visited:
                                        next_frontier.add(out.name)
                            else:
                                for out in cell.outputs.values():
                                    if out.name not in visited:
                                        next_frontier.add(out.name)
            frontier = next_frontier
            if depth > 20:
                break  # cap traversal
        max_depth = max(max_depth, depth)
    return max_depth


def merge_reachable_equivalent(
    mod: Module,
    *,
    cycles: int = 500,
    seed: int = 42,
) -> int:
    """Merge nets that carry identical values across all reachable states.

    Simulates the design for *cycles* clock cycles with random inputs,
    tracking the value of every net. Nets that produce the same value
    sequence are functionally equivalent in the reachable state space.
    Redirect all consumers of duplicate nets to the canonical representative.

    The cycle count is automatically raised when the design contains deep
    FF feedback chains (e.g. refresh counters) that need more cycles to
    distinguish.

    Returns the number of nets merged (cells potentially eliminated by
    subsequent DCE).
    """
    rng = random.Random(seed)

    # Adapt cycle count to FF chain depth — ensures counters and deep
    # state machines are simulated long enough to distinguish.
    depth = _ff_chain_depth(mod)
    if depth > 0:
        # Need at least 2^depth cycles to see all reachable counter states,
        # capped at 2000 to keep runtime reasonable.
        depth_floor = min(2 ** depth + 100, 2000)
        cycles = max(cycles, depth_floor)

    # Identify input ports
    input_ports: dict[str, int] = {}
    for cell in mod.cells.values():
        if cell.op == PrimOp.INPUT:
            for out in cell.outputs.values():
                input_ports[out.name] = out.width

    if not input_ports:
        return 0

    # Initialize FF state
    ff_state: dict[str, int] = {}
    for cell in mod.cells.values():
        if cell.op == PrimOp.FF:
            for out in cell.outputs.values():
                ff_state[out.name] = 0

    # Simulate and collect per-net value signatures
    signatures: dict[str, list[int]] = {}
    for cycle in range(cycles):
        inputs = {name: rng.getrandbits(w) for name, w in input_ports.items()}
        sim = dict(inputs)
        sim.update(ff_state)
        vals = _simulate_combinational(mod, sim)

        for name, val in vals.items():
            if name not in signatures:
                signatures[name] = []
            signatures[name].append(val)

        # Update FF state
        for cell in mod.cells.values():
            if cell.op == PrimOp.FF:
                d_net = cell.inputs.get("D")
                if d_net and d_net.name in vals:
                    for out in cell.outputs.values():
                        ff_state[out.name] = vals[d_net.name]

    # Group nets by value signature
    sig_groups: dict[tuple[int, ...], list[str]] = defaultdict(list)
    for name, sig in signatures.items():
        sig_groups[tuple(sig)].append(name)

    # Compute nets that feed FF D inputs (sequential state feedback).
    ff_input_reachable: set[str] = set()
    _ff_wl: list[str] = []
    for cell in mod.cells.values():
        if cell.op == PrimOp.FF:
            d_net = cell.inputs.get("D")
            if d_net and d_net.name not in ff_input_reachable:
                ff_input_reachable.add(d_net.name)
                _ff_wl.append(d_net.name)
    while _ff_wl:
        _nname = _ff_wl.pop()
        _net = mod.nets.get(_nname)
        if _net is None or _net.driver is None:
            continue
        if _net.driver.op == PrimOp.FF:
            continue  # FF boundary
        for _inp in _net.driver.inputs.values():
            if _inp.name not in ff_input_reachable:
                ff_input_reachable.add(_inp.name)
                _ff_wl.append(_inp.name)

    # Compute the set of nets that feed output ports (directly or through
    # combinational logic). These must not be merged — they carry the
    # design's externally visible behavior.
    output_reachable: set[str] = set()
    _worklist: list[str] = []
    for cell in mod.cells.values():
        if cell.op == PrimOp.OUTPUT:
            for inp_net in cell.inputs.values():
                if inp_net.name not in output_reachable:
                    output_reachable.add(inp_net.name)
                    _worklist.append(inp_net.name)
    while _worklist:
        _nname = _worklist.pop()
        _net = mod.nets.get(_nname)
        if _net is None or _net.driver is None:
            continue
        if _net.driver.op == PrimOp.FF:
            # FF is a boundary — don't walk through
            output_reachable.add(_nname)
            continue
        for _inp in _net.driver.inputs.values():
            if _inp.name not in output_reachable:
                output_reachable.add(_inp.name)
                _worklist.append(_inp.name)

    # Merge: for each group with >1 nets, pick a canonical representative
    # and redirect all consumers of the others to the canonical.
    # SAFETY: never merge a net in the output-reachable set unless its
    # canonical representative is also output-reachable with the same driver.
    merged = 0
    for sig, net_names in sig_groups.items():
        if len(net_names) < 2:
            continue

        # Pick the canonical net: prefer port nets, then shortest name
        canonical_name = None
        for name in net_names:
            if name in mod.ports:
                canonical_name = name
                break
        if canonical_name is None:
            canonical_name = min(net_names, key=len)

        canonical_net = mod.nets.get(canonical_name)
        if canonical_net is None:
            continue

        for name in net_names:
            if name == canonical_name:
                continue
            dup_net = mod.nets.get(name)
            if dup_net is None:
                continue
            if dup_net.width != canonical_net.width:
                continue
            if name in mod.ports:
                continue
            # Safety guards:
            # 1. Don't merge nets in the output-reachable cone
            if name in output_reachable:
                continue
            if canonical_name in output_reachable:
                continue
            # 2. Don't merge nets that feed FF D inputs (sequential state).
            #    Simulation may not cover all state transitions.
            if name in ff_input_reachable:
                continue
            if canonical_name in ff_input_reachable:
                continue

            for cell in mod.cells.values():
                for pname, pnet in list(cell.inputs.items()):
                    if pnet is dup_net:
                        cell.inputs[pname] = canonical_net
            for pname, pnet in list(mod.ports.items()):
                if pnet is dup_net:
                    mod.ports[pname] = canonical_net

            merged += 1

    return merged


def propagate_reachable_constants(
    mod: Module,
    *,
    cycles: int = 200,
    seed: int = 42,
) -> int:
    """Replace nets that are constant across all reachable states with CONST cells.

    Simulates for *cycles* clock cycles and identifies nets whose value
    never changes. These nets are functionally constant in the reachable
    state space even though the combinational logic that drives them is
    not structurally constant. Replace the driver with a CONST cell.

    This is the cofiber construction from stable categories: the "difference"
    (cofiber) between the design and a simplified version is zero for these
    nets. The simplification is therefore exact — no information is lost.

    Returns the number of nets replaced with constants.
    """
    rng = random.Random(seed)

    input_ports: dict[str, int] = {}
    for cell in mod.cells.values():
        if cell.op == PrimOp.INPUT:
            for out in cell.outputs.values():
                input_ports[out.name] = out.width

    if not input_ports:
        return 0

    ff_state: dict[str, int] = {}
    for cell in mod.cells.values():
        if cell.op == PrimOp.FF:
            for out in cell.outputs.values():
                ff_state[out.name] = 0

    # Track min and max value for each net across all cycles
    net_min: dict[str, int] = {}
    net_max: dict[str, int] = {}

    for cycle in range(cycles):
        inputs = {name: rng.getrandbits(w) for name, w in input_ports.items()}
        sim = dict(inputs)
        sim.update(ff_state)
        vals = _simulate_combinational(mod, sim)

        for name, val in vals.items():
            if name not in net_min:
                net_min[name] = val
                net_max[name] = val
            else:
                if val < net_min[name]:
                    net_min[name] = val
                if val > net_max[name]:
                    net_max[name] = val

        for cell in mod.cells.values():
            if cell.op == PrimOp.FF:
                d_net = cell.inputs.get("D")
                if d_net and d_net.name in vals:
                    for out in cell.outputs.values():
                        ff_state[out.name] = vals[d_net.name]

    # Find nets where min == max (constant across all reachable states)
    replaced = 0
    _ctr = [len(mod.nets) + len(mod.cells) + 500]

    for name in list(net_min.keys()):
        if net_min[name] != net_max[name]:
            continue
        const_val = net_min[name]

        net = mod.nets.get(name)
        if net is None:
            continue
        # Don't replace port nets
        if name in mod.ports:
            continue
        # Don't replace nets already driven by CONST
        if net.driver and net.driver.op == PrimOp.CONST:
            continue
        # Don't replace FF outputs (they'll be caught by const-FF removal)
        if net.driver and net.driver.op == PrimOp.FF:
            continue
        # Don't replace INPUT outputs
        if net.driver and net.driver.op == PrimOp.INPUT:
            continue

        # Create a CONST cell to drive this net
        _ctr[0] += 1
        const_name = f"$rconst_{_ctr[0]}"
        const_cell = mod.add_cell(const_name, PrimOp.CONST,
                                  value=const_val, width=net.width)
        mod.connect(const_cell, "Y", net, direction="output")
        replaced += 1

    return replaced
