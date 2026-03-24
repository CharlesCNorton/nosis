"""Nosis post-mapping LUT optimization.

After tech mapping, LUT4 cells are simplified and merged on the ECP5 netlist:

  - Constant input simplification: reduce truth tables when inputs are tied
  - Chain merging: compose chained LUT4 pairs into single LUT4 cells,
    self-checked by exhaustive evaluation of all 16 input combinations
  - Deduplication: eliminate LUT4 cells with identical INIT and inputs
  - Dead LUT elimination: remove LUT4 cells whose output is unconsumed
  - Combinational loop breaking: tie self-referencing inputs to constants

``pack_slices`` runs these passes to a fixed point.
"""

from __future__ import annotations

from nosis.techmap.netlist import ECP5Cell, ECP5Netlist

__all__ = [
    "pack_slices",
    "simplify_constant_luts",
    "merge_lut_chains",
    "deduplicate_luts",
    "absorb_buffer_luts",
    "resynth_cones",
    "trim_dead_carry_tails",
    "break_comb_loops",
]

# Ports that drive bits. Everything else on a cell reads bits.
_OUTPUT_PORTS = {"Z", "Q", "S0", "S1", "COUT", "DO", "F", "F0", "F1",
                 "OFX0", "OFX1"}


def _get_init(cell: ECP5Cell) -> int | None:
    """Parse the INIT parameter as an integer. Returns None on failure."""
    init_str = cell.parameters.get("INIT", "")
    if not init_str:
        return None
    try:
        # INIT is a 16-char binary string (e.g., "1000100010001000")
        return int(init_str, 2)
    except (ValueError, TypeError):
        pass
    try:
        # Fallback: hex format from older code paths
        return int(init_str, 16)
    except (ValueError, TypeError):
        return None


def _set_init(cell: ECP5Cell, value: int) -> None:
    """Set the INIT parameter as a 16-char binary string."""
    cell.parameters["INIT"] = format(value & 0xFFFF, "016b")


def _eval_lut4(init: int, a: int, b: int, c: int, d: int) -> int:
    """Evaluate a LUT4 truth table for given input values (0 or 1)."""
    idx = a | (b << 1) | (c << 2) | (d << 3)
    return (init >> idx) & 1


def simplify_constant_luts(netlist: ECP5Netlist) -> int:
    """Simplify LUT4 cells with tied-constant inputs.

    When a LUT4 input is tied to constant 0 or 1, the truth table is
    reduced over the reachable entries. If the output is constant across
    those entries, the LUT is a constant: every consumer of its output is
    tied to "0"/"1" (constant propagation) and the LUT is removed. Tying
    the consumers — rather than merely deleting the cell — is what keeps
    them from being left with an undriven input bit.

    Returns the number of LUTs simplified or eliminated.
    """
    simplified = 0
    to_remove: list[str] = []

    # Index of who reads each signal bit, so a constant output can be
    # propagated to all of its consumers.
    bit_consumers: dict[int, list[tuple]] = {}
    for _cn, _c in netlist.cells.items():
        for _pn, _bits in _c.ports.items():
            if _pn in _OUTPUT_PORTS:
                continue
            for _i, _b in enumerate(_bits):
                if isinstance(_b, int) and _b >= 2:
                    bit_consumers.setdefault(_b, []).append((_c, _pn, _i))
    port_consumers: dict[int, list[tuple]] = {}
    for _pinfo in netlist.ports.values():
        for _i, _b in enumerate(_pinfo.get("bits", [])):
            if isinstance(_b, int) and _b >= 2:
                port_consumers.setdefault(_b, []).append((_pinfo, _i))

    for name, cell in netlist.cells.items():
        if cell.cell_type != "LUT4":
            continue

        init = _get_init(cell)
        if init is None:
            continue

        # Check which inputs are constants
        const_inputs: dict[int, int] = {}  # pin_index -> 0 or 1
        for pin_idx, pin_name in enumerate(["A", "B", "C", "D"]):
            bits = cell.ports.get(pin_name, ["0"])
            if bits and isinstance(bits[0], str):
                if bits[0] == "0":
                    const_inputs[pin_idx] = 0
                elif bits[0] == "1":
                    const_inputs[pin_idx] = 1

        if not const_inputs:
            continue

        # Reduce truth table by substituting constant values, tracking the
        # set of output values over the reachable (const-consistent) entries.
        new_init = 0
        reachable: set[int] = set()
        for i in range(16):
            if any(((i >> pi) & 1) != pv for pi, pv in const_inputs.items()):
                continue
            bitv = (init >> i) & 1
            reachable.add(bitv)
            if bitv:
                new_init |= (1 << i)

        if new_init != init:
            _set_init(cell, new_init)
            simplified += 1

        # Constant output? Propagate it to every consumer, then drop the LUT.
        if reachable in ({0}, {1}):
            const_str = "1" if reachable == {1} else "0"
            z_bits = cell.ports.get("Z", [])
            if z_bits and isinstance(z_bits[0], int):
                zbit = z_bits[0]
                for c, pn, idx in bit_consumers.get(zbit, []):
                    if c is cell:
                        continue
                    if idx < len(c.ports.get(pn, [])):
                        c.ports[pn][idx] = const_str
                for pinfo, idx in port_consumers.get(zbit, []):
                    bits = pinfo.get("bits", [])
                    if idx < len(bits):
                        bits[idx] = const_str
                to_remove.append(name)

    for name in to_remove:
        if name in netlist.cells:
            del netlist.cells[name]

    return simplified


