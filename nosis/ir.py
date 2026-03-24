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

Example::

    from nosis.ir import Design, Module, PrimOp

    mod = Module(name="example")
    a = mod.add_net("a", 8)
    b = mod.add_net("b", 8)
    y = mod.add_net("y", 8)

    inp_a = mod.add_cell("inp_a", PrimOp.INPUT, port_name="a")
    mod.connect(inp_a, "Y", a, direction="output")
    mod.ports["a"] = a

    add = mod.add_cell("add0", PrimOp.ADD)
    mod.connect(add, "A", a)
    mod.connect(add, "B", b)
    mod.connect(add, "Y", y, direction="output")

    print(mod.stats())  # {'nets': 3, 'cells': 2, 'ports': 1, 'ADD': 1, 'INPUT': 1}
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
    synthesis_warnings: list = field(default_factory=list)  # SynthesisWarning instances

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

    def eliminate_dead_modules(self) -> list[str]:
        """Remove modules not reachable from the top. Returns removed names."""
        if not self.top or self.top not in self.modules:
            return []
        live: set[str] = set()
        worklist = [self.top]
        while worklist:
            name = worklist.pop()
            if name in live:
                continue
            live.add(name)
            mod = self.modules.get(name)
            if mod is None:
                continue
            for cell in mod.cells.values():
                ref = cell.params.get("module_ref")
                if ref and ref in self.modules and ref not in live:
                    worklist.append(ref)
        removed = [n for n in list(self.modules) if n not in live]
        for n in removed:
            del self.modules[n]
        return removed


