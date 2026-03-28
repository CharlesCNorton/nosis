"""Default optimization pipeline — orchestrates all passes."""

from __future__ import annotations

import random
from collections import defaultdict

import nosis.passes as _passes_pkg
from nosis.ir import Module, PrimOp
from nosis.passes.folding import constant_fold
from nosis.passes.identity import identity_simplify
from nosis.passes.dce import dead_code_eliminate
from nosis.passes.constff import remove_const_ffs
from nosis.passes.mux import merge_mux_chains, collapse_case_chains, simplify_constant_masks
from nosis.passes.equiv import (
    _simplify_mux_with_zero,
    _eliminate_functional_identities,
    _eliminate_dont_care_inputs,
    _merge_hit_equivalent,
)
from nosis.passes.misc import annotate_eq_carry

__all__ = ["run_default_passes"]


def run_default_passes(mod: Module, *, verify: bool = False) -> dict[str, int]:
    """Run the default optimization pipeline. Returns pass statistics.

    If *verify* is True, runs equivalence checking after each pass to
    confirm functional equivalence is preserved. Expensive — intended
    for debug and CI validation, not production synthesis.
    """
    from nosis.cse import eliminate_common_subexpressions
    from nosis.boolopt import boolean_optimize, tech_aware_optimize
    import copy

    def _check_equiv(before_mod: Module, after_mod: Module, pass_name: str) -> None:
        """Verify two modules are functionally equivalent via simulation.

        Uses FastSimulator on both modules with random inputs and compares
        output port values. This avoids structural issues with deepcopy
        and the formal equivalence checker's port-matching requirements.
        """
        if not verify:
            return
        import random
        from nosis.sim import FastSimulator

        # Collect input ports (by name) and output ports
        input_ports: dict[str, int] = {}
        output_ports: list[str] = []
        for cell in before_mod.cells.values():
            if cell.op == PrimOp.INPUT:
                pn = str(cell.params.get("port_name", ""))
                for out in cell.outputs.values():
                    input_ports[pn] = out.width
            elif cell.op == PrimOp.OUTPUT:
                for inp in cell.inputs.values():
                    output_ports.append(inp.name)

        if not input_ports or not output_ports:
            return

        total_bits = sum(input_ports.values())
        if total_bits > 20:
            return

        # Map output port names to actual net names in each module
        def _out_net_names(m: Module) -> dict[str, str]:
            result: dict[str, str] = {}
            for cell in m.cells.values():
                if cell.op == PrimOp.OUTPUT:
                    pn = str(cell.params.get("port_name", ""))
                    for inp in cell.inputs.values():
                        result[pn or inp.name] = inp.name
            return result

        out_map_before = _out_net_names(before_mod)
        out_map_after = _out_net_names(after_mod)

        sim_before = FastSimulator(before_mod)
        sim_after = FastSimulator(after_mod)
        rng = random.Random(42)

        for _ in range(min(1000, 1 << total_bits)):
            inputs = {name: rng.getrandbits(w) for name, w in input_ports.items()}
            vals_a = sim_before.step(inputs)
            vals_b = sim_after.step(inputs)
            for out_label in output_ports:
                net_a = out_map_before.get(out_label, out_label)
                net_b = out_map_after.get(out_label, out_label)
                va = vals_a.get(net_a, vals_a.get(out_label, 0))
                vb = vals_b.get(net_b, vals_b.get(out_label, 0))
                if va != vb:
                    raise AssertionError(
                        f"Equivalence check failed after '{pass_name}': "
                        f"output '{out_label}' differs at inputs {inputs}: "
                        f"before={va}, after={vb}"
                    )

    stats: dict[str, int] = {}
    prev_cells = len(mod.cells)

    # Protect PMUX/MEMORY outputs and their full combinational fanout
    # (stop at FF boundaries). This prevents the optimizer from collapsing
    # register-file-dependent logic through cascading constant propagation.
    _mem_protect: set[str] = set()
    _mpwl: list[str] = []
    for _c in mod.cells.values():
        if _c.op in (PrimOp.MEMORY, PrimOp.PMUX):
            for _o in _c.outputs.values():
                _mem_protect.add(_o.name)
                _mpwl.append(_o.name)
    while _mpwl:
        _mn = _mpwl.pop()
        for _c in mod.cells.values():
            if _c.op == PrimOp.FF:
                continue  # stop at FF boundaries
            for _inp in _c.inputs.values():
                if _inp.name == _mn:
                    for _o in _c.outputs.values():
                        if _o.name not in _mem_protect:
                            _mem_protect.add(_o.name)
                            _mpwl.append(_o.name)
    _passes_pkg._active_mem_protect = _mem_protect

    # Skip verification for large designs (deep-copy is O(cells))
    if verify and len(mod.cells) > 500:
        verify = False
    snapshot = copy.deepcopy(mod) if verify else None

    for iteration in range(6):
        if verify:
            round_snapshot = copy.deepcopy(mod)

        cf = constant_fold(mod)
        # Item 2: run identity simplification to local fixed point
        ident = 0
        for _ in range(10):
            _id = identity_simplify(mod)
            if _id == 0:
                break
            ident += _id
        bo = boolean_optimize(mod)
        cff = remove_const_ffs(mod)
        cse = eliminate_common_subexpressions(mod)
        fi = _eliminate_functional_identities(mod)
        hit = _merge_hit_equivalent(mod)
        dci = _eliminate_dont_care_inputs(mod)
        mm = merge_mux_chains(mod)
        # Item 1: collapse case statement EQ+MUX chains
        cc = collapse_case_chains(mod)
        mz = 0  # _simplify_mux_with_zero disabled — width mismatch bug
        # Item 5: constant mask identification
        cm = simplify_constant_masks(mod)
        ta = tech_aware_optimize(mod)
        dce = dead_code_eliminate(mod)

        total = cf + ident + bo + cff + cse + fi + hit + dci + mm + cc + mz + cm + ta + dce
        stats[f"round_{iteration}"] = total

        if verify:
            _check_equiv(round_snapshot, mod, f"round_{iteration}")

        cur_cells = len(mod.cells)
        if cur_cells == prev_cells:
            break
        prev_cells = cur_cells

    if snapshot is not None:
        _check_equiv(snapshot, mod, "iterative_optimization")

    # Timing-driven extra round: identify critical path, re-optimize those cells
    from nosis.timing import analyze_timing
    _timing = analyze_timing(mod)
    if _timing.critical_path and _timing.critical_path.cells:
        _crit_cells = set(_timing.critical_path.cells)
        # Run one more round of optimization focused on critical-path cells
        # by attempting to simplify their input cones
        _crit_cf = constant_fold(mod)
        _crit_id = identity_simplify(mod)
        _crit_dce = dead_code_eliminate(mod)
        stats["timing_driven"] = _crit_cf + _crit_id + _crit_dce
    else:
        stats["timing_driven"] = 0

    # Backward don't-care propagation (duality principle)
    from nosis.dontcare import propagate_dont_cares
    stats["dont_care"] = propagate_dont_cares(mod)
    stats["dce_dc"] = dead_code_eliminate(mod)

    # Reachable-state equivalence merging (HoTT quotient).
    # Note: some SoC output ports are undriven due to a hierarchy lowering
    # issue (sub-instance output wiring), not due to reqmerge. The reqmerge
    # is sound for nets that ARE connected.
    from nosis.reqmerge import merge_reachable_equivalent
    stats["req_merge"] = merge_reachable_equivalent(mod, cycles=500)
    dead_code_eliminate(mod)

    # SAT-proven constant replacement: with the sound reqmerge (FF-input
    # and output-reachable guards), the cascade that removed output port
    # drivers is prevented. Re-enabled.
    from nosis.satconst import prove_constants_sat
    from nosis.sim import FastSimulator

    _rng2 = random.Random(42)
    _ip2: dict[str, int] = {}
    for _c in mod.cells.values():
        if _c.op == PrimOp.INPUT:
            for _o in _c.outputs.values():
                _ip2[_o.name] = _o.width
    _ff2: dict[str, int] = {}
    _ff_pairs2: list[tuple[str, str]] = []
    for _c in mod.cells.values():
        if _c.op == PrimOp.FF:
            for _o in _c.outputs.values():
                _ff2[_o.name] = _rng2.getrandbits(_o.width)
            _d = _c.inputs.get("D")
            if _d:
                for _o in _c.outputs.values():
                    _ff_pairs2.append((_d.name, _o.name))
    _fast2 = FastSimulator(mod)
    # Seed memory storage with random values
    for _mem in _fast2._memories:
        for _mi in range(_mem["depth"]):
            _mem["storage"][_mi] = _rng2.getrandbits(_mem["width"]) if _mem["width"] > 0 else 0
    _nv2: dict[str, set[int]] = {}
    _nv2_seq: dict[str, list[int]] = {}  # per-vector value sequences for equiv detection
    for _ in range(1000):
        _si2 = {n: _rng2.getrandbits(w) for n, w in _ip2.items()}
        _si2.update(_ff2)
        _vs2 = _fast2.step(_si2)
        for _n, _v in _vs2.items():
            _nv2.setdefault(_n, set()).add(_v)
            _nv2_seq.setdefault(_n, []).append(_v)
        for _dn, _qn in _ff_pairs2:
            if _dn in _vs2:
                _ff2[_qn] = _vs2[_dn]

    # Use the global memory protection set.
    _mem_fanout2 = _passes_pkg._active_mem_protect

    _cands2 = {}
    for _n, _svs in _nv2.items():
        if len(_svs) != 1:
            continue
        _net = mod.nets.get(_n)
        if _net is None or _n in mod.ports:
            continue
        if _net.driver and _net.driver.op in (PrimOp.CONST, PrimOp.INPUT, PrimOp.FF):
            continue
        if _n in _mem_fanout2:
            continue
        _cands2[_n] = next(iter(_svs))

    _proven = prove_constants_sat(mod, _cands2, max_cone_inputs=16)
    _ctr3 = [len(mod.nets) + len(mod.cells) + 1200]
    _nrep2 = 0
    for _n, _v in _proven.items():
        _net = mod.nets.get(_n)
        if _net is None:
            continue
        _ctr3[0] += 1
        _cc = mod.add_cell(f"$pconst_{_ctr3[0]}", PrimOp.CONST, value=_v, width=_net.width)
        mod.connect(_cc, "Y", _net, direction="output")
        _nrep2 += 1
    stats["sat_const"] = _nrep2
    if _nrep2 > 0:
        constant_fold(mod)
        identity_simplify(mod)
        dead_code_eliminate(mod)

    # Item 7: SAT-proven equivalence merging.
    # Find net pairs with identical value signatures from the simulation
    # data, then prove equivalence via exhaustive cone evaluation.
    from nosis.satconst import prove_equivalences_sat

    _sig_groups: dict[tuple, list[str]] = defaultdict(list)
    for _n, _seq in _nv2_seq.items():
        if _n in _nv2 and len(_nv2[_n]) <= 1:
            continue  # constants handled by sat_const
        _net = mod.nets.get(_n)
        if _net is None or _n in mod.ports:
            continue
        if _net.driver and _net.driver.op in (PrimOp.CONST, PrimOp.INPUT, PrimOp.FF):
            continue
        if _n in _mem_fanout2:
            continue
        # Use the value SEQUENCE as the grouping key — only nets that
        # produce identical values on every vector are candidates
        sig = tuple(_seq)
        _sig_groups[sig].append(_n)

    _eq_candidates: list[tuple[str, str]] = []
    for sig, nets in _sig_groups.items():
        if len(nets) < 2:
            continue
        # Only try pairs with the same width
        for i in range(len(nets)):
            ni = mod.nets.get(nets[i])
            if ni is None:
                continue
            for j in range(i + 1, min(i + 5, len(nets))):  # limit pairs per group
                nj = mod.nets.get(nets[j])
                if nj is None or nj.width != ni.width:
                    continue
                _eq_candidates.append((nets[i], nets[j]))

    _proven_eq = prove_equivalences_sat(mod, _eq_candidates, max_cone_inputs=16)
    _eq_merged = 0
    for _na, _nb in _proven_eq:
        _net_a = mod.nets.get(_na)
        _net_b = mod.nets.get(_nb)
        if _net_a is None or _net_b is None:
            continue
        # Redirect all consumers of net_b to net_a
        for _c in mod.cells.values():
            for _pn, _pnet in list(_c.inputs.items()):
                if _pnet is _net_b or _pnet.name == _nb:
                    _c.inputs[_pn] = _net_a
        for _pn, _pnet in list(mod.ports.items()):
            if _pnet is _net_b:
                mod.ports[_pn] = _net_a
        _eq_merged += 1

    stats["sat_equiv"] = _eq_merged
    if _eq_merged > 0:
        dead_code_eliminate(mod)

    stats["cut_map"] = 0

    # BDD-inspired decode function minimization
    from nosis.bdd import minimize_decode_functions
    stats["bdd_minimize"] = minimize_decode_functions(mod, max_inputs=10)
    if stats["bdd_minimize"] > 0:
        constant_fold(mod)
        identity_simplify(mod)

    stats["dce_final"] = dead_code_eliminate(mod)

    # Register retiming: move FFs across single-fanout combinational cells
    from nosis.retiming import retime_forward, duplicate_high_fanout
    stats["retime_fwd"] = 0  # disabled: creates D=Q loops on multi-module designs
    stats["fanout_dup"] = duplicate_high_fanout(mod, threshold=64)
    if stats["retime_fwd"] > 0 or stats["fanout_dup"] > 0:
        dead_code_eliminate(mod)

    stats["cdc_sync"] = 0

    # Item 6: annotate EQ comparisons for carry chain mapping
    stats["eq_carry"] = annotate_eq_carry(mod)

    # Re-run inference after optimization
    from nosis.carry import infer_carry_chains
    from nosis.bram import infer_brams
    from nosis.dsp import infer_dsps
    stats["carry_infer"] = infer_carry_chains(mod)
    stats["bram_infer"] = infer_brams(mod)
    stats["dsp_infer"] = infer_dsps(mod)

    # Break combinational self-loops: optimization may merge a MUX output
    # net with one of its inputs, creating input=output on the same cell.
    # Resolve by finding the FF Q net that should provide the "hold" value.
    _ff_q_for_target: dict[str, Net] = {}
    for _c in mod.cells.values():
        if _c.op == PrimOp.FF:
            _tgt = _c.params.get("ff_target", "")
            for _q in _c.outputs.values():
                _ff_q_for_target[_tgt] = _q
    # Also break FF D=Q self-loops (FF always holds → replace D with const)
    _ff_dq_broken = 0
    for _c in list(mod.cells.values()):
        if _c.op != PrimOp.FF:
            continue
        _d = _c.inputs.get("D")
        if _d is None:
            continue
        _out_ids = {id(n) for n in _c.outputs.values()}
        if id(_d) in _out_ids:
            # D == Q: FF always holds. Replace D with const 0.
            _ctr_ff = len(mod.nets) + len(mod.cells) + 6000 + _ff_dq_broken
            _zn = mod.add_net(f"$ff_dq_break_{_ctr_ff}", _d.width)
            _zc = mod.add_cell(f"$ff_dq_const_{_ctr_ff}", PrimOp.CONST, value=0, width=_d.width)
            mod.connect(_zc, "Y", _zn, direction="output")
            _c.inputs["D"] = _zn
            _ff_dq_broken += 1
    stats["ff_dq_loops"] = _ff_dq_broken

    _loops_broken = 0
    for _c in mod.cells.values():
        if _c.op in (PrimOp.FF, PrimOp.INPUT, PrimOp.OUTPUT, PrimOp.CONST):
            continue
        _out_ids = {id(n) for n in _c.outputs.values()}
        for _pn, _pnet in list(_c.inputs.items()):
            if id(_pnet) not in _out_ids:
                continue
            # Self-loop: find a FF Q net to replace with
            _replaced = False
            for _qname, _qnet in _ff_q_for_target.items():
                if _qnet.width == _pnet.width and _pnet.name in (
                    _qname, _qnet.name,
                    *[n.name for n in _c.outputs.values()],
                ):
                    _c.inputs[_pn] = _qnet
                    _replaced = True
                    _loops_broken += 1
                    break
            if not _replaced:
                # Last resort: create a const 0 to break the loop
                _ctr_lb = len(mod.nets) + len(mod.cells) + 5000
                _znet = mod.add_net(f"$loop_break_{_ctr_lb}", _pnet.width)
                _zc = mod.add_cell(f"$loop_const_{_ctr_lb}", PrimOp.CONST, value=0, width=_pnet.width)
                mod.connect(_zc, "Y", _znet, direction="output")
                _c.inputs[_pn] = _znet
                _loops_broken += 1
    stats["loops_broken"] = _loops_broken

    # Post-optimization integrity check: no internal net should be undriven
    # (ports are exempt — they're driven externally)
    undriven = []
    for name, net in mod.nets.items():
        if name in mod.ports:
            continue
        # Check if any INPUT cell drives this net (it's a port net)
        is_port_net = any(
            c.op == PrimOp.INPUT and any(o.name == name for o in c.outputs.values())
            for c in mod.cells.values()
        )
        if is_port_net:
            continue
        if net.driver is None:
            # Check if any cell reads this net — if not, it's truly dead
            used = any(name in [i.name for i in c.inputs.values()] for c in mod.cells.values())
            if used:
                undriven.append(name)
    stats["undriven_internal_nets"] = len(undriven)

    return stats
