"""Nosis ECP5 technology mapping — map IR primitives to ECP5 cells.

Maps the technology-independent Nosis IR onto Lattice ECP5 primitives:
  - Combinational logic -> LUT4
  - Sequential logic -> TRELLIS_FF
  - Constants -> tied signals
  - Ports -> top-level port declarations

This is the first-pass mapper: LUT4 + FF only. BRAM inference, DSP
mapping, and carry chain extraction are separate passes added later.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from nosis.ir import PrimOp

__all__ = [
    "ECP5Cell",
    "ECP5Net",
    "ECP5Netlist",
]


@dataclass(slots=True)
class ECP5Net:
    """A net in the ECP5 netlist."""
    name: str
    bits: list[int | str]  # bit indices or constant "0"/"1"/"x"

    def __repr__(self) -> str:
        return f"ECP5Net({self.name!r}, {self.bits})"


@dataclass(slots=True)
class ECP5Cell:
    """A technology-mapped ECP5 cell."""
    name: str
    cell_type: str  # "TRELLIS_SLICE", "TRELLIS_FF", etc.
    parameters: dict[str, str] = field(default_factory=dict)
    ports: dict[str, list[int | str]] = field(default_factory=dict)
    attributes: dict[str, str] = field(default_factory=dict)

    def __repr__(self) -> str:
        return f"ECP5Cell({self.name!r}, {self.cell_type})"


@dataclass(slots=True)
class ECP5Netlist:
    """Complete ECP5-mapped netlist ready for nextpnr JSON export."""
    top: str
    cells: dict[str, ECP5Cell] = field(default_factory=dict)
    nets: dict[str, ECP5Net] = field(default_factory=dict)
    ports: dict[str, dict[str, Any]] = field(default_factory=dict)
    _bit_counter: int = 2  # 0 and 1 are reserved for constant 0/1

    def alloc_bit(self) -> int:
        """Allocate a fresh signal bit index. Bits 0/1 are reserved for constants."""
        bit = self._bit_counter
        self._bit_counter += 1
        return bit

    def alloc_bits(self, count: int) -> list[int]:
        """Allocate *count* consecutive signal bit indices."""
        return [self.alloc_bit() for _ in range(count)]

    def add_net(self, name: str, width: int) -> ECP5Net:
        """Create a named net with *width* freshly allocated bits."""
        bits: list[int | str] = list(self.alloc_bits(width))
        net = ECP5Net(name=name, bits=bits)
        self.nets[name] = net
        return net

    def add_cell(self, name: str, cell_type: str) -> ECP5Cell:
        """Create a named cell of the given ECP5 primitive type."""
        cell = ECP5Cell(name=name, cell_type=cell_type)
        self.cells[name] = cell
        return cell

    def stats(self) -> dict[str, int]:
        """Return cell type counts: ``{cell_type: count}``."""
        from collections import Counter
        type_counts = Counter(c.cell_type for c in self.cells.values())
        return {
            "cells": len(self.cells),
            "nets": len(self.nets),
            "ports": len(self.ports),
            **{t: n for t, n in sorted(type_counts.items())},
        }


# ---------------------------------------------------------------------------
# LUT4 truth table computation
# ---------------------------------------------------------------------------

def _compute_lut4_init(op: PrimOp, num_inputs: int) -> int:
    """Compute a 16-bit LUT4 INIT value for a given primitive operation.

    Inputs are mapped to LUT inputs A, B, C, D (indices 0, 1, 2, 3).
    The truth table is: for each combination of inputs i (0..15),
    INIT[i] = f(A=i&1, B=(i>>1)&1, C=(i>>2)&1, D=(i>>3)&1).

    For comparison ops used in the per-bit comparator chain:
      A = a_bit, B = b_bit, C = borrow_in (less-than so far from lower bits)
      Result = borrow_out (this bit position says a < b so far)
    """
    init = 0
    for i in range(16):
        a = (i >> 0) & 1
        b = (i >> 1) & 1
        c = (i >> 2) & 1
        d = (i >> 3) & 1  # noqa: F841 — reserved for future 4-input ops

        if op == PrimOp.AND:
            result = a & b
        elif op == PrimOp.OR:
            result = a | b
        elif op == PrimOp.XOR:
            result = a ^ b
        elif op == PrimOp.NOT:
            result = 1 - a
        elif op == PrimOp.MUX:
            # S=A, false=B, true=C -> result = C if A else B
            result = c if a else b
        elif op == PrimOp.EQ:
            result = 1 if a == b else 0
        elif op == PrimOp.NE:
            result = 1 if a != b else 0
        elif op == PrimOp.LT:
            # Per-bit comparator: A=a_bit, B=b_bit, C=borrow_in
            # borrow_out = (!a & b) | (!(a ^ b) & borrow_in)
            # i.e. b>a at this bit, or equal and previous borrow propagates
            result = ((~a & 1) & b) | (((a ^ b) ^ 1) & c)
        elif op == PrimOp.LE:
            # Same as LT but also true when fully equal (final stage adds OR with eq chain)
            result = ((~a & 1) & b) | (((a ^ b) ^ 1) & c)
        elif op == PrimOp.GT:
            # Swap a/b: a>b iff b<a
            result = ((~b & 1) & a) | (((a ^ b) ^ 1) & c)
        elif op == PrimOp.GE:
            result = ((~b & 1) & a) | (((a ^ b) ^ 1) & c)
        elif op == PrimOp.REDUCE_AND:
            result = a  # single bit: AND = identity
        elif op == PrimOp.REDUCE_OR:
            result = a
        elif op == PrimOp.REDUCE_XOR:
            result = a
        else:
            result = 0  # default: constant 0

        if result:
            init |= (1 << i)

    return init


def _const_bits(value: int, width: int) -> list[int | str]:
    """Convert an integer constant to a list of nextpnr bit references."""
    bits: list[int | str] = []
    for i in range(width):
        bit = (value >> i) & 1
        bits.append("1" if bit else "0")
    return bits
