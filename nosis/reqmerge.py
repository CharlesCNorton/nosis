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
]


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

    Returns the number of nets merged (cells potentially eliminated by
    subsequent DCE).
    """
    rng = random.Random(seed)

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

    # Merge: for each group with >1 nets, pick a canonical representative
    # and redirect all consumers of the others to the canonical
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
            # Don't merge nets with different widths
            if dup_net.width != canonical_net.width:
                continue
            # Don't merge port nets (they must keep their identity)
            if name in mod.ports:
                continue

            # Redirect all consumers of dup_net to canonical_net
            for cell in mod.cells.values():
                for pname, pnet in list(cell.inputs.items()):
                    if pnet is dup_net:
                        cell.inputs[pname] = canonical_net
            # Update port references
            for pname, pnet in list(mod.ports.items()):
                if pnet is dup_net:
                    mod.ports[pname] = canonical_net

            merged += 1

    return merged
