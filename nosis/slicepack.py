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
            if cell_a.parameters.get("MODE") != "LOGIC":
                break
            if cell_b.parameters.get("MODE") != "LOGIC":
                continue

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


def pack_slices(netlist: ECP5Netlist) -> dict[str, int]:
    """Run all slice packing passes. Returns counts."""
    return {
        "dual_lut4": pack_dual_lut4(netlist),
        "pfumx": pack_pfumx(netlist),
        "l6mux21": pack_l6mux21(netlist),
    }
