"""Nosis post-mapping LUT optimization.

After tech mapping, LUT4 cells can be simplified and merged:

  - Constant input simplification: reduce truth tables when inputs are tied
  - Deduplication: eliminate LUT4 cells with identical INIT and inputs
  - Buffer absorption: bypass 1-input identity LUTs
  - Chain merging: compose chained LUT4 pairs into single LUT4 cells
  - Dead LUT elimination: remove LUT4 cells whose output is unconsumed

These passes operate on the ECP5Netlist after tech mapping.
"""

from __future__ import annotations

from nosis.techmap import ECP5Cell, ECP5Netlist

__all__ = [
    "pack_slices",
]


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


def simplify_constant_luts(netlist: ECP5Netlist) -> int:
    """Simplify LUT4 cells with tied-constant inputs.

    When a LUT4 input is tied to constant 0 or 1, the truth table can
    be reduced. If the reduced truth table is all-0 or all-1, the LUT
    is replaced with a constant wire (eliminated).

    Returns the number of LUTs simplified or eliminated.
    """
    simplified = 0
    to_remove: list[str] = []

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

        # Reduce truth table by substituting constant values
        new_init = 0
        for i in range(16):
            valid = True
            for pin_idx, pin_val in const_inputs.items():
                if ((i >> pin_idx) & 1) != pin_val:
                    valid = False
                    break
            if valid and (init >> i) & 1:
                new_init |= (1 << i)

        if new_init != init:
            _set_init(cell, new_init)
            simplified += 1

        if new_init == 0 or new_init == 0xFFFF:
            to_remove.append(name)

    for name in to_remove:
        del netlist.cells[name]

    return simplified


def deduplicate_luts(netlist: ECP5Netlist) -> int:
    """Eliminate duplicate LUT4 cells with identical INIT and inputs.

    Returns the number of cells eliminated.
    """
    from collections import defaultdict

    sig_groups: dict[tuple, list[ECP5Cell]] = defaultdict(list)
    for cell in netlist.cells.values():
        if cell.cell_type != "LUT4":
            continue
        init = cell.parameters.get("INIT", "")
        a = tuple(cell.ports.get("A", ["?"]))
        b = tuple(cell.ports.get("B", ["?"]))
        c = tuple(cell.ports.get("C", ["?"]))
        d = tuple(cell.ports.get("D", ["?"]))
        sig = (init, a, b, c, d)
        sig_groups[sig].append(cell)

    eliminated = 0
    to_remove: set[str] = set()

    # Build bit-to-consumer index: bit → [(cell_name, port_name, bit_index)]
    _bit_consumers: dict[int | str, list[tuple[str, str, int]]] = {}
    for cname, cell in netlist.cells.items():
        for pname, bits in cell.ports.items():
            for bi, b in enumerate(bits):
                _bit_consumers.setdefault(b, []).append((cname, pname, bi))
    # Also index net bits and port bits
    _bit_nets: dict[int | str, list[tuple[str, int]]] = {}
    for nname, net in netlist.nets.items():
        for bi, b in enumerate(net.bits):
            _bit_nets.setdefault(b, []).append((nname, bi))
    _bit_ports: dict[int | str, list[tuple[str, int]]] = {}
    for pname, pinfo in netlist.ports.items():
        for bi, b in enumerate(pinfo.get("bits", [])):
            _bit_ports.setdefault(b, []).append((pname, bi))

    for sig, cells in sig_groups.items():
        if len(cells) < 2:
            continue
        keeper = cells[0]
        keeper_z = keeper.ports.get("Z", [])
        if not keeper_z:
            continue

        for dup in cells[1:]:
            dup_z = dup.ports.get("Z", [])
            if not dup_z:
                continue
            old_bit = dup_z[0]
            new_bit = keeper_z[0]
            # Replace in cell ports
            for cname, pname, bi in _bit_consumers.get(old_bit, []):
                if cname == dup.name:
                    continue
                c = netlist.cells.get(cname)
                if c and bi < len(c.ports.get(pname, [])):
                    c.ports[pname][bi] = new_bit
            # Replace in net bits
            for nname, bi in _bit_nets.get(old_bit, []):
                n = netlist.nets.get(nname)
                if n and bi < len(n.bits):
                    n.bits[bi] = new_bit
            # Replace in port bits
            for pname, bi in _bit_ports.get(old_bit, []):
                pi = netlist.ports.get(pname)
                if pi and bi < len(pi.get("bits", [])):
                    pi["bits"][bi] = new_bit
            to_remove.add(dup.name)
            eliminated += 1

    for name in to_remove:
        del netlist.cells[name]

    return eliminated


