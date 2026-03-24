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


def readmem_to_dp16kd_initvals(
    mem_data: dict[int, int],
    *,
    data_width: int = 18,
    depth: int = 1024,
) -> dict[str, str]:
    """Convert memory initialization data to DP16KD INITVAL_XX parameters.

    Each INITVAL_XX parameter encodes 16 entries of the memory as a 320-bit
    hex string (20 hex nibbles per entry for 18-bit mode, etc.).

    Returns ``{"INITVAL_00": "0x...", "INITVAL_01": "0x...", ...}`` for
    up to 64 INITVAL parameters (covering the full 16Kbit BRAM).
    """
    # DP16KD stores data in 64 INITVAL rows, each row covers (16384 / 64) = 256 bits
    # Each row is written as a 320-bit value (padded to 80 hex nibbles)
    # In X18 mode: 16 entries per row, 18 bits each
    # In X9 mode: 32 entries per row, 9 bits each
    # General formula: entries_per_row = 256 // data_width

    if data_width <= 0:
        return {}

    entries_per_row = max(1, 256 // data_width)
    total_rows = min(64, (depth + entries_per_row - 1) // entries_per_row)
    mask = (1 << data_width) - 1

    initvals: dict[str, str] = {}
    for row in range(total_rows):
        row_val = 0
        for entry in range(entries_per_row):
            addr = row * entries_per_row + entry
            value = mem_data.get(addr, 0) & mask
            row_val |= value << (entry * data_width)
        # Format as 0x followed by enough hex digits for 320 bits (80 nibbles)
        hex_str = f"0x{row_val:080X}"
        initvals[f"INITVAL_{row:02X}"] = hex_str

    # Fill remaining rows with zeros
    for row in range(total_rows, 64):
        initvals[f"INITVAL_{row:02X}"] = "0x" + "0" * 80

    return initvals