def _eliminate_dead_luts(netlist: ECP5Netlist) -> int:
    """Remove LUT4 cells whose output bit is unconsumed."""
    used: set[int] = set()
    for cell in netlist.cells.values():
        for port_name, bits in cell.ports.items():
            if port_name not in ("Z", "Q", "COUT", "S0", "S1"):
                for b in bits:
                    if isinstance(b, int) and b >= 2:
                        used.add(b)
    for port_info in netlist.ports.values():
        for b in port_info.get("bits", []):
            if isinstance(b, int) and b >= 2:
                used.add(b)

    to_remove: list[str] = []
    for name, cell in netlist.cells.items():
        if cell.cell_type != "LUT4":
            continue
        z = cell.ports.get("Z", [])
        if z and isinstance(z[0], int) and z[0] not in used:
            to_remove.append(name)

    for name in to_remove:
        del netlist.cells[name]
    return len(to_remove)


def resynth_cones(netlist: ECP5Netlist) -> int:
    """Collapse single-fanout LUT cones by Shannon decomposition.

    Pairwise chain merging cannot cross the 4-input barrier, so a cone that
    computes a 5-input function accretes as a deep tower of half-filled LUTs.
    yosys/ABC instead computes each output as one shallow function of its
    support. This pass approximates that: for every LUT, grow the cone of
    internal single-fanout LUT feeders (leaves = FFs, carries, ports,
    multi-fanout LUTs), take the cone's exhaustive truth table over its
    support, and re-emit:

      support <= 4: one LUT4 (catches what pairwise merging missed);
      support == 5: Shannon-decompose on one variable — two cofactor LUT4s
                    plus a 3-input mux LUT4, replacing cones of 4+ cells
                    with exactly 3.

    Every replacement is verified against the original cone over all 2^s
    input assignments before it is committed.

    Returns the net number of LUT4 cells removed.
    """
    removed = 0

    # Global fanout per bit (cells + module ports).
    fanout: dict[int, int] = {}
    for cell in netlist.cells.values():
        for pn, bits in cell.ports.items():
            if pn in _OUTPUT_PORTS:
                continue
            for b in bits:
                if isinstance(b, int) and b >= 2:
                    fanout[b] = fanout.get(b, 0) + 1
    for pinfo in netlist.ports.values():
        for b in pinfo.get("bits", []):
            if isinstance(b, int) and b >= 2:
                fanout[b] = fanout.get(b, 0) + 1

    # Producer index: output bit -> LUT4 cell.
    lut_of: dict[int, ECP5Cell] = {}
    name_of: dict[int, str] = {}
    for name, cell in netlist.cells.items():
        if cell.cell_type != "LUT4":
            continue
        z = cell.ports.get("Z", [None])[0]
        if isinstance(z, int) and z >= 2:
            lut_of[z] = cell
            name_of[z] = name

    consumed: set[str] = set()  # cells deleted this pass

    def _pins(cell: ECP5Cell) -> list:
        return [cell.ports.get(p, ["0"])[0] for p in ("A", "B", "C", "D")]

    def _grow_cone(root: ECP5Cell) -> tuple[list[ECP5Cell], list[int]] | None:
        """Return (cone cells, support bits) or None if not collapsible."""
        cone: list[ECP5Cell] = []
        cone_ids: set[int] = set()
        support: list[int] = []
        stack = [root]
        while stack:
            cell = stack.pop()
            if id(cell) in cone_ids:
                continue
            cone_ids.add(id(cell))
            cone.append(cell)
            if len(cone) > 12:
                return None
            for b in _pins(cell):
                if isinstance(b, str):
                    continue
                feeder = lut_of.get(b)
                internal = (feeder is not None and feeder is not root
                            and id(feeder) not in cone_ids
                            and fanout.get(b, 0) == 1)
                if internal:
                    stack.append(feeder)
                elif feeder is not None and id(feeder) in cone_ids:
                    return None  # feeder reached by two paths: reconvergence
                elif b not in support:
                    support.append(b)
                    if len(support) > 6:
                        return None
        return cone, support

    def _eval_cone(cone: list[ECP5Cell], root: ECP5Cell, assign: dict[int, int]) -> int:
        vals = dict(assign)
        memo: dict[int, int] = {}

        def _val(b) -> int:
            if isinstance(b, str):
                return 1 if b == "1" else 0
            if b in vals:
                return vals[b]
            if b in memo:
                return memo[b]
            cell = lut_of.get(b)
            init = _get_init(cell) or 0
            p = [_val(x) for x in _pins(cell)]
            r = _eval_lut4(init, p[0], p[1], p[2], p[3])
            memo[b] = r
            return r

        z = root.ports.get("Z", [None])[0]
        return _val(z)

    for name in sorted(list(netlist.cells.keys())):
        cell = netlist.cells.get(name)
        if cell is None or name in consumed or cell.cell_type != "LUT4":
            continue
        z = cell.ports.get("Z", [None])[0]
        if not isinstance(z, int) or fanout.get(z, 0) == 0:
            continue
        grown = _grow_cone(cell)
        if grown is None:
            continue
        cone, support = grown
        s = len(support)
        if len(cone) < 2:
            continue

        # Exhaustive truth table over the support.
        tt = 0
        for a in range(1 << s):
            assign = {b: (a >> i) & 1 for i, b in enumerate(support)}
            if _eval_cone(cone, cell, assign):
                tt |= 1 << a

        # For 5-6 variable cones, try Ashenhurst decomposition: if some
        # 4-variable bound set B has column multiplicity <= 2 (every B
        # assignment produces one of at most two residual functions of the
        # free variables), then f = h(g(B), F) — two LUT4s total.
        decomp = None  # (bound_idx, free_idx, g_init, h_init) | ("indep", free_idx, h_init)
        if s in (5, 6):
            from itertools import combinations
            nfree = s - 4
            for bound in combinations(range(s), 4):
                free = [i for i in range(s) if i not in bound]
                patterns: dict[int, tuple] = {}
                for b_assign in range(16):
                    vec = []
                    for f_assign in range(1 << nfree):
                        idx = 0
                        for j, si in enumerate(bound):
                            if (b_assign >> j) & 1:
                                idx |= 1 << si
                        for j, si in enumerate(free):
                            if (f_assign >> j) & 1:
                                idx |= 1 << si
                        vec.append((tt >> idx) & 1)
                    patterns[b_assign] = tuple(vec)
                distinct = sorted(set(patterns.values()))
                if len(distinct) == 1:
                    # Independent of the bound set: f = h(F) alone.
                    h_init = 0
                    vec = distinct[0]
                    for idx in range(16):
                        if vec[idx & ((1 << nfree) - 1)]:
                            h_init |= 1 << idx
                    decomp = ("indep", free, h_init)
                    break
                if len(distinct) == 2:
                    p0, p1 = distinct
                    g_init = 0
                    for b_assign in range(16):
                        if patterns[b_assign] == p1:
                            g_init |= 1 << b_assign
                    # h pins: [g] + free vars; h(g, F) = p1[F] if g else p0[F]
                    h_init = 0
                    for idx in range(16):
                        gv = idx & 1
                        fa = (idx >> 1) & ((1 << nfree) - 1)
                        if (p1 if gv else p0)[fa]:
                            h_init |= 1 << idx
                    decomp = (bound, free, g_init, h_init)
                    break

        if s <= 4:
            new_count = 1
        elif decomp is not None:
            new_count = 1 if decomp[0] == "indep" else 2
        elif s == 5:
            new_count = 3  # Shannon fallback
        else:
            continue  # 6-input, not decomposable: no profitable LUT4-only form
        if len(cone) <= new_count:
            continue

        old_root_pins = [b for b in _pins(cell) if isinstance(b, int) and b >= 2]

        if s <= 4:
            # One LUT4: pins = support (padded), INIT = tt replicated across
            # the unused high pins.
            init = 0
            for idx in range(16):
                if (tt >> (idx & ((1 << s) - 1))) & 1:
                    init |= 1 << idx
            for pi, p in enumerate(("A", "B", "C", "D")):
                cell.ports[p] = [support[pi]] if pi < s else ["0"]
            _set_init(cell, init)
            for b in support:
                fanout[b] = fanout.get(b, 0) + 1
        elif decomp is not None and decomp[0] == "indep":
            # Function is independent of some 4-variable bound set: one LUT
            # on the free variables alone.
            _, free, h_init = decomp
            fpins = [support[i] for i in free]
            for pi, p in enumerate(("A", "B", "C", "D")):
                cell.ports[p] = [fpins[pi]] if pi < len(fpins) else ["0"]
            _set_init(cell, h_init)
            for b in fpins:
                fanout[b] = fanout.get(b, 0) + 1
        elif decomp is not None:
            # Ashenhurst: g on the bound set, root becomes h(g, free vars).
            bound, free, g_init, h_init = decomp
            bpins = [support[i] for i in bound]
            fpins = [support[i] for i in free]
            gb = netlist.alloc_bit()
            gc = netlist.add_cell(name + "$dc0", "LUT4")
            _set_init(gc, g_init)
            for pi, p in enumerate(("A", "B", "C", "D")):
                gc.ports[p] = [bpins[pi]]
            gc.ports["Z"] = [gb]
            hpins = [gb] + fpins
            for pi, p in enumerate(("A", "B", "C", "D")):
                cell.ports[p] = [hpins[pi]] if pi < len(hpins) else ["0"]
            _set_init(cell, h_init)
            lut_of[gb] = gc
            name_of[gb] = name + "$dc0"
            fanout[gb] = 1
            for b in bpins:
                fanout[b] = fanout.get(b, 0) + 1
            for b in fpins:
                fanout[b] = fanout.get(b, 0) + 1
        else:
            # Shannon on the last support variable.
            split = support[4]
            lows = support[:4]
            init0 = 0
            init1 = 0
            for idx in range(16):
                if (tt >> idx) & 1:
                    init0 |= 1 << idx
                if (tt >> (idx | 16)) & 1:
                    init1 |= 1 << idx
            b0 = netlist.alloc_bit()
            c0 = netlist.add_cell(name + "$sh0", "LUT4")
            _set_init(c0, init0)
            for pi, p in enumerate(("A", "B", "C", "D")):
                c0.ports[p] = [lows[pi]]
            c0.ports["Z"] = [b0]
            b1 = netlist.alloc_bit()
            c1 = netlist.add_cell(name + "$sh1", "LUT4")
            _set_init(c1, init1)
            for pi, p in enumerate(("A", "B", "C", "D")):
                c1.ports[p] = [lows[pi]]
            c1.ports["Z"] = [b1]
            # Root becomes the mux: Z = split ? f1 : f0, pins A=f0 B=f1 C=split.
            mux_init = 0
            for idx in range(16):
                pa, pb, pc = idx & 1, (idx >> 1) & 1, (idx >> 2) & 1
                if (pb if pc else pa):
                    mux_init |= 1 << idx
            cell.ports["A"] = [b0]
            cell.ports["B"] = [b1]
            cell.ports["C"] = [split]
            cell.ports["D"] = ["0"]
            _set_init(cell, mux_init)
            lut_of[b0] = c0
            lut_of[b1] = c1
            name_of[b0] = name + "$sh0"
            name_of[b1] = name + "$sh1"
            fanout[b0] = 1
            fanout[b1] = 1
            for b in lows:
                fanout[b] = fanout.get(b, 0) + 2  # read by both cofactor LUTs
            fanout[split] = fanout.get(split, 0) + 1

        # Verify the replacement over all assignments; roll back on mismatch.
        ok = True
        for a in range(1 << s):
            assign = {b: (a >> i) & 1 for i, b in enumerate(support)}
            if _eval_cone([cell], cell, assign) != ((tt >> a) & 1):
                ok = False
                break
        if not ok:
            raise AssertionError(
                f"resynth_cones self-check failed at {name}: decomposition "
                f"does not match the cone truth table")

        # Delete the interior cells (all except the root, which was reused).
        for c in cone:
            if c is cell:
                continue
            cname = None
            zz = c.ports.get("Z", [None])[0]
            if isinstance(zz, int):
                cname = name_of.get(zz)
                lut_of.pop(zz, None)
            if cname and cname in netlist.cells:
                del netlist.cells[cname]
                consumed.add(cname)
        removed += len(cone) - new_count
        # Release the replaced reads from the fanout map so neighboring
        # cones see accurate counts this same pass: the interior cells'
        # pins and the root's former pins are gone.
        for c in cone:
            if c is cell:
                continue
            for b in _pins(c):
                if isinstance(b, int) and b >= 2:
                    fanout[b] = fanout.get(b, 0) - 1
        for b in old_root_pins:
            fanout[b] = fanout.get(b, 0) - 1

    return removed


