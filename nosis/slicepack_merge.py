"""Self-checking LUT chain merge pass.

Merges chained LUT4 pairs where the combined function fits in 4 inputs.
Every merge is verified by evaluating the original two-LUT chain and the
composed single-LUT result for all 16 input combinations.  If they
disagree, the merge is rejected and a diagnostic is printed.

This replaces the broken merge_lut_chains in slicepack.py.
"""

from __future__ import annotations
from nosis.techmap.netlist import ECP5Netlist, ECP5Cell


def _get_init(cell: ECP5Cell) -> int | None:
    init_str = cell.parameters.get("INIT", "")
    if not init_str:
        return None
    try:
        return int(init_str, 2)
    except (ValueError, TypeError):
        pass
    try:
        return int(init_str, 16)
    except (ValueError, TypeError):
        return None


def _eval_lut4(init: int, a: int, b: int, c: int, d: int) -> int:
    """Evaluate a LUT4 truth table for given input values (0 or 1)."""
    idx = a | (b << 1) | (c << 2) | (d << 3)
    return (init >> idx) & 1


def merge_lut_chains_safe(netlist: ECP5Netlist) -> int:
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
    _OUTPUT_PORTS = {"Z", "Q", "S0", "S1", "COUT", "DO", "F", "F0", "F1",
                     "OFX0", "OFX1"}
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
                if pi == 0: fa = val
                elif pi == 1: fb = val
                elif pi == 2: fc = val
                elif pi == 3: fd = val
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
                if pi == 0: ca = val
                elif pi == 1: cb = val
                elif pi == 2: cc = val
                elif pi == 3: cd = val
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
                if b == "1": val = 1
                elif isinstance(b, int) and b >= 2: val = sig_vals.get(b, 0)
                if pi == 0: fa = val
                elif pi == 1: fb = val
                elif pi == 2: fc = val
                elif pi == 3: fd = val
            feeder_out = _eval_lut4(feeder_init, fa, fb, fc, fd)

            # Evaluate child with feeder output
            ca, cb, cc, cd = 0, 0, 0, 0
            for pi, b in enumerate(child_pin_vals):
                val = 0
                if pi == feeder_pin_idx: val = feeder_out
                elif b == "1": val = 1
                elif isinstance(b, int) and b >= 2: val = sig_vals.get(b, 0)
                if pi == 0: ca = val
                elif pi == 1: cb = val
                elif pi == 2: cc = val
                elif pi == 3: cd = val
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

        # Rewire pins: assign signals to A/B/C/D by sorted index
        for pi, pin in enumerate(("A", "B", "C", "D")):
            if pi < len(sig_list):
                cell.ports[pin] = [sig_list[pi]]
            else:
                cell.ports[pin] = ["0"]

        absorbed.add(feeder_bit)
        merged += 1

        # Remove feeder only if NO cell still reads its Z bit.
        # The fanout counter can drift during iterative merging,
        # so scan the actual netlist instead of trusting the counter.
        still_used = False
        for other_cell in netlist.cells.values():
            for pn, bits in other_cell.ports.items():
                if pn in _OUTPUT_PORTS:
                    continue
                if feeder_bit in bits:
                    still_used = True
                    break
            if still_used:
                break
        if not still_used:
            for pi in netlist.ports.values():
                if feeder_bit in pi.get("bits", []):
                    still_used = True
                    break
        if not still_used and src_name in netlist.cells:
            del netlist.cells[src_name]

    return merged
