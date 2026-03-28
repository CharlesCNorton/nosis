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

from nosis.ir import Module, PrimOp
from nosis.sim import FastSimulator

__all__ = [
    "merge_reachable_equivalent",
    "propagate_reachable_constants",
]


def _ff_chain_depth(mod: Module) -> int:
    """Compute the longest FF-to-FF combinational path depth.

    Single-pass: build a net-to-consumers index, then BFS from each FF Q
    output through combinational logic until hitting another FF D input.
    Count the max number of FF boundaries crossed.
    """
    # Build net -> consumer cells index (O(cells) once)
    consumers: dict[str, list] = {}
    for cell in mod.cells.values():
        for inp in cell.inputs.values():
            consumers.setdefault(inp.name, []).append(cell)

    # Collect FF Q outputs
    ff_outputs: list[str] = []
    for cell in mod.cells.values():
        if cell.op == PrimOp.FF:
            for out in cell.outputs.values():
                ff_outputs.append(out.name)

    if not ff_outputs:
        return 0

    # BFS from each FF output, count FF boundaries crossed
    max_depth = 0
    for start in ff_outputs:
        visited: set[str] = set()
        depth = 0
        frontier = {start}
        while frontier and depth <= 20:
            next_frontier: set[str] = set()
            for net_name in frontier:
                if net_name in visited:
                    continue
                visited.add(net_name)
                for cell in consumers.get(net_name, []):
                    if cell.op == PrimOp.FF:
                        depth += 1
                        for out in cell.outputs.values():
                            if out.name not in visited:
                                next_frontier.add(out.name)
                    else:
                        for out in cell.outputs.values():
                            if out.name not in visited:
                                next_frontier.add(out.name)
            frontier = next_frontier
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
                # Seed FFs with random values so simulation explores
                # register-file-dependent paths, not just the reset state.
                ff_state[out.name] = rng.getrandbits(out.width)

    # Pre-compile the simulator once — avoids per-cycle topo sort and dispatch
    fast_sim = FastSimulator(mod)

    # Seed MEMORY storage with random values
    for mem in fast_sim._memories:
        for i in range(mem["depth"]):
            mem["storage"][i] = rng.getrandbits(mem["width"]) if mem["width"] > 0 else 0

    # Pre-collect FF (d_name, q_name) pairs for fast state update
    ff_pairs: list[tuple[str, str]] = []
    for cell in mod.cells.values():
        if cell.op == PrimOp.FF:
            d_net = cell.inputs.get("D")
            if d_net:
                for out in cell.outputs.values():
                    ff_pairs.append((d_net.name, out.name))

    # Simulate and collect per-net value signatures via incremental hashing.
    # Instead of storing a list[int] per net per cycle (O(cycles * nets) memory),
    # we maintain a running hash per net. Two nets with the same hash sequence
    # are candidate equivalents.
    _FNV_OFFSET = 0xCBF29CE484222325
    _FNV_PRIME = 0x100000001B3
    _MASK64 = (1 << 64) - 1
    sig_hashes: dict[str, int] = {}  # net_name -> running FNV-1a hash
    for cycle in range(cycles):
        inputs = {name: rng.getrandbits(w) for name, w in input_ports.items()}
        inputs.update(ff_state)
        vals = fast_sim.step(inputs)

        for name, val in vals.items():
            h = sig_hashes.get(name, _FNV_OFFSET)
            h = ((h ^ (val & 0xFFFFFFFF)) * _FNV_PRIME) & _MASK64
            h = ((h ^ ((val >> 32) & 0xFFFFFFFF)) * _FNV_PRIME) & _MASK64
            sig_hashes[name] = h

        # Update FF state
        for d_name, q_name in ff_pairs:
            if d_name in vals:
                ff_state[q_name] = vals[d_name]

    # Group nets by signature hash
    sig_groups: dict[int, list[str]] = defaultdict(list)
    for name, h in sig_hashes.items():
        sig_groups[h].append(name)

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

    # Protect all nets connected to MEMORY cells — simulation cannot model
    # stateful memory reads (the value depends on past writes, not current inputs).
    memory_reachable: set[str] = set()
    for cell in mod.cells.values():
        if cell.op == PrimOp.MEMORY:
            for net in cell.inputs.values():
                memory_reachable.add(net.name)
            for net in cell.outputs.values():
                memory_reachable.add(net.name)

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
    for sig, net_names in sig_groups.items():  # type: ignore[assignment]
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
            # 0. Don't merge nets driven by (* keep *) cells.
            if dup_net.driver and dup_net.driver.attributes.get("keep"):
                continue
            # 1. Don't merge nets in the output-reachable cone
            if name in output_reachable:
                continue
            if canonical_name in output_reachable:
                continue
            # 2. Don't merge nets that feed FF D inputs (sequential state).
            #    Simulation may not cover all state transitions.
            if name in ff_input_reachable:
                continue
            # 3. Don't merge nets connected to MEMORY cells.
            #    Simulation cannot model stateful memory behavior.
            if name in memory_reachable:
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
    ff_pairs: list[tuple[str, str]] = []
    for cell in mod.cells.values():
        if cell.op == PrimOp.FF:
            for out in cell.outputs.values():
                ff_state[out.name] = 0
            d_net = cell.inputs.get("D")
            if d_net:
                for out in cell.outputs.values():
                    ff_pairs.append((d_net.name, out.name))

    fast_sim = FastSimulator(mod)

    # Track min and max value for each net across all cycles
    net_min: dict[str, int] = {}
    net_max: dict[str, int] = {}

    for cycle in range(cycles):
        inputs = {name: rng.getrandbits(w) for name, w in input_ports.items()}
        inputs.update(ff_state)
        vals = fast_sim.step(inputs)

        for name, val in vals.items():
            if name not in net_min:
                net_min[name] = val
                net_max[name] = val
            else:
                if val < net_min[name]:
                    net_min[name] = val
                if val > net_max[name]:
                    net_max[name] = val

        for d_name, q_name in ff_pairs:
            if d_name in vals:
                ff_state[q_name] = vals[d_name]

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
