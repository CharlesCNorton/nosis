"""Nosis slice packing — PFUMX (LUT5) and L6MUX21 (LUT6) optimization.

After tech mapping, TRELLIS_SLICE cells can be combined:

  PFUMX (LUT5): Two LUT4 cells that share 3 inputs and differ on the
  4th can be combined into a single slice using the passthrough mux.
  The 5th input selects between the two LUT4 outputs.

  L6MUX21 (LUT6): Two PFUMX (LUT5) outputs can be combined through
  a 2:1 mux to create a 6-input function.

These passes operate on the ECP5Netlist after tech mapping.
"""

from __future__ import annotations

from nosis.techmap import ECP5Cell, ECP5Netlist

__all__ = [
    "pack_pfumx",
    "pack_l6mux21",
    "pack_slices",
]


def _get_input_bits(cell: ECP5Cell) -> tuple[list, list, list, list]:
    """Extract A0, B0, C0, D0 input bit lists from a TRELLIS_SLICE."""
    return (
        cell.ports.get("A0", ["0"]),
        cell.ports.get("B0", ["0"]),
        cell.ports.get("C0", ["0"]),
        cell.ports.get("D0", ["0"]),
    )


def _get_output_bit(cell: ECP5Cell) -> int | str:
    """Get the F0 output bit from a TRELLIS_SLICE."""
    f0 = cell.ports.get("F0", [])
    return f0[0] if f0 else "0"


def pack_pfumx(netlist: ECP5Netlist) -> int:
    """Combine pairs of LUT4 cells into PFUMX (LUT5) where possible.

    Two TRELLIS_SLICE cells can share a slice if they have 3 common
    inputs. The 4th input becomes the PFUMX select, and the two LUT4
    outputs are muxed into a single 5-input function.

    Returns the number of PFUMX cells created.
    """
    # Build a map from (3-input signature) to list of candidate cells
    slices = [
        (name, cell) for name, cell in netlist.cells.items()
        if cell.cell_type == "TRELLIS_SLICE"
    ]

    packed = 0
    used: set[str] = set()

    # Match LUT4 pairs that share any 3 of 4 inputs (relaxed from exact A0/B0)
    for i, (name_a, cell_a) in enumerate(slices):
        if name_a in used:
            continue
        a0_a, b0_a, c0_a, d0_a = _get_input_bits(cell_a)
        inputs_a_set = {
            a0_a[0] if a0_a else "0",
            b0_a[0] if b0_a else "0",
            c0_a[0] if c0_a else "0",
            d0_a[0] if d0_a else "0",
        }
        # Remove constant "0" from the active input set
        active_a = inputs_a_set - {"0"}
        if len(active_a) < 1:
            continue  # all-constant LUT, nothing to share

        for j, (name_b, cell_b) in enumerate(slices):
            if j <= i or name_b in used:
                continue
            a0_b, b0_b, c0_b, d0_b = _get_input_bits(cell_b)
            inputs_b_set = {
                a0_b[0] if a0_b else "0",
                b0_b[0] if b0_b else "0",
                c0_b[0] if c0_b else "0",
                d0_b[0] if d0_b else "0",
            }
            active_b = inputs_b_set - {"0"}
            if len(active_b) < 1:
                continue

            # Count shared active inputs
            shared = active_a & active_b
            total_unique = active_a | active_b

            # PFUMX can accommodate 5 inputs total (4 shared + 1 select).
            # Two LUT4s can share a slice if their combined unique inputs ≤ 5
            # (4 for the shared LUT inputs + 1 for the PFUMX select).
            # The minimum sharing for this: at least 3 shared inputs,
            # or all unique inputs fit in 5.
            if len(total_unique) <= 5 and len(shared) >= max(len(active_a) - 1, 1):
                out_a = _get_output_bit(cell_a)
                out_b = _get_output_bit(cell_b)
                pfumx_out = netlist.alloc_bit()

                pfumx = netlist.add_cell(f"$pfumx_{packed}", "PFUMX")
                pfumx.ports["ALUT"] = [out_a]
                pfumx.ports["BLUT"] = [out_b]
                pfumx.ports["C0"] = [netlist.alloc_bit()]  # select input
                pfumx.ports["Z"] = [pfumx_out]

                used.add(name_a)
                used.add(name_b)
                packed += 1
                break

            if packed >= 2000:  # safety limit
                break

    return packed


