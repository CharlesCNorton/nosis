"""Nosis $readmemh / $readmemb support — parse memory initialization files.

Reads Verilog hex ($readmemh) and binary ($readmemb) memory initialization
files and converts them to initialization data for BRAM cells.
"""

from __future__ import annotations

from pathlib import Path

__all__ = [
    "parse_readmemh",
    "parse_readmemb",
    "readmem_to_dp16kd_initvals",
]


def parse_readmemh(path: str | Path) -> dict[int, int]:
    """Parse a $readmemh hex file. Returns {address: value}."""
    result: dict[int, int] = {}
    addr = 0
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("//"):
            continue
        if line.startswith("@"):
            addr = int(line[1:].strip(), 16)
            continue
        for word in line.split():
            if word.startswith("//"):
                break
            result[addr] = int(word, 16)
            addr += 1
    return result


def parse_readmemb(path: str | Path) -> dict[int, int]:
    """Parse a $readmemb binary file. Returns {address: value}."""
    result: dict[int, int] = {}
    addr = 0
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("//"):
            continue
        if line.startswith("@"):
            addr = int(line[1:].strip(), 16)
            continue
        for word in line.split():
            if word.startswith("//"):
                break
            result[addr] = int(word, 2)
            addr += 1
    return result


def _physical_entry_width(data_width: int) -> int:
    """Return the physical entry width in the DP16KD INITVAL encoding.

    The ECP5 DP16KD INITVAL rows use a fixed physical layout where wider
    modes (X9, X18, X36) interleave parity bits with data bits.  The
    physical entry width determines both how many entries fit per 320-bit
    INITVAL row and the bit spacing between entries.

    Layout per mode:
      X1:  1 bit  (no parity)  — 256 entries/row (+ 64 unused bits)
      X2:  2 bits (no parity)  — 128 entries/row (+ 64 unused bits)
      X4:  4 bits (no parity)  —  64 entries/row (+ 64 unused bits)
      X9:  10 bits (9 data + 1 parity) — 32 entries/row
      X18: 20 bits (9 data + 1 parity + 9 data + 1 parity) — 16 entries/row
      X36: 40 bits (4×(9 data + 1 parity)) — 8 entries/row
    """
    if data_width >= 36:
        return 40
    if data_width >= 18:
        return 20
    if data_width >= 9:
        return 10
    return data_width  # X1, X2, X4 — no parity bits


def _encode_entry(value: int, data_width: int) -> int:
    """Encode a data value into the DP16KD physical INITVAL bit layout.

    For narrow modes (X1–X4), the value passes through unchanged.
    For X9: inserts a zero parity bit at position 9.
    For X18: inserts zero parity bits at positions 9 and 19.
    For X36: inserts zero parity bits at positions 9, 19, 29, 39.
    """
    if data_width <= 4:
        return value & ((1 << data_width) - 1)

    if data_width <= 9:
        # X9: bits [8:0] = data[8:0], bit [9] = parity (0)
        d = value & 0x1FF
        return d  # parity bit 9 is 0, so just the lower 9 bits in a 10-bit slot

    if data_width <= 18:
        # X18: bits [8:0]=data[8:0], bit[9]=parity0,
        #       bits[18:10]=data[17:9], bit[19]=parity1
        lo = value & 0x1FF          # data[8:0]
        hi = (value >> 9) & 0x1FF   # data[17:9]
        return lo | (hi << 10)      # parity bits at 9 and 19 are 0

    # X36: four 9-bit data groups with parity after each
    result = 0
    for group in range(4):
        chunk = (value >> (group * 9)) & 0x1FF
        result |= chunk << (group * 10)
    return result


def readmem_to_dp16kd_initvals(
    mem_data: dict[int, int],
    *,
    data_width: int = 18,
    depth: int = 1024,
) -> dict[str, str]:
    """Convert memory initialization data to DP16KD INITVAL_XX parameters.

    Each INITVAL_XX parameter is a 320-bit hex string encoding one row
    of the BRAM.  The physical layout includes parity bit positions for
    X9/X18/X36 modes — data values are encoded with
    :func:`_encode_entry` to insert the parity gaps.

    Returns ``{"INITVAL_00": "0x...", ...}`` for all 64 rows.
    """
    if data_width <= 0:
        return {}

    phys_width = _physical_entry_width(data_width)
    entries_per_row = max(1, 320 // phys_width) if phys_width > 0 else 1
    total_rows = min(64, (depth + entries_per_row - 1) // entries_per_row)
    mask = (1 << data_width) - 1

    initvals: dict[str, str] = {}
    for row in range(total_rows):
        row_val = 0
        for entry in range(entries_per_row):
            addr = row * entries_per_row + entry
            value = mem_data.get(addr, 0) & mask
            encoded = _encode_entry(value, data_width)
            row_val |= encoded << (entry * phys_width)
        # Format as 0x followed by enough hex digits for 320 bits (80 nibbles)
        hex_str = f"0x{row_val:080X}"
        initvals[f"INITVAL_{row:02X}"] = hex_str

    # Fill remaining rows with zeros
    for row in range(total_rows, 64):
        initvals[f"INITVAL_{row:02X}"] = "0x" + "0" * 80

    return initvals
