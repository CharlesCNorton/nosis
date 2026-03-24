"""Nosis intermediate representation — technology-independent netlist.

The IR is a flat graph of cells and nets. Each cell has a primitive
operation (PrimOp), named input ports, named output ports, and a
parameter dict. Each net has a name, a bit width, and at most one
driver cell. A Module is a collection of cells and nets with named
ports. A Design is a collection of Modules with an optional top.

Cells are not hierarchical — module instantiation is flattened during
lowering. The IR represents a single level of abstraction between
behavioral HDL and technology-mapped cells.

30 primitive operations cover combinational logic, arithmetic,
comparison, bit manipulation, sequential elements, memory, constants,
and ports. Every IR operation has well-defined semantics that can be
evaluated in Python for simulation and equivalence checking.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any


class PrimOp(Enum):
    """Primitive operations in the Nosis IR."""

    # Combinational logic
    AND = auto()
    OR = auto()
    XOR = auto()
    NOT = auto()
    MUX = auto()       # 2:1 multiplexer: sel, a, b -> out
    PMUX = auto()      # parallel mux: default, cases..., sel_bits -> out
    REDUCE_AND = auto()
    REDUCE_OR = auto()
    REDUCE_XOR = auto()

    # Arithmetic
    ADD = auto()
    SUB = auto()
    MUL = auto()
    DIV = auto()
    MOD = auto()
    SHL = auto()
    SHR = auto()
    SSHR = auto()      # arithmetic shift right

    # Comparison
    EQ = auto()
    NE = auto()
    LT = auto()
    LE = auto()
    GT = auto()
    GE = auto()

    # Bit manipulation
    CONCAT = auto()
    SLICE = auto()      # extract bits [offset +: width]
    REPEAT = auto()     # replicate N times
    ZEXT = auto()       # zero-extend
    SEXT = auto()       # sign-extend

    # Sequential
    FF = auto()         # D flip-flop: clk, d, rst, rst_val -> q
    LATCH = auto()      # level-sensitive latch (avoid, but represent if present)

    # Memory
    MEMORY = auto()     # abstract memory: addr, wdata, we, raddr -> rdata

    # Constants
    CONST = auto()      # constant value

    # Ports
    INPUT = auto()
    OUTPUT = auto()


@dataclass(slots=True)
class Net:
    """A named signal with a fixed bit width."""
    name: str
    width: int
    driver: Cell | None = None
    attributes: dict[str, str] = field(default_factory=dict)  # synthesis pragmas

    def __repr__(self) -> str:
        return f"Net({self.name!r}, w={self.width})"


@dataclass(slots=True)
class Cell:
    """A primitive cell in the netlist."""
    name: str
    op: PrimOp
    inputs: dict[str, Net] = field(default_factory=dict)
    outputs: dict[str, Net] = field(default_factory=dict)
    params: dict[str, Any] = field(default_factory=dict)
    src: str = ""  # source location (file:line) for debug tracing
    attributes: dict[str, str] = field(default_factory=dict)  # synthesis pragmas (* keep *) etc.

    def __repr__(self) -> str:
        return f"Cell({self.name!r}, {self.op.name})"


@dataclass(slots=True)
class Module:
    """A module in the Nosis IR — a flat collection of cells and nets."""
    name: str
    nets: dict[str, Net] = field(default_factory=dict)
    cells: dict[str, Cell] = field(default_factory=dict)
    ports: dict[str, Net] = field(default_factory=dict)

    def add_net(self, name: str, width: int) -> Net:
        if name in self.nets:
            raise ValueError(f"duplicate net: {name}")
        net = Net(name=name, width=width)
        self.nets[name] = net
        return net

    def add_cell(self, name: str, op: PrimOp, src: str = "", **params: Any) -> Cell:
        if name in self.cells:
            raise ValueError(f"duplicate cell: {name}")
        cell = Cell(name=name, op=op, params=params, src=src)
        self.cells[name] = cell
        return cell

    def connect(self, cell: Cell, port: str, net: Net, *, direction: str = "input") -> None:
        if direction == "input":
            cell.inputs[port] = net
        elif direction == "output":
            cell.outputs[port] = net
            net.driver = cell
        else:
            raise ValueError(f"direction must be 'input' or 'output', got {direction!r}")

    def stats(self) -> dict[str, int]:
        from collections import Counter
        op_counts = Counter(cell.op for cell in self.cells.values())
        return {
            "nets": len(self.nets),
            "cells": len(self.cells),
            "ports": len(self.ports),
            **{op.name: count for op, count in sorted(op_counts.items(), key=lambda x: x[0].name)},
        }


@dataclass(slots=True)
class Design:
    """Top-level container for one or more modules."""
    modules: dict[str, Module] = field(default_factory=dict)
    top: str | None = None

    def add_module(self, name: str) -> Module:
        if name in self.modules:
            raise ValueError(f"duplicate module: {name}")
        module = Module(name=name)
        self.modules[name] = module
        return module

    def top_module(self) -> Module:
        if self.top and self.top in self.modules:
            return self.modules[self.top]
        if len(self.modules) == 1:
            return next(iter(self.modules.values()))
        raise ValueError("design has multiple modules but no top specified")
