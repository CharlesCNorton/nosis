"""Nosis $readmemh / $readmemb support — parse memory initialization files.

Reads Verilog hex ($readmemh) and binary ($readmemb) memory initialization
files and converts them to initialization data for BRAM cells.
"""

from __future__ import annotations

from pathlib import Path

__all__ = [
    "parse_readmemh",
    "parse_readmemb",
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