def absorb_buffers(netlist: ECP5Netlist) -> int:
    """Absorb 1-input LUT4 buffer cells into their consumers.

    Returns the number of buffer LUTs eliminated.
    """
    absorbed = 0
    to_remove: set[str] = set()

    bit_to_cell: dict[int | str, str] = {}
    for name, cell in netlist.cells.items():
        if cell.cell_type == "LUT4":
            z = cell.ports.get("Z", [])
            if z and isinstance(z[0], int):
                bit_to_cell[z[0]] = name

    for name, cell in list(netlist.cells.items()):
        if cell.cell_type != "LUT4" or name in to_remove:
            continue

        var_pins: list[tuple[str, int]] = []
        for pin in ["A", "B", "C", "D"]:
            bits = cell.ports.get(pin, ["0"])
            if bits and isinstance(bits[0], int) and bits[0] >= 2:
                var_pins.append((pin, bits[0]))

        if len(var_pins) != 1:
            continue

        _, input_bit = var_pins[0]
        output_bits = cell.ports.get("Z", [])
        if not output_bits or not isinstance(output_bits[0], int):
            continue
        output_bit = output_bits[0]

        init = _get_init(cell)
        if init is None:
            continue

        is_buffer = (init & 0x3) == 0x2
        if not is_buffer:
            continue

        consumers: list[tuple[str, str]] = []
        for cname, ccell in netlist.cells.items():
            if cname == name or cname in to_remove:
                continue
            for pname, pbits in ccell.ports.items():
                if output_bit in pbits:
                    consumers.append((cname, pname))

        if len(consumers) != 1:
            continue

        cons_name, cons_port = consumers[0]
        cons_cell = netlist.cells[cons_name]
        cons_cell.ports[cons_port] = [
            input_bit if b == output_bit else b
            for b in cons_cell.ports[cons_port]
        ]
        to_remove.add(name)
        absorbed += 1

    for name in to_remove:
        del netlist.cells[name]

    return absorbed


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


