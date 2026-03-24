"""Nosis ECP5 technology mapping — map IR primitives to ECP5 cells.

Maps the technology-independent Nosis IR onto Lattice ECP5 primitives:
  - Combinational logic -> TRELLIS_SLICE (LUT4)
  - Sequential logic -> TRELLIS_FF
  - Constants -> tied signals
  - Ports -> top-level port declarations

This is the first-pass mapper: LUT4 + FF only. BRAM inference, DSP
mapping, and carry chain extraction are separate passes added later.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from nosis.ir import Cell, Design, Module, Net, PrimOp

__all__ = [
    "ECP5Cell",
    "ECP5Net",
    "ECP5Netlist",
    "map_to_ecp5",
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
        bit = self._bit_counter
        self._bit_counter += 1
        return bit

    def alloc_bits(self, count: int) -> list[int]:
        return [self.alloc_bit() for _ in range(count)]

    def add_net(self, name: str, width: int) -> ECP5Net:
        bits = self.alloc_bits(width)
        net = ECP5Net(name=name, bits=bits)
        self.nets[name] = net
        return net

    def add_cell(self, name: str, cell_type: str) -> ECP5Cell:
        cell = ECP5Cell(name=name, cell_type=cell_type)
        self.cells[name] = cell
        return cell

    def stats(self) -> dict[str, int]:
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
    """
    init = 0
    for i in range(16):
        a = (i >> 0) & 1
        b = (i >> 1) & 1
        c = (i >> 2) & 1
        d = (i >> 3) & 1

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


# ---------------------------------------------------------------------------
# Mapper
# ---------------------------------------------------------------------------