def absorb_buffer_luts(netlist: ECP5Netlist) -> int:
    """Remove LUT4 cells that are pure pass-throughs of one input pin.

    Slicing, width reconstruction, and merging leave LUTs whose truth table,
    over the reachable assignments of their tied-constant pins, equals one
    free pin verbatim. Such a cell is a wire: every reader of its output is
    rewired to the source bit and the LUT is deleted.

    Returns the number of buffer LUTs absorbed.
    """
    # Consumer index: bit -> [(cell, port, idx)] plus module-port slots.
    bit_consumers: dict[int, list[tuple]] = {}
    for cell in netlist.cells.values():
        for pn, bits in cell.ports.items():
            if pn in _OUTPUT_PORTS:
                continue
            for i, b in enumerate(bits):
                if isinstance(b, int) and b >= 2:
                    bit_consumers.setdefault(b, []).append((cell, pn, i))
    port_consumers: dict[int, list[tuple]] = {}
    for pinfo in netlist.ports.values():
        for i, b in enumerate(pinfo.get("bits", [])):
            if isinstance(b, int) and b >= 2:
                port_consumers.setdefault(b, []).append((pinfo, i))

    absorbed = 0
    for name, cell in list(netlist.cells.items()):
        if cell.cell_type != "LUT4":
            continue
        init = _get_init(cell)
        if init is None:
            continue
        pins = [cell.ports.get(p, ["0"])[0] for p in ("A", "B", "C", "D")]
        tied: dict[int, int] = {}
        free: list[int] = []
        for pi, b in enumerate(pins):
            if isinstance(b, str):
                tied[pi] = 1 if b == "1" else 0
            else:
                free.append(pi)
        if not free:
            continue
        source_pin = None
        for cand in free:
            ok = True
            for assign in range(1 << len(free)):
                a = [0, 0, 0, 0]
                for pi, v in tied.items():
                    a[pi] = v
                for j, pi in enumerate(free):
                    a[pi] = (assign >> j) & 1
                if _eval_lut4(init, a[0], a[1], a[2], a[3]) != a[cand]:
                    ok = False
                    break
            if ok:
                source_pin = cand
                break
        if source_pin is None:
            continue
        src_bit = pins[source_pin]
        z = cell.ports.get("Z", [None])[0]
        if not isinstance(z, int) or z < 2 or not isinstance(src_bit, int):
            continue
        for c, pn, i in bit_consumers.get(z, []):
            if c is cell:
                continue
            if i < len(c.ports.get(pn, [])):
                c.ports[pn][i] = src_bit
                bit_consumers.setdefault(src_bit, []).append((c, pn, i))
        for pinfo, i in port_consumers.get(z, []):
            bits = pinfo.get("bits", [])
            if i < len(bits):
                bits[i] = src_bit
                port_consumers.setdefault(src_bit, []).append((pinfo, i))
        del netlist.cells[name]
        absorbed += 1

    return absorbed


