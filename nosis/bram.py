"""Nosis BRAM inference — recognize array patterns and emit DP16KD instances.

Scans the IR for MEMORY cells (arrays inferred from behavioral HDL) and
determines whether they can be mapped to ECP5 DP16KD block RAMs.

DP16KD is a true dual-port 16Kbit BRAM:
  - Configurable as 16Kx1, 8Kx2, 4Kx4, 2Kx9, 1Kx18, 512x36
  - Two independent read/write ports
  - Synchronous read and write
"""

from __future__ import annotations

from nosis.ir import Cell, Module, PrimOp

__all__ = [
    "infer_brams",
]


def _fits_dp16kd(depth: int, width: int) -> tuple[int, int] | None:
    """Check if array dimensions fit a DP16KD configuration.

    Returns ``(addr_bits, data_width)`` for the best-fit config, or None.
    """
    configs = [
        (14, 1, 16384),   # 16Kx1
        (13, 2, 8192),    # 8Kx2
        (12, 4, 4096),    # 4Kx4
        (11, 9, 2048),    # 2Kx9 (8 data + 1 parity)
        (10, 18, 1024),   # 1Kx18 (16 data + 2 parity)
        (9, 36, 512),     # 512x36 (32 data + 4 parity)
    ]
    for addr_bits, data_width, max_depth in configs:
        if depth <= max_depth and width <= data_width:
            return addr_bits, data_width
    return None


def _count_brams_needed(depth: int, width: int) -> int:
    """Count how many DP16KD instances are needed for an array."""
    fit = _fits_dp16kd(depth, width)
    if fit is not None:
        return 1
    # Multiple BRAMs needed — width tiling
    best_data_width = 36  # widest single BRAM
    brams_wide = (width + best_data_width - 1) // best_data_width
    best_depth = 512  # depth for 36-wide
    brams_deep = (depth + best_depth - 1) // best_depth
    return brams_wide * brams_deep


def infer_brams(mod: Module) -> int:
    """Tag MEMORY cells that should become DP16KD instances.

    Adds ``bram_config`` to cell params for cells that qualify.
    Returns the number of memories tagged for BRAM inference.
    """
    tagged = 0

    for cell in mod.cells.values():
        if cell.op != PrimOp.MEMORY:
            continue

        depth = int(cell.params.get("depth", 0))
        width = int(cell.params.get("width", 0))

        if depth <= 0 or width <= 0:
            continue

        # Minimum threshold: don't waste a BRAM on tiny arrays
        # that fit efficiently in distributed RAM (LUT-based)
        total_bits = depth * width
        if total_bits < 256:
            continue

        fit = _fits_dp16kd(depth, width)
        if fit is not None:
            addr_bits, data_width = fit
            cell.params["bram_config"] = "DP16KD"
            cell.params["bram_addr_bits"] = addr_bits
            cell.params["bram_data_width"] = data_width
            cell.params["bram_count"] = 1
            tagged += 1
        else:
            count = _count_brams_needed(depth, width)
            if count <= 56:  # ECP5-25F has 56 BRAMs
                cell.params["bram_config"] = "DP16KD_TILED"
                cell.params["bram_count"] = count
                tagged += 1

    return tagged