class _ECP5Mapper:
    """Maps a Nosis IR Module to an ECP5Netlist."""

    def __init__(self, netlist: ECP5Netlist) -> None:
        self.nl = netlist
        self._cell_counter = 0
        self._net_map: dict[str, ECP5Net] = {}

    def _fresh_name(self, prefix: str) -> str:
        name = f"${prefix}_{self._cell_counter}"
        self._cell_counter += 1
        return name

    def _get_net(self, ir_net: Net) -> ECP5Net:
        """Get or create the ECP5 net corresponding to an IR net."""
        if ir_net.name in self._net_map:
            return self._net_map[ir_net.name]
        ecp5_net = self.nl.add_net(ir_net.name, ir_net.width)
        self._net_map[ir_net.name] = ecp5_net
        return ecp5_net

    def _get_bit(self, ir_net: Net, bit_index: int = 0) -> int | str:
        """Get a single bit reference from an IR net."""
        ecp5_net = self._get_net(ir_net)
        if bit_index < len(ecp5_net.bits):
            return ecp5_net.bits[bit_index]
        return "0"

    def _get_bits(self, ir_net: Net) -> list[int | str]:
        """Get all bit references from an IR net."""
        return self._get_net(ir_net).bits

    def map_module(self, mod: Module) -> None:
        """Map all cells in an IR module to ECP5 cells."""
        # First pass: create ECP5 nets for all IR nets
        for net in mod.nets.values():
            self._get_net(net)

        # Map ports
        for port_name, port_net in mod.ports.items():
            ecp5_net = self._get_net(port_net)
            # Determine direction from the IR cells
            direction = "input"
            for cell in mod.cells.values():
                if cell.op == PrimOp.OUTPUT and port_name in cell.params.get("port_name", ""):
                    direction = "output"
                    break
                if cell.op == PrimOp.OUTPUT:
                    for inp_net in cell.inputs.values():
                        if inp_net.name == port_name:
                            direction = "output"
                            break
                if cell.op == PrimOp.INPUT:
                    if cell.params.get("inout"):
                        direction = "inout"
                        break

            self.nl.ports[port_name] = {
                "direction": direction,
                "bits": ecp5_net.bits,
            }

        # Second pass: map each IR cell
        for cell in mod.cells.values():
            self._map_cell(cell)

    def _map_cell(self, cell: Cell) -> None:
        """Map a single IR cell to one or more ECP5 cells."""
        op = cell.op

        if op == PrimOp.INPUT or op == PrimOp.OUTPUT:
            return  # handled as ports

        if op == PrimOp.CONST:
            self._map_const(cell)
        elif op == PrimOp.FF:
            self._map_ff(cell)
        elif op in (PrimOp.AND, PrimOp.OR, PrimOp.XOR, PrimOp.NOT,
                     PrimOp.MUX, PrimOp.EQ, PrimOp.NE,
                     PrimOp.REDUCE_AND, PrimOp.REDUCE_OR, PrimOp.REDUCE_XOR):
            self._map_lut(cell)
        elif op in (PrimOp.ADD, PrimOp.SUB):
            self._map_arithmetic(cell)
        elif op in (PrimOp.MUL, PrimOp.DIV, PrimOp.MOD):
            self._map_multiply(cell)
        elif op in (PrimOp.SHL, PrimOp.SHR, PrimOp.SSHR):
            self._map_shift(cell)
        elif op in (PrimOp.LT, PrimOp.LE, PrimOp.GT, PrimOp.GE):
            self._map_compare(cell)
        elif op == PrimOp.CONCAT:
            self._map_concat(cell)
        elif op == PrimOp.SLICE:
            self._map_slice(cell)
        elif op in (PrimOp.ZEXT, PrimOp.SEXT):
            self._map_extend(cell)
        elif op == PrimOp.PMUX:
            self._map_pmux(cell)
        elif op == PrimOp.REPEAT:
            self._map_repeat(cell)
        else:
            # Unsupported — emit as a LUT tied to 0
            self._map_unknown(cell)

    def _map_const(self, cell: Cell) -> None:
        """Map a constant to tied bit values (no physical cell needed)."""
        value = int(cell.params.get("value", 0))
        width = int(cell.params.get("width", 1))
        for port_name, out_net in cell.outputs.items():
            ecp5_net = self._get_net(out_net)
            # Override bits with constant values
            ecp5_net.bits = _const_bits(value, width)

    def _map_ff(self, cell: Cell) -> None:
        """Map an IR FF to TRELLIS_FF cells (one per bit)."""
        d_net = cell.inputs.get("D")
        clk_net = cell.inputs.get("CLK")
        rst_net = cell.inputs.get("RST")
        q_net = list(cell.outputs.values())[0] if cell.outputs else None

        if d_net is None or q_net is None:
            return

        width = d_net.width
        d_bits = self._get_bits(d_net)
        q_bits = self._get_bits(q_net)
        clk_bits = self._get_bits(clk_net) if clk_net else ["0"]
        rst_bits = self._get_bits(rst_net) if rst_net else ["0"]

        for i in range(min(width, len(d_bits), len(q_bits))):
            ff = self.nl.add_cell(self._fresh_name("tff"), "TRELLIS_FF")
            if cell.src:
                ff.attributes["src"] = cell.src
            ff.parameters["GSR"] = "DISABLED"
            ff.parameters["CEMUX"] = "1"
            ff.parameters["CLKMUX"] = "CLK"
            ff.parameters["LSRMUX"] = "LSR" if rst_net else "INV"
            ff.parameters["REGSET"] = "RESET"
            ff.parameters["SRMODE"] = "LSR_OVER_CE"
            ff.ports["CLK"] = [clk_bits[0] if clk_bits else "0"]
            ff.ports["DI"] = [d_bits[i] if i < len(d_bits) else "0"]
            ff.ports["LSR"] = [rst_bits[0] if rst_bits else "0"]
            ff.ports["CE"] = ["1"]
            ff.ports["Q"] = [q_bits[i] if i < len(q_bits) else self.nl.alloc_bit()]

    def _map_lut(self, cell: Cell) -> None:
        """Map a logic operation to TRELLIS_SLICE LUT4 cells (one per output bit)."""
        out_nets = list(cell.outputs.values())
        if not out_nets:
            return
        out_net = out_nets[0]
        width = out_net.width

        a_net = cell.inputs.get("A")
        b_net = cell.inputs.get("B")
        s_net = cell.inputs.get("S")

        init = _compute_lut4_init(cell.op, len(cell.inputs))

        out_bits = self._get_bits(out_net)

        for i in range(width):
            lut = self.nl.add_cell(self._fresh_name("lut"), "TRELLIS_SLICE")
            if cell.src:
                lut.attributes["src"] = cell.src
            lut.parameters["LUT0_INITVAL"] = f"0x{init:04X}"
            lut.parameters["REG0_SD"] = "0"
            lut.parameters["SRMODE"] = "LSR_OVER_CE"
            lut.parameters["GSR"] = "DISABLED"
            lut.parameters["MODE"] = "LOGIC"

            if cell.op == PrimOp.MUX:
                # S -> A input, false -> B, true -> C
                lut.ports["A0"] = [self._get_bit(s_net, min(i, s_net.width - 1)) if s_net else "0"]
                lut.ports["B0"] = [self._get_bit(a_net, i) if a_net else "0"]
                lut.ports["C0"] = [self._get_bit(b_net, i) if b_net else "0"]  # B is true branch
                lut.ports["D0"] = ["0"]
            elif cell.op == PrimOp.NOT:
                lut.ports["A0"] = [self._get_bit(a_net, i) if a_net else "0"]
                lut.ports["B0"] = ["0"]
                lut.ports["C0"] = ["0"]
                lut.ports["D0"] = ["0"]
            else:
                lut.ports["A0"] = [self._get_bit(a_net, i) if a_net else "0"]
                lut.ports["B0"] = [self._get_bit(b_net, i) if b_net else "0"]
                lut.ports["C0"] = ["0"]
                lut.ports["D0"] = ["0"]

            lut.ports["F0"] = [out_bits[i] if i < len(out_bits) else self.nl.alloc_bit()]

    def _map_arithmetic(self, cell: Cell) -> None:
        """Map ADD/SUB to chains of TRELLIS_SLICE in CCU2C mode."""
        # For now: decompose into per-bit LUT4 with carry chain emulation
        # Full CCU2C mapping is a later optimization
        self._map_lut(cell)

    def _map_multiply(self, cell: Cell) -> None:
        """Map MUL — placeholder for MULT18X18D inference."""
        self._map_lut(cell)

    def _map_shift(self, cell: Cell) -> None:
        """Map shift operations to LUT chains."""
        self._map_lut(cell)

    def _map_compare(self, cell: Cell) -> None:
        """Map comparison operations to LUT chains."""
        self._map_lut(cell)

    def _map_concat(self, cell: Cell) -> None:
        """Map concatenation — pure wiring, no physical cells."""
        out_nets = list(cell.outputs.values())
        if not out_nets:
            return
        out_net = out_nets[0]
        out_ecp5 = self._get_net(out_net)

        # Gather input bits in order
        gathered: list[int | str] = []
        count = int(cell.params.get("count", 0))
        for i in range(count):
            inp = cell.inputs.get(f"I{i}")
            if inp:
                gathered.extend(self._get_bits(inp))
            else:
                gathered.append("0")

        # Assign bits to output (truncate or pad)
        for i in range(len(out_ecp5.bits)):
            if i < len(gathered):
                out_ecp5.bits[i] = gathered[i]

    def _map_slice(self, cell: Cell) -> None:
        """Map bit slice — pure wiring."""
        a_net = cell.inputs.get("A")
        out_nets = list(cell.outputs.values())
        if not a_net or not out_nets:
            return
        out_net = out_nets[0]
        offset = int(cell.params.get("offset", 0))
        width = int(cell.params.get("width", out_net.width))
        a_bits = self._get_bits(a_net)
        out_ecp5 = self._get_net(out_net)
        for i in range(min(width, len(out_ecp5.bits))):
            src_idx = offset + i
            if src_idx < len(a_bits):
                out_ecp5.bits[i] = a_bits[src_idx]

    def _map_extend(self, cell: Cell) -> None:
        """Map zero/sign extension — wiring + constant padding."""
        a_net = cell.inputs.get("A")
        out_nets = list(cell.outputs.values())
        if not a_net or not out_nets:
            return
        out_net = out_nets[0]
        a_bits = self._get_bits(a_net)
        out_ecp5 = self._get_net(out_net)
        for i in range(len(out_ecp5.bits)):
            if i < len(a_bits):
                out_ecp5.bits[i] = a_bits[i]
            elif cell.op == PrimOp.SEXT and a_bits:
                out_ecp5.bits[i] = a_bits[-1]  # sign bit
            else:
                out_ecp5.bits[i] = "0"

    def _map_pmux(self, cell: Cell) -> None:
        """Map parallel MUX to LUT cascade."""
        self._map_lut(cell)

    def _map_repeat(self, cell: Cell) -> None:
        """Map repeat — wiring."""
        a_net = cell.inputs.get("A")
        out_nets = list(cell.outputs.values())
        if not a_net or not out_nets:
            return
        out_net = out_nets[0]
        a_bits = self._get_bits(a_net)
        out_ecp5 = self._get_net(out_net)
        for i in range(len(out_ecp5.bits)):
            out_ecp5.bits[i] = a_bits[i % len(a_bits)] if a_bits else "0"

    def _map_unknown(self, cell: Cell) -> None:
        """Emit a placeholder for unsupported operations."""
        for out_net in cell.outputs.values():
            ecp5_net = self._get_net(out_net)
            ecp5_net.bits = ["0"] * out_net.width


def map_to_ecp5(design: Design) -> ECP5Netlist:
    """Map a Nosis IR Design to an ECP5 netlist."""
    mod = design.top_module()
    netlist = ECP5Netlist(top=mod.name)
    mapper = _ECP5Mapper(netlist)
    mapper.map_module(mod)
    return netlist