def trim_dead_carry_tails(netlist: ECP5Netlist) -> int:
    """Remove CCU2C carry cells none of whose outputs are read.

    Register width reduction and dead-LUT elimination leave the high cells of
    a full-width carry chain with unread sum outputs, but each dead cell's
    COUT is still read by the *next* dead cell, so a one-shot liveness check
    sees only the chain end. Reference-count the bits and cascade: deleting
    the end cell releases its CIN, which kills its feeder, and the dead tail
    unravels back to the last cell whose sum bits are actually consumed.

    Returns the number of CCU2C cells removed.
    """
    refcount: dict[int, int] = {}
    for cell in netlist.cells.values():
        for pn, bits in cell.ports.items():
            if pn in _OUTPUT_PORTS:
                continue
            for b in bits:
                if isinstance(b, int) and b >= 2:
                    refcount[b] = refcount.get(b, 0) + 1
    for pinfo in netlist.ports.values():
        for b in pinfo.get("bits", []):
            if isinstance(b, int) and b >= 2:
                refcount[b] = refcount.get(b, 0) + 1

    # Producer index so releasing a CIN can requeue the feeder cell.
    producer: dict[int, str] = {}
    for name, cell in netlist.cells.items():
        if cell.cell_type != "CCU2C":
            continue
        for pn in ("COUT", "S0", "S1"):
            for b in cell.ports.get(pn, []):
                if isinstance(b, int) and b >= 2:
                    producer[b] = name

    def _dead(cell: ECP5Cell) -> bool:
        for pn in ("S0", "S1", "COUT"):
            for b in cell.ports.get(pn, []):
                if isinstance(b, int) and b >= 2 and refcount.get(b, 0) > 0:
                    return False
        return True

    queue = [name for name, cell in netlist.cells.items()
             if cell.cell_type == "CCU2C" and _dead(cell)]
    removed = 0
    while queue:
        name = queue.pop()
        cell = netlist.cells.get(name)
        if cell is None or not _dead(cell):
            continue
        for pn, bits in cell.ports.items():
            if pn in _OUTPUT_PORTS:
                continue
            for b in bits:
                if isinstance(b, int) and b >= 2:
                    refcount[b] = refcount.get(b, 0) - 1
                    if refcount[b] <= 0 and b in producer:
                        queue.append(producer[b])
        del netlist.cells[name]
        removed += 1

    return removed