def pack_l6mux21(netlist: ECP5Netlist) -> int:
    """Combine pairs of PFUMX outputs into L6MUX21 (LUT6) where possible.

    Returns the number of L6MUX21 cells created.
    """
    pfumx_cells = [
        (name, cell) for name, cell in netlist.cells.items()
        if cell.cell_type == "PFUMX"
    ]

    packed = 0
    used: set[str] = set()

    for i, (name_a, cell_a) in enumerate(pfumx_cells):
        if name_a in used:
            continue
        for j, (name_b, cell_b) in enumerate(pfumx_cells):
            if j <= i or name_b in used:
                continue

            out_a = cell_a.ports.get("Z", [])
            out_b = cell_b.ports.get("Z", [])
            if not out_a or not out_b:
                continue

            l6_out = netlist.alloc_bit()
            l6 = netlist.add_cell(f"$l6mux_{packed}", "L6MUX21")
            l6.ports["D0"] = [out_a[0]]
            l6.ports["D1"] = [out_b[0]]
            l6.ports["SD"] = [netlist.alloc_bit()]  # select
            l6.ports["Z"] = [l6_out]

            used.add(name_a)
            used.add(name_b)
            packed += 1
            break

    return packed


def pack_dual_lut4(netlist: ECP5Netlist) -> int:
    """Pack two independent LUT4 cells into a single TRELLIS_SLICE dual-LUT.

    Each TRELLIS_SLICE has two LUT4 slots (LUT0 and LUT1). When two
    independent LUT4 cells don't need to share inputs, they can be
    co-located in the same slice to reduce total slice count by up to 50%.

    Packs independent LUT4 cells into dual-LUT slices.

    Returns the number of cells eliminated by dual-packing.
    """
    slices = [
        (name, cell) for name, cell in netlist.cells.items()
        if cell.cell_type == "TRELLIS_SLICE"
    ]
    if len(slices) < 2:
        return 0

    packed = 0
    used: set[str] = set()

    for i, (name_a, cell_a) in enumerate(slices):
        if name_a in used:
            continue
        for j, (name_b, cell_b) in enumerate(slices):
            if j <= i or name_b in used:
                continue

            # Both cells must be simple LUT4 (MODE=LOGIC, no carry, no FF)
            # and cell_a must not already have LUT1 populated
            if cell_a.parameters.get("MODE") != "LOGIC":
                break
            if "LUT1_INITVAL" in cell_a.parameters:
                break  # already dual-packed
            if cell_b.parameters.get("MODE") != "LOGIC":
                continue
            if "LUT1_INITVAL" in cell_b.parameters:
                continue  # already dual-packed

            # Pack cell_b's LUT into cell_a's LUT1 slot
            # cell_a keeps LUT0, cell_b becomes LUT1
            init_b = cell_b.parameters.get("LUT0_INITVAL", "0x0000")
            cell_a.parameters["LUT1_INITVAL"] = init_b

            # Wire cell_b's inputs to cell_a's LUT1 ports
            cell_a.ports["A1"] = cell_b.ports.get("A0", ["0"])
            cell_a.ports["B1"] = cell_b.ports.get("B0", ["0"])
            cell_a.ports["C1"] = cell_b.ports.get("C0", ["0"])
            cell_a.ports["D1"] = cell_b.ports.get("D0", ["0"])
            cell_a.ports["F1"] = cell_b.ports.get("F0", ["0"])

            # Mark cell_b as consumed
            used.add(name_b)
            packed += 1
            break

        if packed >= 5000:  # safety limit
            break

    # Remove consumed cells
    for name in used:
        del netlist.cells[name]

    return packed


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
        if cell.cell_type != "TRELLIS_SLICE":
            continue

        init_str = cell.parameters.get("LUT0_INITVAL", "0x0000")
        try:
            init = int(init_str, 16)
        except (ValueError, TypeError):
            continue

        # Check which inputs are constants
        const_inputs: dict[int, int] = {}  # pin_index -> 0 or 1
        for pin_idx, pin_name in enumerate(["A0", "B0", "C0", "D0"]):
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
            # Check if this index is consistent with the constant inputs
            valid = True
            for pin_idx, pin_val in const_inputs.items():
                if ((i >> pin_idx) & 1) != pin_val:
                    valid = False
                    break
            if valid and (init >> i) & 1:
                new_init |= (1 << i)

        if new_init != init:
            cell.parameters["LUT0_INITVAL"] = f"0x{new_init:04X}"
            simplified += 1

        # If truth table is all-0 or all-1, the LUT is constant
        if new_init == 0 or new_init == 0xFFFF:
            to_remove.append(name)

    for name in to_remove:
        del netlist.cells[name]

    return simplified


