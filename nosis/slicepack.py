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
            for other in netlist.cells.values():
                if other.name == dup.name:
                    continue
                for port_name, bits in list(other.ports.items()):
                    other.ports[port_name] = [
                        new_bit if b == old_bit else b for b in bits
                    ]
            for net in netlist.nets.values():
                net.bits = [new_bit if b == old_bit else b for b in net.bits]
            for port_name, port_info in netlist.ports.items():
                port_info["bits"] = [
                    new_bit if b == old_bit else b for b in port_info["bits"]
                ]
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
    """Break combinational self-loops in LUT4 cells.

    Detects LUT4 cells where an input bit equals the output bit (the
    output feeds back to the same cell's input). These are latches
    inferred from incomplete case/if statements. Breaks the loop by
    tying the self-referencing input to constant 0 and adjusting the
    INIT truth table accordingly.

    Returns the number of loops broken.
    """
    broken = 0
    for cell in netlist.cells.values():
        if cell.cell_type != "LUT4":
            continue
        z_bits = cell.ports.get("Z", [])
        if not z_bits or not isinstance(z_bits[0], int):
            continue
        z_bit = z_bits[0]
        for pin in ("A", "B", "C", "D"):
            pin_bits = cell.ports.get(pin, [])
            if pin_bits and isinstance(pin_bits[0], int) and pin_bits[0] == z_bit:
                # Self-loop: input pin reads from the same bit as output Z.
                # Break it by tying the input to 0 and reducing the truth table.
                pin_idx = {"A": 0, "B": 1, "C": 2, "D": 3}[pin]
                init = _get_init(cell)
                if init is None:
                    continue
                # Reduce truth table: keep only rows where this pin = 0.
                new_init = 0
                for i in range(16):
                    if (i >> pin_idx) & 1:
                        continue  # skip rows where the self-input is 1
                    if (init >> i) & 1:
                        new_init |= (1 << i)
                _set_init(cell, new_init)
                cell.ports[pin] = [0]  # tie to GND
                broken += 1
                break  # only one self-loop per cell
    return broken


def pack_slices(netlist: ECP5Netlist) -> dict[str, int]:
    """Run all LUT optimization passes. Returns counts."""
    s1 = simplify_constant_luts(netlist)
    dd = deduplicate_luts(netlist)
    ab = absorb_buffers(netlist)
    dl = _eliminate_dead_luts(netlist)
    mc = 0
    for _ in range(5):
        m = merge_lut_chains(netlist)
        if m == 0:
            break
        mc += m
        simplify_constant_luts(netlist)
    s2 = simplify_constant_luts(netlist)
    dl2 = _eliminate_dead_luts(netlist)
    s3 = simplify_constant_luts(netlist)
    bl = break_comb_loops(netlist)
    return {
        "const_lut_simplify": s1 + s2 + s3,
        "lut_dedup": dd,
        "buffer_absorb": ab,
        "dead_lut": dl + dl2,
        "chain_merge": mc,
        "loops_broken": bl,
    }