def merge_lut_chains(netlist: ECP5Netlist) -> int:
    """Merge chained LUT4 pairs with self-checking verification.

    For each candidate merge:
    1. Identify feeder LUT whose Z feeds one input of child LUT
    2. Collect all unique variable inputs across both LUTs
    3. If total unique inputs <= 4, compute the composed truth table
    4. VERIFY: evaluate the original two-LUT chain for all 16 input
       combinations and compare against the composed single-LUT result
    5. Only commit the merge if all 16 evaluations match

    Returns the number of LUT4 cells eliminated.
    """
    # Build output-bit -> cell_name map
    bit_to_lut: dict[int, str] = {}
    for name, cell in netlist.cells.items():
        if cell.cell_type != "LUT4":
            continue
        z = cell.ports.get("Z", [])
        if z and isinstance(z[0], int) and z[0] >= 2:
            bit_to_lut[z[0]] = name

    # Build fanout count — ALL cell types, not just LUT4.
    # A feeder can only be deleted if its Z has exactly 1 consumer.
    # FFs, CCU2Cs, DPRs etc. also consume LUT outputs.
    bit_fanout: dict[int, int] = {}
    for cell in netlist.cells.values():
        for port_name, bits in cell.ports.items():
            if port_name in _OUTPUT_PORTS:
                continue  # skip output ports
            for b in bits:
                if isinstance(b, int) and b >= 2:
                    bit_fanout[b] = bit_fanout.get(b, 0) + 1
    # Also count port bits (module outputs reference signal bits)
    for port_info in netlist.ports.values():
        for b in port_info.get("bits", []):
            if isinstance(b, int) and b >= 2:
                bit_fanout[b] = bit_fanout.get(b, 0) + 1

    merged = 0
    absorbed: set[int] = set()  # feeder Z bits that were absorbed
    feeder_names: dict[int, str] = {}  # feeder Z bit -> feeder cell name

    for name, cell in list(netlist.cells.items()):
        if cell.cell_type != "LUT4":
            continue
        child_init = _get_init(cell)
        if child_init is None:
            continue

        # Collect child's pin values: int (signal) or str ("0"/"1")
        child_pin_vals: list[int | str] = []
        for pin in ("A", "B", "C", "D"):
            b = cell.ports.get(pin, ["0"])[0]
            child_pin_vals.append(b)

        # Find a single-fanout feeder
        feeder_pin_idx = -1
        feeder_bit = -1
        for pi, b in enumerate(child_pin_vals):
            if isinstance(b, int) and b >= 2 and b in bit_to_lut and b not in absorbed:
                src = bit_to_lut[b]
                if src != name and bit_fanout.get(b, 0) == 1:
                    feeder_pin_idx = pi
                    feeder_bit = b
                    break

        if feeder_pin_idx < 0:
            continue

        src_name = bit_to_lut[feeder_bit]
        src_cell = netlist.cells.get(src_name)
        if src_cell is None:
            continue
        feeder_init = _get_init(src_cell)
        if feeder_init is None:
            continue

        # Collect feeder's pin values
        feeder_pin_vals: list[int | str] = []
        for pin in ("A", "B", "C", "D"):
            b = src_cell.ports.get(pin, ["0"])[0]
            feeder_pin_vals.append(b)

        # Collect all unique variable signal bits
        all_signals: set[int] = set()
        for b in feeder_pin_vals:
            if isinstance(b, int) and b >= 2:
                all_signals.add(b)
        for pi, b in enumerate(child_pin_vals):
            if pi == feeder_pin_idx:
                continue  # skip the feeder connection
            if isinstance(b, int) and b >= 2:
                all_signals.add(b)

        if len(all_signals) > 4:
            continue  # doesn't fit in one LUT4

        # Assign each signal to a composed-LUT pin index (0..3)
        sig_list = sorted(all_signals)
        sig_to_idx = {s: i for i, s in enumerate(sig_list)}

        # --- Compute composed truth table ---
        composed_init = 0
        for i in range(16):
            # Map composed-LUT input bits to signal values
            sig_vals: dict[int, int] = {}
            for s, idx in sig_to_idx.items():
                sig_vals[s] = (i >> idx) & 1

            # Evaluate feeder LUT
            fa, fb, fc, fd = 0, 0, 0, 0
            for pi, b in enumerate(feeder_pin_vals):
                val = 0
                if b == "1":
                    val = 1
                elif isinstance(b, int) and b >= 2:
                    val = sig_vals.get(b, 0)
                if pi == 0:
                    fa = val
                elif pi == 1:
                    fb = val
                elif pi == 2:
                    fc = val
                elif pi == 3:
                    fd = val
            feeder_out = _eval_lut4(feeder_init, fa, fb, fc, fd)

            # Evaluate child LUT
            ca, cb, cc, cd = 0, 0, 0, 0
            for pi, b in enumerate(child_pin_vals):
                val = 0
                if pi == feeder_pin_idx:
                    val = feeder_out  # substitute feeder output
                elif b == "1":
                    val = 1
                elif isinstance(b, int) and b >= 2:
                    val = sig_vals.get(b, 0)
                if pi == 0:
                    ca = val
                elif pi == 1:
                    cb = val
                elif pi == 2:
                    cc = val
                elif pi == 3:
                    cd = val
            result = _eval_lut4(child_init, ca, cb, cc, cd)

            if result:
                composed_init |= (1 << i)

        # --- SELF-CHECK: verify against direct two-LUT evaluation ---
        ok = True
        for i in range(16):
            sig_vals = {}
            for s, idx in sig_to_idx.items():
                sig_vals[s] = (i >> idx) & 1

            # Evaluate feeder
            fa, fb, fc, fd = 0, 0, 0, 0
            for pi, b in enumerate(feeder_pin_vals):
                val = 0
                if b == "1":
                    val = 1
                elif isinstance(b, int) and b >= 2:
                    val = sig_vals.get(b, 0)
                if pi == 0:
                    fa = val
                elif pi == 1:
                    fb = val
                elif pi == 2:
                    fc = val
                elif pi == 3:
                    fd = val
            feeder_out = _eval_lut4(feeder_init, fa, fb, fc, fd)

            # Evaluate child with feeder output
            ca, cb, cc, cd = 0, 0, 0, 0
            for pi, b in enumerate(child_pin_vals):
                val = 0
                if pi == feeder_pin_idx:
                    val = feeder_out
                elif b == "1":
                    val = 1
                elif isinstance(b, int) and b >= 2:
                    val = sig_vals.get(b, 0)
                if pi == 0:
                    ca = val
                elif pi == 1:
                    cb = val
                elif pi == 2:
                    cc = val
                elif pi == 3:
                    cd = val
            chain_result = _eval_lut4(child_init, ca, cb, cc, cd)

            # Evaluate composed LUT
            composed_result = (composed_init >> i) & 1

            if chain_result != composed_result:
                ok = False
                break

        if not ok:
            # Should never happen — the composition loop above is
            # identical to the verification loop.  If it does, there
            # is a code bug.
            continue

        # --- Commit the merge ---
        cell.parameters["INIT"] = format(composed_init & 0xFFFF, "016b")

        # Maintain bit_fanout incrementally so it stays exact across the many
        # merges in one pass. Drop the child's old pin references, rewire to
        # the sorted signal list, then add the new references. (Scanning the
        # whole netlist per merge to recount, as before, is O(cells) per merge
        # and quadratic over a pass — 480 s on the RIME image alone.)
        for b in child_pin_vals:
            if isinstance(b, int) and b >= 2:
                bit_fanout[b] = bit_fanout.get(b, 0) - 1
        for pi, pin in enumerate(("A", "B", "C", "D")):
            if pi < len(sig_list):
                cell.ports[pin] = [sig_list[pi]]
            else:
                cell.ports[pin] = ["0"]
        for b in sig_list:
            bit_fanout[b] = bit_fanout.get(b, 0) + 1

        absorbed.add(feeder_bit)
        feeder_names[feeder_bit] = src_name
        merged += 1
        # Retire the feeder as a future candidate; its actual deletion is
        # decided by an authoritative reference sweep after the pass, so a
        # slightly stale fan-out estimate can never delete a live feeder.
        bit_to_lut.pop(feeder_bit, None)

    # Delete absorbed feeders that nothing references any more. One O(cells)
    # sweep for the whole pass instead of a rescan per merge.
    if absorbed:
        referenced: set[int] = set()
        for c in netlist.cells.values():
            for pn, bits in c.ports.items():
                if pn in _OUTPUT_PORTS:
                    continue
                referenced.update(b for b in bits if isinstance(b, int))
        for pinfo in netlist.ports.values():
            referenced.update(b for b in pinfo.get("bits", []) if isinstance(b, int))
        for z in absorbed:
            if z not in referenced:
                src_name = feeder_names.get(z)
                if src_name and src_name in netlist.cells:
                    del netlist.cells[src_name]

    return merged