def deduplicate_luts(netlist: ECP5Netlist) -> int:
    """Eliminate duplicate LUT4 functions with identical INIT and inputs.

    When two TRELLIS_SLICE cells compute the same function on the same
    input bits, one is redundant. Redirect all references to the
    duplicate's output bits to the original's output bits.

    Returns the number of cells eliminated.
    """
    from collections import defaultdict

    # Build signature -> list of cells
    sig_groups: dict[tuple, list[ECP5Cell]] = defaultdict(list)
    for cell in netlist.cells.values():
        if cell.cell_type != "TRELLIS_SLICE":
            continue
        init0 = cell.parameters.get("LUT0_INITVAL", "0x0000")
        a0 = tuple(cell.ports.get("A0", ["?"]))
        b0 = tuple(cell.ports.get("B0", ["?"]))
        c0 = tuple(cell.ports.get("C0", ["?"]))
        d0 = tuple(cell.ports.get("D0", ["?"]))
        sig = (init0, a0, b0, c0, d0)
        sig_groups[sig].append(cell)

    eliminated = 0
    to_remove: set[str] = set()

    for sig, cells in sig_groups.items():
        if len(cells) < 2:
            continue
        keeper = cells[0]
        keeper_f0 = keeper.ports.get("F0", [])
        if not keeper_f0:
            continue

        for dup in cells[1:]:
            dup_f0 = dup.ports.get("F0", [])
            if not dup_f0:
                continue
            # Redirect: everywhere dup's F0 bit appears, replace with keeper's F0 bit
            old_bit = dup_f0[0]
            new_bit = keeper_f0[0]
            for other in netlist.cells.values():
                if other.name == dup.name:
                    continue
                for port_name, bits in list(other.ports.items()):
                    other.ports[port_name] = [
                        new_bit if b == old_bit else b for b in bits
                    ]
            # Also redirect in netlist nets
            for net in netlist.nets.values():
                net.bits = [new_bit if b == old_bit else b for b in net.bits]
            # Redirect in ports
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
    """Absorb 1-input LUT4 cells (buffers/inverters) into their consumers.

    A LUT4 with only 1 variable input is either a buffer (INIT where
    f(0)=0, f(1)=1) or an inverter (f(0)=1, f(1)=0). These can be
    absorbed into the consuming LUT by modifying its truth table to
    include the buffer/inverter function on the corresponding input.

    Returns the number of buffer LUTs eliminated.
    """
    absorbed = 0
    to_remove: set[str] = set()

    # Build output-bit -> cell map
    bit_to_cell: dict[int | str, str] = {}
    for name, cell in netlist.cells.items():
        if cell.cell_type == "TRELLIS_SLICE":
            f0 = cell.ports.get("F0", [])
            if f0 and isinstance(f0[0], int):
                bit_to_cell[f0[0]] = name

    for name, cell in list(netlist.cells.items()):
        if cell.cell_type != "TRELLIS_SLICE" or name in to_remove:
            continue
        if "LUT1_INITVAL" in cell.parameters:
            continue  # dual-LUT, don't touch

        # Count variable inputs
        var_pins: list[tuple[str, int]] = []
        for pin in ["A0", "B0", "C0", "D0"]:
            bits = cell.ports.get(pin, ["0"])
            if bits and isinstance(bits[0], int) and bits[0] >= 2:
                var_pins.append((pin, bits[0]))

        if len(var_pins) != 1:
            continue  # not a 1-input LUT

        _, input_bit = var_pins[0]
        output_bits = cell.ports.get("F0", [])
        if not output_bits or not isinstance(output_bits[0], int):
            continue
        output_bit = output_bits[0]

        init_str = cell.parameters.get("LUT0_INITVAL", "0x0000")
        try:
            init = int(init_str, 16)
        except (ValueError, TypeError):
            continue

        # Determine if buffer or inverter
        # For a 1-input function on pin A0 (bit 0):
        #   buffer: init bit 0 = 0, init bit 1 = 1 -> INIT & 0x3 == 0x2
        #   inverter: init bit 0 = 1, init bit 1 = 0 -> INIT & 0x3 == 0x1
        is_buffer = (init & 0x3) == 0x2
        is_inverter = (init & 0x3) == 0x1

        if not is_buffer and not is_inverter:
            continue

        # Find consumers of this LUT's output bit
        consumers: list[tuple[str, str]] = []  # (cell_name, port_name)
        for cname, ccell in netlist.cells.items():
            if cname == name or cname in to_remove:
                continue
            for pname, pbits in ccell.ports.items():
                if output_bit in pbits:
                    consumers.append((cname, pname))

        if len(consumers) != 1:
            continue  # must have exactly 1 consumer to absorb safely

        cons_name, cons_port = consumers[0]
        cons_cell = netlist.cells[cons_name]

        if is_buffer:
            # Buffer: just replace the consumer's input with the original signal
            cons_cell.ports[cons_port] = [
                input_bit if b == output_bit else b
                for b in cons_cell.ports[cons_port]
            ]
            to_remove.add(name)
            absorbed += 1

        # Inverter absorption requires modifying the consumer's truth table,
        # which is more complex — skip for now to avoid correctness risk.

    for name in to_remove:
        del netlist.cells[name]

    return absorbed