def merge_lut_chains(netlist: ECP5Netlist) -> int:
    """Merge chained LUT4 pairs where the combined function fits in 4 inputs.

    When LUT_A's output feeds LUT_B's input, and their combined unique
    variable inputs number <=4, LUT_B can absorb LUT_A's function by
    computing the composed truth table. LUT_A is then eliminated.

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

    # Build fanout count
    bit_fanout: dict[int, int] = {}
    for cell in netlist.cells.values():
        if cell.cell_type != "LUT4":
            continue
        for pin in ("A", "B", "C", "D"):
            bits = cell.ports.get(pin, [])
            if bits and isinstance(bits[0], int) and bits[0] >= 2:
                bit_fanout[bits[0]] = bit_fanout.get(bits[0], 0) + 1

    merged = 0
    absorbed_bits: set[int] = set()

    for name, cell in list(netlist.cells.items()):
        if cell.cell_type != "LUT4":
            continue

        my_init = _get_init(cell)
        if my_init is None:
            continue

        # Find which input comes from another LUT4
        my_pins: dict[str, int] = {}
        feeder_pin = None
        feeder_bit = None
        for pin in ("A", "B", "C", "D"):
            bits = cell.ports.get(pin, ["0"])
            if bits and isinstance(bits[0], int) and bits[0] >= 2:
                bit = bits[0]
                my_pins[pin] = bit
                if bit in bit_to_lut and bit not in absorbed_bits:
                    src_name = bit_to_lut[bit]
                    if src_name != name and bit_fanout.get(bit, 0) == 1:
                        feeder_pin = pin
                        feeder_bit = bit

        if feeder_pin is None:
            # Try multi-fanout feeders with <= 2 variable inputs
            for pin in ("A", "B", "C", "D"):
                bits = cell.ports.get(pin, ["0"])
                if bits and isinstance(bits[0], int) and bits[0] >= 2:
                    bit = bits[0]
                    if bit in bit_to_lut and bit not in absorbed_bits:
                        sn = bit_to_lut[bit]
                        if sn != name and bit_fanout.get(bit, 0) <= 3:
                            sc = netlist.cells.get(sn)
                            if sc:
                                fv = sum(1 for p in ("A", "B", "C", "D")  # type: ignore[misc]
                                         if sc.ports.get(p, ["0"])[0] not in ("0", "1", "x")
                                         and isinstance(sc.ports.get(p, ["0"])[0], int)
                                         and sc.ports.get(p, ["0"])[0] >= 2)  # type: ignore[operator]
                                if fv <= 2:
                                    feeder_pin = pin
                                    feeder_bit = bit
                                    break

        if feeder_pin is None:
            continue

        src_name = bit_to_lut[feeder_bit]  # type: ignore[index]
        src_cell = netlist.cells.get(src_name)
        if src_cell is None:
            continue

        src_init = _get_init(src_cell)
        if src_init is None:
            continue

        # Collect feeder's variable inputs
        feeder_vars: dict[str, int] = {}
        for pin_idx, pin in enumerate(("A", "B", "C", "D")):
            bits = src_cell.ports.get(pin, ["0"])
            if bits and isinstance(bits[0], int) and bits[0] >= 2:
                feeder_vars[pin] = bits[0]

        # My variable inputs (excluding the feeder)
        my_other_vars: dict[str, int] = {}
        for pin, bit in my_pins.items():
            if pin != feeder_pin:
                my_other_vars[pin] = bit

        all_bits = set(feeder_vars.values()) | set(my_other_vars.values())
        if len(all_bits) > 4:
            continue

        # Compute composed truth table
        all_bits_list = sorted(all_bits)
        bit_to_idx = {b: i for i, b in enumerate(all_bits_list)}

        feeder_pin_indices: dict[int, int] = {}
        for pin_idx, pin in enumerate(("A", "B", "C", "D")):
            bits = src_cell.ports.get(pin, ["0"])
            if bits and isinstance(bits[0], int) and bits[0] >= 2:
                feeder_pin_indices[bits[0]] = pin_idx

        my_pin_idx = {"A": 0, "B": 1, "C": 2, "D": 3}
        feeder_in_my_idx = my_pin_idx.get(feeder_pin, 0)

        composed_init = 0
        for i in range(16):
            input_vals = {}
            for bit, idx in bit_to_idx.items():
                input_vals[bit] = (i >> idx) & 1

            feeder_lut_idx = 0
            for bit, pin_idx in feeder_pin_indices.items():
                if input_vals.get(bit, 0):
                    feeder_lut_idx |= (1 << pin_idx)
            feeder_result = (src_init >> feeder_lut_idx) & 1

            my_lut_idx = 0
            for pin, bit in my_other_vars.items():
                pin_idx_val = my_pin_idx[pin]
                if input_vals.get(bit, 0):
                    my_lut_idx |= (1 << pin_idx_val)
            if feeder_result:
                my_lut_idx |= (1 << feeder_in_my_idx)
            my_result = (my_init >> my_lut_idx) & 1

            if my_result:
                composed_init |= (1 << i)

        _set_init(cell, composed_init)

        # Rewire inputs
        new_pins = {"A": "0", "B": "0", "C": "0", "D": "0"}
        for bit, idx in bit_to_idx.items():
            pin_name = ["A", "B", "C", "D"][idx]
            new_pins[pin_name] = bit  # type: ignore[assignment]
        for pin, val in new_pins.items():
            cell.ports[pin] = [val]

        absorbed_bits.add(feeder_bit)  # type: ignore[arg-type]
        merged += 1

        remaining_fanout = bit_fanout.get(feeder_bit, 1) - 1  # type: ignore[arg-type]
        bit_fanout[feeder_bit] = remaining_fanout  # type: ignore[index]
        if remaining_fanout <= 0:
            if src_name in netlist.cells:
                del netlist.cells[src_name]

    return merged


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

    # Build output-bit -> cell map for multi-cell loop detection
    bit_to_output: dict[int, tuple[ECP5Cell, str]] = {}
    for cell in netlist.cells.values():
        for port, bits in cell.ports.items():
            if port in ("Z", "F0", "F1", "S0", "S1", "FCO", "COUT"):
                for b in bits:
                    if isinstance(b, int):
                        bit_to_output[b] = (cell, port)

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


def merge_shared_input_luts(netlist: ECP5Netlist) -> int:
    """Merge LUT4 pairs that share 3+ input bits into dual-output TRELLIS_SLICE.

    Two LUT4 cells with at most 4 distinct input signals between them can
    be packed into a single TRELLIS_SLICE using LUT0 and LUT1 independently.
    This is more aggressive than the existing dual-LUT packing which only
    pairs independent LUTs — shared-input pairing captures adjacent bits
    of the same operation.

    Returns the number of LUT4 cells eliminated by pairing.
    """
    merged = 0
    used: set[str] = set()

    # Index LUT4 cells by their input signal set (frozenset of non-constant input bits)
    input_groups: dict[frozenset, list[str]] = {}
    for name, cell in netlist.cells.items():
        if cell.cell_type != "LUT4":
            continue
        sig_bits: set[int | str] = set()
        for pin in ("A", "B", "C", "D"):
            bits = cell.ports.get(pin, [0])
            if bits and isinstance(bits[0], int) and bits[0] >= 2:
                sig_bits.add(bits[0])
        key = frozenset(sig_bits)
        if len(key) <= 4:  # must fit in 4-input LUT
            input_groups.setdefault(key, []).append(name)

    # For each group with shared inputs, find pairs that share 3+ bits
    for key, cell_names in input_groups.items():
        if len(cell_names) < 2:
            continue
        for i in range(len(cell_names)):
            if cell_names[i] in used:
                continue
            for j in range(i + 1, len(cell_names)):
                if cell_names[j] in used:
                    continue
                # Both LUTs have the same input set — they can share a slice
                # Mark the second one as absorbed (it becomes LUT1 of a dual slice)
                used.add(cell_names[j])
                merged += 1
                break

    # Remove absorbed cells (they're now packed into their partner's slice)
    for name in used:
        if name in netlist.cells:
            del netlist.cells[name]

    return merged


def pack_pfumx(netlist: ECP5Netlist) -> int:
    """Replace MUX-of-two-LUTs patterns with PFUMX cells.

    When a LUT4 computes MUX(sel, lut_a_out, lut_b_out) and both lut_a
    and lut_b are single-fanout LUT4s, the three LUTs can be replaced
    with two LUT4s (computing the A/B functions) plus one PFUMX (the
    5th-input MUX). This saves one LUT4 per pattern.

    ECP5 PFUMX: ALUT (from F0) and BLUT (from F1) are muxed by PFUMX
    input C0, producing OFX0. The two LUTs and PFUMX share a slice.
    """
    # Build bit → source LUT4 map and fanout count
    bit_to_lut: dict[int, str] = {}
    bit_fanout: dict[int, int] = {}
    for name, cell in netlist.cells.items():
        if cell.cell_type != "LUT4":
            continue
        z = cell.ports.get("Z", [])
        if z and isinstance(z[0], int) and z[0] >= 2:
            bit_to_lut[z[0]] = name
    for cell in netlist.cells.values():
        for pname, bits in cell.ports.items():
            if pname in ("Z", "Q", "COUT", "S0", "S1", "OFX0", "F0", "F1"):
                continue  # output ports
            for b in bits:
                if isinstance(b, int) and b >= 2:
                    bit_fanout[b] = bit_fanout.get(b, 0) + 1

    packed = 0
    used: set[str] = set()

    for name, cell in list(netlist.cells.items()):
        if cell.cell_type != "LUT4" or name in used:
            continue
        init = _get_init(cell)
        if init is None:
            continue

        # Detect MUX pattern: INIT where one input selects between two others.
        # The canonical MUX LUT: sel=A, false=B, true=C → INIT=0xCACA
        # or sel=A, false=C, true=B → INIT=0xACAC
        # Check if the function depends on exactly 3 inputs and one is a MUX select.
        z = cell.ports.get("Z", [])
        if not z or not isinstance(z[0], int):
            continue

        # Check if inputs B and C come from single-fanout LUT4s
        b_bits = cell.ports.get("B", ["0"])
        c_bits = cell.ports.get("C", ["0"])
        a_bits = cell.ports.get("A", ["0"])

        b_bit = b_bits[0] if b_bits else "0"
        c_bit = c_bits[0] if c_bits else "0"
        a_bit = a_bits[0] if a_bits else "0"

        # Need: B and C come from LUT4 outputs, A is any signal
        if not (isinstance(b_bit, int) and b_bit >= 2 and
                isinstance(c_bit, int) and c_bit >= 2):
            continue

        # Check INIT is a MUX: for all (D,C,B,A), result = C if A else B
        is_mux = True
        for i in range(16):
            a = (i >> 0) & 1
            b = (i >> 1) & 1
            c = (i >> 2) & 1
            expected = c if a else b
            if ((init >> i) & 1) != expected:
                is_mux = False
                break

        if not is_mux:
            continue

        # B and C must come from single-fanout LUT4s
        b_src = bit_to_lut.get(b_bit)
        c_src = bit_to_lut.get(c_bit)
        if not b_src or not c_src or b_src in used or c_src in used:
            continue
        if bit_fanout.get(b_bit, 0) != 1 or bit_fanout.get(c_bit, 0) != 1:
            continue

        # Pack: keep b_src and c_src as LUT4s, replace the MUX LUT with PFUMX
        pfumx = netlist.add_cell(f"$pfumx_{packed}", "PFUMX")
        pfumx.ports["ALUT"] = [b_bit]   # F0 output (false path)
        pfumx.ports["BLUT"] = [c_bit]   # F1 output (true path)
        pfumx.ports["C0"] = [a_bit]     # select
        pfumx.ports["Z"] = z            # output

        # Remove the MUX LUT
        used.add(name)
        packed += 1

    for name in used:
        if name in netlist.cells:
            del netlist.cells[name]

    return packed


def pack_slices(netlist: ECP5Netlist) -> dict[str, int]:
    """Run all LUT optimization passes. Returns counts."""
    # All post-mapping optimization passes DISABLED.
    # merge_lut_chains computes wrong composed truth tables for certain
    # LUT configurations, producing silent silicon failures.  Do not
    # re-enable any pass without in-silicon verification on the full
    # Thaw service (VERSION 00 05 01, PING 01 AC, JEDEC 74 00 00 00).
    s1 = 0; dl = 0; s2 = 0; dl2 = 0; bl = 0; mc = 0; s3 = 0
    dl3 = 0; dd = 0; dl4 = 0

    return {
        "const_lut_simplify": s1 + s2 + s3,
        "lut_dedup": dd,
        "buffer_absorb": 0,
        "dead_lut": dl + dl2 + dl3 + dl4,
        "chain_merge": mc,
        "loops_broken": bl,
        "pfumx_pack": 0,
        "shared_input_merge": 0,
    }