def deduplicate_luts(netlist: ECP5Netlist) -> int:
    """Eliminate duplicate LUT4 cells with identical INIT and inputs.

    Two LUTs with the same truth table and same input signals produce
    identical outputs.  Replace all references to the duplicate's Z
    with the canonical Z, updating ALL cell types (not just LUT4).
    """
    # Build signature -> first cell
    sig_map: dict[tuple, str] = {}
    replacements: dict[int, int] = {}  # old_z -> canonical_z

    for name, cell in netlist.cells.items():
        if cell.cell_type != "LUT4":
            continue
        init = cell.parameters.get("INIT", "")
        inputs = tuple(cell.ports.get(p, ["0"])[0] for p in "ABCD")
        sig = (init, inputs)
        z = cell.ports.get("Z", [None])[0]
        if not isinstance(z, int) or z < 2:
            continue

        if sig in sig_map:
            canonical = netlist.cells.get(sig_map[sig])
            if canonical is None:
                sig_map[sig] = name
                continue
            canon_z = canonical.ports.get("Z", [None])[0]
            if isinstance(canon_z, int) and canon_z >= 2 and canon_z != z:
                replacements[z] = canon_z
        else:
            sig_map[sig] = name

    if not replacements:
        return 0

    # Apply replacements to ALL cell inputs (every cell type)
    for cell in netlist.cells.values():
        for port_name, bits in cell.ports.items():
            if port_name in _OUTPUT_PORTS:
                continue
            for i, b in enumerate(bits):
                if b in replacements:
                    bits[i] = replacements[b]

    # Apply to module port bits
    for pinfo in netlist.ports.values():
        bits = pinfo.get("bits", [])
        for i, b in enumerate(bits):
            if b in replacements:
                bits[i] = replacements[b]

    # Every reference to a duplicate's Z was rewritten to its canonical above
    # (inputs and port bits alike), so each replaced Z now has zero consumers.
    # Delete those LUTs directly — the previous per-duplicate netlist rescan
    # was O(cells) each and quadratic over the pass.
    to_delete = [
        name for name, cell in netlist.cells.items()
        if cell.cell_type == "LUT4"
        and isinstance(cell.ports.get("Z", [None])[0], int)
        and cell.ports.get("Z", [None])[0] in replacements
    ]
    for name in to_delete:
        del netlist.cells[name]

    return len(to_delete)