def _eliminate_dead_lut_bits(netlist: ECP5Netlist) -> int:
    """Remove dead LUT functions from dual-LUT slices.

    A LUT output bit that feeds no other cell's input is dead. If it's
    the LUT1 in a dual-LUT slice, stripping it makes the slice single-LUT,
    potentially enabling re-packing with a different partner.
    """
    # Collect all consumed bits
    used: set[int] = set()
    for cell in netlist.cells.values():
        for port_name, bits in cell.ports.items():
            # Input-type ports
            if not any(port_name.startswith(p) for p in ("F", "Q", "CO", "S0", "S1", "DO", "P", "R", "Z")):
                for b in bits:
                    if isinstance(b, int) and b >= 2:
                        used.add(b)
    for port_info in netlist.ports.values():
        for b in port_info.get("bits", []):
            if isinstance(b, int) and b >= 2:
                used.add(b)

    stripped = 0
    for cell in netlist.cells.values():
        if cell.cell_type != "TRELLIS_SLICE":
            continue
        if "LUT1_INITVAL" not in cell.parameters:
            continue
        # Check if LUT1's output is dead
        f1 = cell.ports.get("F1", [])
        if f1 and isinstance(f1[0], int) and f1[0] not in used:
            # Strip LUT1
            del cell.parameters["LUT1_INITVAL"]
            for pin in ("A1", "B1", "C1", "D1", "F1"):
                cell.ports.pop(pin, None)
            stripped += 1
            continue
        # Check if LUT0's output is dead (promote LUT1 to LUT0)
        f0 = cell.ports.get("F0", [])
        if f0 and isinstance(f0[0], int) and f0[0] not in used:
            cell.parameters["LUT0_INITVAL"] = cell.parameters.pop("LUT1_INITVAL")
            for pin in ("A", "B", "C", "D", "F"):
                p1 = f"{pin}1"
                p0 = f"{pin}0"
                if p1 in cell.ports:
                    cell.ports[p0] = cell.ports.pop(p1)
            stripped += 1

    return stripped