def emit_verilog(mod: Module) -> str:
    """Emit a structural Verilog representation of the IR module."""
    lines: list[str] = []
    port_dirs: dict[str, str] = {}
    port_widths: dict[str, int] = {}
    for cell in mod.cells.values():
        if cell.op == PrimOp.INPUT:
            pn = cell.params.get("port_name", "")
            if pn:
                port_dirs[pn] = "input"
                for n in cell.outputs.values():
                    port_widths[pn] = n.width
        elif cell.op == PrimOp.OUTPUT:
            pn = cell.params.get("port_name", "")
            if pn:
                port_dirs[pn] = "output"
                for n in cell.inputs.values():
                    port_widths[pn] = n.width

    port_list = sorted(port_dirs.keys())
    lines.append(f"module {mod.name} (")
    decls = []
    for pn in port_list:
        d = port_dirs[pn]
        w = port_widths.get(pn, 1)
        decls.append(f"  {d} [{w-1}:0] {pn}" if w > 1 else f"  {d} {pn}")
    lines.append(",\n".join(decls))
    lines.append(");")
    lines.append("")

    for net_name, net in sorted(mod.nets.items()):
        if net_name in port_dirs:
            continue
        esc = _verilog_id(net_name)
        lines.append(f"  wire [{net.width-1}:0] {esc};" if net.width > 1 else f"  wire {esc};")
    lines.append("")

    for cell in mod.cells.values():
        if cell.op in (PrimOp.INPUT, PrimOp.OUTPUT):
            continue
        out_nets = list(cell.outputs.values())
        if not out_nets:
            continue
        out = _verilog_id(out_nets[0].name)
        if cell.op == PrimOp.CONST:
            v = cell.params.get("value", 0)
            w = cell.params.get("width", 1)
            lines.append(f"  assign {out} = {w}'d{v};")
        elif cell.op == PrimOp.AND:
            a = _verilog_id(cell.inputs.get("A", out_nets[0]).name)
            b = _verilog_id(cell.inputs.get("B", out_nets[0]).name)
            lines.append(f"  assign {out} = {a} & {b};")
        elif cell.op == PrimOp.OR:
            a = _verilog_id(cell.inputs.get("A", out_nets[0]).name)
            b = _verilog_id(cell.inputs.get("B", out_nets[0]).name)
            lines.append(f"  assign {out} = {a} | {b};")
        elif cell.op == PrimOp.XOR:
            a = _verilog_id(cell.inputs.get("A", out_nets[0]).name)
            b = _verilog_id(cell.inputs.get("B", out_nets[0]).name)
            lines.append(f"  assign {out} = {a} ^ {b};")
        elif cell.op == PrimOp.NOT:
            a = _verilog_id(cell.inputs.get("A", out_nets[0]).name)
            lines.append(f"  assign {out} = ~{a};")
        elif cell.op == PrimOp.ADD:
            a = _verilog_id(cell.inputs.get("A", out_nets[0]).name)
            b = _verilog_id(cell.inputs.get("B", out_nets[0]).name)
            lines.append(f"  assign {out} = {a} + {b};")
        elif cell.op == PrimOp.SUB:
            a = _verilog_id(cell.inputs.get("A", out_nets[0]).name)
            b = _verilog_id(cell.inputs.get("B", out_nets[0]).name)
            lines.append(f"  assign {out} = {a} - {b};")
        elif cell.op == PrimOp.MUX:
            s = _verilog_id(cell.inputs.get("S", out_nets[0]).name)
            a = _verilog_id(cell.inputs.get("A", out_nets[0]).name)
            b = _verilog_id(cell.inputs.get("B", out_nets[0]).name)
            lines.append(f"  assign {out} = {s} ? {b} : {a};")
        elif cell.op == PrimOp.MUL:
            a = _verilog_id(cell.inputs["A"].name) if "A" in cell.inputs else "0"
            b = _verilog_id(cell.inputs["B"].name) if "B" in cell.inputs else "0"
            lines.append(f"  assign {out} = {a} * {b};")
        elif cell.op == PrimOp.DIV:
            a = _verilog_id(cell.inputs["A"].name) if "A" in cell.inputs else "0"
            b = _verilog_id(cell.inputs["B"].name) if "B" in cell.inputs else "1"
            lines.append(f"  assign {out} = {a} / {b};")
        elif cell.op == PrimOp.MOD:
            a = _verilog_id(cell.inputs["A"].name) if "A" in cell.inputs else "0"
            b = _verilog_id(cell.inputs["B"].name) if "B" in cell.inputs else "1"
            lines.append(f"  assign {out} = {a} % {b};")
        elif cell.op == PrimOp.SHL:
            a = _verilog_id(cell.inputs["A"].name) if "A" in cell.inputs else "0"
            b = _verilog_id(cell.inputs["B"].name) if "B" in cell.inputs else "0"
            lines.append(f"  assign {out} = {a} << {b};")
        elif cell.op == PrimOp.SHR:
            a = _verilog_id(cell.inputs["A"].name) if "A" in cell.inputs else "0"
            b = _verilog_id(cell.inputs["B"].name) if "B" in cell.inputs else "0"
            lines.append(f"  assign {out} = {a} >> {b};")
        elif cell.op == PrimOp.SSHR:
            a = _verilog_id(cell.inputs["A"].name) if "A" in cell.inputs else "0"
            b = _verilog_id(cell.inputs["B"].name) if "B" in cell.inputs else "0"
            lines.append(f"  assign {out} = $signed({a}) >>> {b};")
        elif cell.op == PrimOp.EQ:
            a = _verilog_id(cell.inputs["A"].name) if "A" in cell.inputs else "0"
            b = _verilog_id(cell.inputs["B"].name) if "B" in cell.inputs else "0"
            lines.append(f"  assign {out} = ({a} == {b});")
        elif cell.op == PrimOp.NE:
            a = _verilog_id(cell.inputs["A"].name) if "A" in cell.inputs else "0"
            b = _verilog_id(cell.inputs["B"].name) if "B" in cell.inputs else "0"
            lines.append(f"  assign {out} = ({a} != {b});")
        elif cell.op == PrimOp.LT:
            a = _verilog_id(cell.inputs["A"].name) if "A" in cell.inputs else "0"
            b = _verilog_id(cell.inputs["B"].name) if "B" in cell.inputs else "0"
            lines.append(f"  assign {out} = ({a} < {b});")
        elif cell.op == PrimOp.LE:
            a = _verilog_id(cell.inputs["A"].name) if "A" in cell.inputs else "0"
            b = _verilog_id(cell.inputs["B"].name) if "B" in cell.inputs else "0"
            lines.append(f"  assign {out} = ({a} <= {b});")
        elif cell.op == PrimOp.GT:
            a = _verilog_id(cell.inputs["A"].name) if "A" in cell.inputs else "0"
            b = _verilog_id(cell.inputs["B"].name) if "B" in cell.inputs else "0"
            lines.append(f"  assign {out} = ({a} > {b});")
        elif cell.op == PrimOp.GE:
            a = _verilog_id(cell.inputs["A"].name) if "A" in cell.inputs else "0"
            b = _verilog_id(cell.inputs["B"].name) if "B" in cell.inputs else "0"
            lines.append(f"  assign {out} = ({a} >= {b});")
        elif cell.op == PrimOp.REDUCE_AND:
            a = _verilog_id(cell.inputs["A"].name) if "A" in cell.inputs else "0"
            lines.append(f"  assign {out} = &{a};")
        elif cell.op == PrimOp.REDUCE_OR:
            a = _verilog_id(cell.inputs["A"].name) if "A" in cell.inputs else "0"
            lines.append(f"  assign {out} = |{a};")
        elif cell.op == PrimOp.REDUCE_XOR:
            a = _verilog_id(cell.inputs["A"].name) if "A" in cell.inputs else "0"
            lines.append(f"  assign {out} = ^{a};")
        elif cell.op == PrimOp.CONCAT:
            parts = []
            count = int(cell.params.get("count", 0))
            for ci in range(count - 1, -1, -1):
                inp = cell.inputs.get(f"I{ci}")
                if inp:
                    parts.append(_verilog_id(inp.name))
            lines.append(f"  assign {out} = {{{', '.join(parts)}}};") if parts else None
        elif cell.op == PrimOp.SLICE:
            a = _verilog_id(cell.inputs["A"].name) if "A" in cell.inputs else "0"
            offset = cell.params.get("offset", 0)
            w = cell.params.get("width", 1)
            lines.append(f"  assign {out} = {a}[{offset + w - 1}:{offset}];")
        elif cell.op == PrimOp.ZEXT:
            a = _verilog_id(cell.inputs["A"].name) if "A" in cell.inputs else "0"
            lines.append(f"  assign {out} = {{{{0}}, {a}}};")
        elif cell.op == PrimOp.SEXT:
            a = _verilog_id(cell.inputs["A"].name) if "A" in cell.inputs else "0"
            lines.append(f"  assign {out} = $signed({a});")
        elif cell.op == PrimOp.REPEAT:
            a = _verilog_id(cell.inputs["A"].name) if "A" in cell.inputs else "0"
            n = cell.params.get("count", 1)
            lines.append(f"  assign {out} = {{{n}{{{a}}}}};")
        elif cell.op == PrimOp.PMUX:
            lines.append(f"  // PMUX: {cell.name} (case statement)")
        elif cell.op == PrimOp.LATCH:
            d = _verilog_id(cell.inputs["D"].name) if "D" in cell.inputs else "0"
            en = _verilog_id(cell.inputs.get("EN", cell.inputs.get("CLK", list(cell.inputs.values())[0] if cell.inputs else out_nets[0])).name)
            lines.append(f"  always @(*) if ({en}) {out} = {d};")
        elif cell.op == PrimOp.MEMORY:
            lines.append(f"  // MEMORY: {cell.name} (depth={cell.params.get('depth', 0)}, width={cell.params.get('width', 0)})")
        elif cell.op == PrimOp.FF:
            d = _verilog_id(cell.inputs["D"].name) if "D" in cell.inputs else "0"
            clk = _verilog_id(cell.inputs["CLK"].name) if "CLK" in cell.inputs else "clk"
            lines.append(f"  always @(posedge {clk}) {out} <= {d};")
        else:
            lines.append(f"  // {cell.op.name}: {cell.name}")

    lines.append("")
    lines.append("endmodule")
    return "\n".join(lines)


def _verilog_id(name: str) -> str:
    """Escape a net name for Verilog output."""
    if name.startswith("$") or "." in name or " " in name:
        return "\\" + name + " "
    return name