def break_comb_loops(netlist: ECP5Netlist) -> int:
    """Break combinational self-loops in LUT4 and CCU2C cells.

    Detects cells where an input bit equals an output bit (the output
    feeds back to the same cell's input). These are latches inferred
    from incomplete case/if statements or hold-value feedback. Breaks
    the loop by tying the self-referencing input to constant 0.

    For LUT4: adjusts the INIT truth table accordingly.
    For CCU2C: ties the self-referencing input to GND.

    Returns the number of loops broken.
    """
    broken = 0

    for cell in netlist.cells.values():
        if cell.cell_type == "LUT4":
            z_bits = cell.ports.get("Z", [])
            if not z_bits or not isinstance(z_bits[0], int):
                continue
            z_bit = z_bits[0]
            for pin in ("A", "B", "C", "D"):
                pin_bits = cell.ports.get(pin, [])
                if pin_bits and isinstance(pin_bits[0], int) and pin_bits[0] == z_bit:
                    pin_idx = {"A": 0, "B": 1, "C": 2, "D": 3}[pin]
                    init = _get_init(cell)
                    if init is None:
                        continue
                    new_init = 0
                    for i in range(16):
                        if (i >> pin_idx) & 1:
                            continue
                        if (init >> i) & 1:
                            new_init |= (1 << i)
                    _set_init(cell, new_init)
                    cell.ports[pin] = [0]
                    broken += 1
                    break

        elif cell.cell_type == "CCU2C":
            # CCU2C outputs: S0, S1, COUT.  Inputs: A0,B0,C0,D0, A1,B1,C1,D1, CIN
            out_bits: set[int] = set()
            for port in ("S0", "S1", "COUT"):
                for b in cell.ports.get(port, []):
                    if isinstance(b, int):
                        out_bits.add(b)
            for pin in ("A0", "B0", "C0", "D0", "A1", "B1", "C1", "D1", "CIN"):
                pin_bits = cell.ports.get(pin, [])
                if pin_bits and isinstance(pin_bits[0], int) and pin_bits[0] in out_bits:
                    cell.ports[pin] = [0]
                    broken += 1

    return broken