def merge_lut_chains(netlist: ECP5Netlist) -> int:
    """Merge chained LUT4 pairs where the combined function fits in 4 inputs.

    When LUT_A's output feeds LUT_B's input, and their combined unique
    variable inputs number ≤4, LUT_B can absorb LUT_A's function by
    computing the composed truth table. LUT_A is then eliminated.

    This is the core of priority-cut technology mapping applied post-hoc
    to an already-mapped netlist. It catches per-bit chains that the
    IR-level packer misses because the IR operates at multi-bit granularity.

    Constraint: LUT_A must have exactly one consumer (single-fanout)
    to ensure the merge doesn't duplicate logic.

    Returns the number of LUT4 functions eliminated.
    """
    # Build output-bit -> (cell_name, slot) map
    bit_to_lut: dict[int, tuple[str, str]] = {}
    for name, cell in netlist.cells.items():
        if cell.cell_type != "TRELLIS_SLICE":
            continue
        for slot in ("0", "1"):
            f_key = f"F{slot}"
            f_bits = cell.ports.get(f_key, [])
            if f_bits and isinstance(f_bits[0], int) and f_bits[0] >= 2:
                bit_to_lut[f_bits[0]] = (name, slot)

    # Build fanout count: how many LUT inputs reference each output bit
    bit_fanout: dict[int, int] = {}
    for cell in netlist.cells.values():
        if cell.cell_type != "TRELLIS_SLICE":
            continue
        for slot in ("0", "1"):
            for pin in (f"A{slot}", f"B{slot}", f"C{slot}", f"D{slot}"):
                bits = cell.ports.get(pin, [])
                if bits and isinstance(bits[0], int) and bits[0] >= 2:
                    bit_fanout[bits[0]] = bit_fanout.get(bits[0], 0) + 1

    merged = 0
    absorbed_bits: set[int] = set()  # output bits of absorbed LUTs

    for name, cell in list(netlist.cells.items()):
        if cell.cell_type != "TRELLIS_SLICE":
            continue

        for slot in ("0", "1"):
            init_key = f"LUT{slot}_INITVAL" if slot == "1" else "LUT0_INITVAL"
            if init_key not in cell.parameters:
                continue

            try:
                my_init = int(cell.parameters[init_key], 16)
            except (ValueError, TypeError):
                continue

            # Find which input comes from another LUT4
            my_pins = {}  # pin_name -> bit_value
            feeder_pin = None
            feeder_bit = None
            for pin_idx, pin in enumerate((f"A{slot}", f"B{slot}", f"C{slot}", f"D{slot}")):
                bits = cell.ports.get(pin, ["0"])
                if bits and isinstance(bits[0], int) and bits[0] >= 2:
                    bit = bits[0]
                    my_pins[pin] = bit
                    if bit in bit_to_lut and bit not in absorbed_bits:
                        src_name, src_slot = bit_to_lut[bit]
                        if src_name != name and bit_fanout.get(bit, 0) == 1:
                            feeder_pin = pin
                            feeder_bit = bit

            if feeder_pin is None:
                # Try multi-fanout: feeder with fanout ≤ 3 and ≤ 2 variable inputs
                for pin_idx2, pin2 in enumerate((f"A{slot}", f"B{slot}", f"C{slot}", f"D{slot}")):
                    bits2 = cell.ports.get(pin2, ["0"])
                    if bits2 and isinstance(bits2[0], int) and bits2[0] >= 2:
                        bit2 = bits2[0]
                        if bit2 in bit_to_lut and bit2 not in absorbed_bits:
                            sn2, ss2 = bit_to_lut[bit2]
                            if sn2 != name and bit_fanout.get(bit2, 0) <= 3:
                                sc2 = netlist.cells.get(sn2)
                                if sc2:
                                    # Count feeder's variable inputs
                                    fv2 = sum(1 for p in (f"A{ss2}", f"B{ss2}", f"C{ss2}", f"D{ss2}")
                                             if sc2.ports.get(p, ["0"])[0] not in ("0", "1", "x")
                                             and isinstance(sc2.ports.get(p, ["0"])[0], int)
                                             and sc2.ports.get(p, ["0"])[0] >= 2)
                                    if fv2 <= 2:
                                        # Feeder has ≤1 variable input — cheap to duplicate
                                        feeder_pin = pin2
                                        feeder_bit = bit2
                                        break

            if feeder_pin is None:
                continue

            src_name, src_slot = bit_to_lut[feeder_bit]
            src_cell = netlist.cells.get(src_name)
            if src_cell is None:
                continue

            src_init_key = f"LUT{src_slot}_INITVAL" if src_slot == "1" else "LUT0_INITVAL"
            try:
                src_init = int(src_cell.parameters.get(src_init_key, "0x0000"), 16)
            except (ValueError, TypeError):
                continue

            # Collect feeder's variable inputs
            feeder_vars: dict[str, int] = {}
            for pin_idx, pin in enumerate((f"A{src_slot}", f"B{src_slot}", f"C{src_slot}", f"D{src_slot}")):
                bits = src_cell.ports.get(pin, ["0"])
                if bits and isinstance(bits[0], int) and bits[0] >= 2:
                    feeder_vars[pin] = bits[0]

            # My variable inputs (excluding the feeder connection)
            my_other_vars: dict[str, int] = {}
            for pin, bit in my_pins.items():
                if pin != feeder_pin:
                    my_other_vars[pin] = bit

            # Combined unique variable inputs
            all_bits = set(feeder_vars.values()) | set(my_other_vars.values())
            if len(all_bits) > 4:
                continue

            # Compute composed truth table
            # Map combined inputs to LUT4 pin indices
            all_bits_list = sorted(all_bits)
            bit_to_idx = {b: i for i, b in enumerate(all_bits_list)}

            # Feeder pin index in feeder's LUT
            feeder_pin_indices: dict[int, int] = {}  # feeder_bit -> feeder_pin_idx
            for pin_idx, pin in enumerate((f"A{src_slot}", f"B{src_slot}", f"C{src_slot}", f"D{src_slot}")):
                bits = src_cell.ports.get(pin, ["0"])
                if bits and isinstance(bits[0], int) and bits[0] >= 2:
                    feeder_pin_indices[bits[0]] = pin_idx

            # My pin index mapping
            my_pin_idx = {f"A{slot}": 0, f"B{slot}": 1, f"C{slot}": 2, f"D{slot}": 3}
            feeder_in_my_idx = my_pin_idx.get(feeder_pin, 0)

            composed_init = 0
            for i in range(16):
                # Map combined input index to individual bit values
                input_vals = {}
                for bit, idx in bit_to_idx.items():
                    input_vals[bit] = (i >> idx) & 1

                # Evaluate feeder LUT
                feeder_lut_idx = 0
                for bit, pin_idx in feeder_pin_indices.items():
                    if input_vals.get(bit, 0):
                        feeder_lut_idx |= (1 << pin_idx)
                feeder_result = (src_init >> feeder_lut_idx) & 1

                # Evaluate my LUT with feeder result substituted
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

            # Apply the composed truth table
            cell.parameters[init_key] = f"0x{composed_init:04X}"

            # Rewire my inputs to the combined set
            new_pins = {f"A{slot}": "0", f"B{slot}": "0", f"C{slot}": "0", f"D{slot}": "0"}
            for bit, idx in bit_to_idx.items():
                pin_name = [f"A{slot}", f"B{slot}", f"C{slot}", f"D{slot}"][idx]
                new_pins[pin_name] = bit
            for pin, val in new_pins.items():
                if isinstance(val, int):
                    cell.ports[pin] = [val]
                else:
                    cell.ports[pin] = [val]

            # Mark feeder's output bit as absorbed by this consumer
            absorbed_bits.add(feeder_bit)
            merged += 1

            # Only remove the feeder if ALL its consumers have absorbed it
            # (fanout fully consumed). Decrement the fanout counter.
            remaining_fanout = bit_fanout.get(feeder_bit, 1) - 1
            bit_fanout[feeder_bit] = remaining_fanout
            if remaining_fanout > 0:
                break  # feeder still needed by other consumers

            # If the feeder was in a dual-LUT slice, remove only its slot
            if src_slot == "0" and "LUT1_INITVAL" in src_cell.parameters:
                # Promote LUT1 to LUT0
                src_cell.parameters["LUT0_INITVAL"] = src_cell.parameters.pop("LUT1_INITVAL")
                for pin in ("A", "B", "C", "D", "F"):
                    p1 = f"{pin}1"
                    p0 = f"{pin}0"
                    if p1 in src_cell.ports:
                        src_cell.ports[p0] = src_cell.ports.pop(p1)
            elif src_slot == "1":
                # Remove LUT1 from the dual-LUT slice
                src_cell.parameters.pop("LUT1_INITVAL", None)
                for pin in ("A1", "B1", "C1", "D1", "F1"):
                    src_cell.ports.pop(pin, None)
            else:
                # Single-LUT cell, remove entirely
                # But only if we already removed its only function
                if "LUT1_INITVAL" not in src_cell.parameters:
                    del netlist.cells[src_name]

            break  # one merge per consumer cell per pass

    return merged


def pack_slices(netlist: ECP5Netlist) -> dict[str, int]:
    """Run all slice packing and simplification passes. Returns counts."""
    s1 = simplify_constant_luts(netlist)
    dd = deduplicate_luts(netlist)
    ab = absorb_buffers(netlist)
    # Eliminate dead LUT bits from dual-LUT slices
    _eliminate_dead_lut_bits(netlist)
    # Priority-cut chain merging: absorb feeder LUTs into consumers
    mc = 0
    for _ in range(5):
        m = merge_lut_chains(netlist)
        if m == 0:
            break
        mc += m
        simplify_constant_luts(netlist)
    s2 = simplify_constant_luts(netlist)
    d = pack_dual_lut4(netlist)
    p = pack_pfumx(netlist)
    l = pack_l6mux21(netlist)
    s3 = simplify_constant_luts(netlist)
    return {
        "const_lut_simplify": s1 + s2 + s3,
        "lut_dedup": dd,
        "buffer_absorb": ab,
        "chain_merge": mc,
        "dual_lut4": d,
        "pfumx": p,
        "l6mux21": l,
    }