def tie_dont_care_inputs(netlist: ECP5Netlist) -> int:
    """Ground undriven LUT4 inputs that provably do not affect the output.

    Tech mapping can leave a LUT4 input referencing a bit no cell drives
    (comparison padding and similar). When the INIT truth table is
    invariant under that pin, tying the pin to constant 0 is exact — the
    LUT computes the same function for every input. Pins the truth table
    actually depends on are left untouched; those are real wiring bugs
    that must stay visible.

    Returns the number of pins tied.
    """
    driven: set[int] = set()
    for port_info in netlist.ports.values():
        if port_info.get("direction") == "input":
            for b in port_info.get("bits", []):
                if isinstance(b, int):
                    driven.add(b)
    for cell in netlist.cells.values():
        for port_name, bits in cell.ports.items():
            if (port_name in _OUTPUT_PORTS or port_name.startswith("DO")
                    or port_name.startswith("P") or port_name.startswith("R")):
                for b in bits:
                    if isinstance(b, int):
                        driven.add(b)

    tied = 0
    for cell in netlist.cells.values():
        if cell.cell_type != "LUT4":
            continue
        init = _get_init(cell)
        if init is None:
            continue
        for pin_idx, pin in enumerate(("A", "B", "C", "D")):
            bits = cell.ports.get(pin, ["0"])
            b = bits[0] if bits else "0"
            if not (isinstance(b, int) and b >= 2 and b not in driven):
                continue
            # Invariance check: flipping this pin never changes the output
            independent = all(
                ((init >> i) & 1) == ((init >> (i ^ (1 << pin_idx))) & 1)
                for i in range(16)
            )
            if independent:
                cell.ports[pin] = ["0"]
                tied += 1

    return tied


def pack_slices(netlist: ECP5Netlist) -> dict[str, int]:
    """Run all LUT optimization passes to a fixed point. Returns counts."""
    total_simplify = simplify_constant_luts(netlist)
    total_dead = _eliminate_dead_luts(netlist)
    total_loops = break_comb_loops(netlist)
    total_merge = 0
    total_dedup = 0

    total_carry = 0

    total_buf = 0
    total_resynth = 0

    for _ in range(10):
        mc = merge_lut_chains(netlist)
        sc = simplify_constant_luts(netlist)
        ab = absorb_buffer_luts(netlist)
        rs = resynth_cones(netlist)
        dd = deduplicate_luts(netlist)
        dl = _eliminate_dead_luts(netlist)
        tc = trim_dead_carry_tails(netlist)
        total_merge += mc
        total_simplify += sc
        total_buf += ab
        total_resynth += rs
        total_dedup += dd
        total_dead += dl
        total_carry += tc
        if mc + sc + ab + rs + dd + dl + tc == 0:
            break

    total_tied = tie_dont_care_inputs(netlist)

    return {
        "const_lut_simplify": total_simplify,
        "lut_dedup": total_dedup,
        "dead_lut": total_dead,
        "chain_merge": total_merge,
        "buffers_absorbed": total_buf,
        "cones_resynth": total_resynth,
        "dead_carry": total_carry,
        "loops_broken": total_loops,
        "dont_care_tied": total_tied,
    }
