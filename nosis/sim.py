"""Fast combinational simulator — pre-compiled flat evaluation for inner loops.

Replaces the generic _simulate_combinational path with a compiled
representation that avoids per-cycle dict lookups, topological sorting,
and function call overhead.

Usage::

    sim = FastSimulator(mod)
    values = sim.step({"clk": 0, "data_in": 0xFF})
    # values is a dict[str, int] of all net values
"""

from __future__ import annotations

from nosis.ir import Cell, Module, PrimOp

__all__ = ["FastSimulator"]

# Op dispatch table — maps PrimOp to a function (a, b, s, mask, params) -> int.
# Each function takes pre-extracted inputs to avoid dict lookups in the hot loop.

def _op_const(_a, _b, _s, mask, params):
    return int(params.get("value", 0)) & mask

def _op_not(a, _b, _s, mask, _p):
    return (~a) & mask

def _op_and(a, b, _s, mask, _p):
    return (a & b) & mask

def _op_or(a, b, _s, mask, _p):
    return (a | b) & mask

def _op_xor(a, b, _s, mask, _p):
    return (a ^ b) & mask

def _op_add(a, b, _s, mask, _p):
    return (a + b) & mask

def _op_sub(a, b, _s, mask, _p):
    return (a - b) & mask

def _op_mul(a, b, _s, mask, _p):
    return (a * b) & mask

def _op_div(a, b, _s, mask, params):
    if b == 0:
        return 0
    if params.get("signed"):
        w = params.get("_cmp_width", 0)
        if w > 0:
            sa = a if not (a & (1 << (w - 1))) else a - (1 << w)
            sb = b if not (b & (1 << (w - 1))) else b - (1 << w)
            return (int(sa / sb) if sb != 0 else 0) & mask
    return (a // b) & mask

def _op_mod(a, b, _s, mask, params):
    if b == 0:
        return 0
    if params.get("signed"):
        w = params.get("_cmp_width", 0)
        if w > 0:
            sa = a if not (a & (1 << (w - 1))) else a - (1 << w)
            sb = b if not (b & (1 << (w - 1))) else b - (1 << w)
            return (int(sa - int(sa / sb) * sb) if sb != 0 else 0) & mask
    return (a % b) & mask

def _op_shl(a, b, _s, mask, _p):
    return (a << (b & 0x3F)) & mask

def _op_shr(a, b, _s, mask, _p):
    return (a >> (b & 0x3F)) & mask

def _op_eq(a, b, _s, _m, _p):
    return 1 if a == b else 0

def _op_ne(a, b, _s, _m, _p):
    return 1 if a != b else 0

def _to_signed(v, mask, params):
    if not params.get("signed"):
        return v
    w = params.get("_cmp_width", 0)
    if w > 0 and (v & (1 << (w - 1))):
        return v - (1 << w)
    return v

def _op_lt(a, b, _s, mask, params):
    if params.get("signed"):
        return 1 if _to_signed(a, mask, params) < _to_signed(b, mask, params) else 0
    return 1 if a < b else 0

def _op_le(a, b, _s, mask, params):
    if params.get("signed"):
        return 1 if _to_signed(a, mask, params) <= _to_signed(b, mask, params) else 0
    return 1 if a <= b else 0

def _op_gt(a, b, _s, mask, params):
    if params.get("signed"):
        return 1 if _to_signed(a, mask, params) > _to_signed(b, mask, params) else 0
    return 1 if a > b else 0

def _op_ge(a, b, _s, mask, params):
    if params.get("signed"):
        return 1 if _to_signed(a, mask, params) >= _to_signed(b, mask, params) else 0
    return 1 if a >= b else 0

def _op_mux(a, b, s, mask, _p):
    return (b if (s & 1) else a) & mask

def _op_reduce_and(a, _b, _s, mask, _p):
    return 1 if (a & mask) == mask else 0

def _op_reduce_or(a, _b, _s, mask, _p):
    return 1 if (a & mask) != 0 else 0

def _op_reduce_xor(a, _b, _s, mask, _p):
    return bin(a & mask).count("1") & 1

def _op_zext(a, _b, _s, mask, _p):
    return a & mask

def _op_sext(a, _b, _s, mask, params):
    from_w = int(params.get("from_width", 0))
    if from_w > 0 and (a & (1 << (from_w - 1))):
        return (a | (~((1 << from_w) - 1))) & mask
    return a & mask

def _op_sshr(a, b, _s, mask, params):
    width = int(params.get("_width", 1))
    if width > 0 and (a & (1 << (width - 1))):
        shifted = a >> (b & 0x3F)
        fill = mask & ~((1 << max(0, width - (b & 0x3F))) - 1)
        return (shifted | fill) & mask
    return (a >> (b & 0x3F)) & mask

def _op_slice(a, _b, _s, _mask, params):
    offset = int(params.get("offset", 0))
    w = int(params.get("width", 1))
    return (a >> offset) & ((1 << w) - 1)


_DISPATCH: dict[PrimOp, object] = {
    PrimOp.CONST: _op_const,
    PrimOp.NOT: _op_not,
    PrimOp.AND: _op_and,
    PrimOp.OR: _op_or,
    PrimOp.XOR: _op_xor,
    PrimOp.ADD: _op_add,
    PrimOp.SUB: _op_sub,
    PrimOp.MUL: _op_mul,
    PrimOp.DIV: _op_div,
    PrimOp.MOD: _op_mod,
    PrimOp.SHL: _op_shl,
    PrimOp.SHR: _op_shr,
    PrimOp.SSHR: _op_sshr,
    PrimOp.EQ: _op_eq,
    PrimOp.NE: _op_ne,
    PrimOp.LT: _op_lt,
    PrimOp.LE: _op_le,
    PrimOp.GT: _op_gt,
    PrimOp.GE: _op_ge,
    PrimOp.MUX: _op_mux,
    PrimOp.REDUCE_AND: _op_reduce_and,
    PrimOp.REDUCE_OR: _op_reduce_or,
    PrimOp.REDUCE_XOR: _op_reduce_xor,
    PrimOp.ZEXT: _op_zext,
    PrimOp.SEXT: _op_sext,
    PrimOp.SLICE: _op_slice,
}


# Pre-compiled instruction for the simulator.
# (func, a_idx, b_idx, s_idx, out_idx, mask, params)
# Indices are into the flat value array. -1 means "use 0".
_Instruction = tuple  # (callable, int, int, int, int, int, dict)


class FastSimulator:
    """Pre-compiled combinational simulator.

    Compiles a module's combinational logic into a flat instruction list
    on construction. Each step() call evaluates all instructions in order
    using a flat int array — no dict lookups, no topological sort, no
    function dispatch overhead per cell.
    """

    __slots__ = (
        "_instructions", "_net_index", "_n_nets",
        "_input_cells", "_const_instructions",
        "_ff_d_idx", "_ff_q_idx",
        "_concat_instrs", "_repeat_instrs", "_pmux_instrs",
        "_memories",
    )

    def __init__(self, mod: Module) -> None:
        # Assign each net a flat integer index
        net_index: dict[str, int] = {}
        idx = 0
        for name in mod.nets:
            net_index[name] = idx
            idx += 1
        self._net_index = net_index
        self._n_nets = idx

        # Collect INPUT cells: (port_name, out_idx)
        self._input_cells: list[tuple[str, int]] = []
        for cell in mod.cells.values():
            if cell.op == PrimOp.INPUT:
                port_name = str(cell.params.get("port_name", ""))
                for out_net in cell.outputs.values():
                    self._input_cells.append((port_name, net_index.get(out_net.name, -1)))

        # Collect FF cells: (d_idx, q_idx)
        self._ff_d_idx: list[int] = []
        self._ff_q_idx: list[int] = []
        for cell in mod.cells.values():
            if cell.op == PrimOp.FF:
                d_net = cell.inputs.get("D")
                d_idx = net_index.get(d_net.name, -1) if d_net else -1
                for out in cell.outputs.values():
                    q_idx = net_index.get(out.name, -1)
                    self._ff_d_idx.append(d_idx)
                    self._ff_q_idx.append(q_idx)

        # Collect MEMORY cells: (depth, width, addr_indices[], rdata_indices[], waddr_idx, wdata_idx, we_idx)
        self._memories: list[dict] = []
        for cell in mod.cells.values():
            if cell.op == PrimOp.MEMORY:
                depth = int(cell.params.get("depth", 0))
                width = int(cell.params.get("width", 0))
                mem: dict = {"depth": depth, "width": width, "storage": [0] * depth,
                             "reads": [], "waddr_idx": -1, "wdata_idx": -1, "we_idx": -1}
                # Collect read ports
                for pname, pnet in cell.outputs.items():
                    if pname.startswith("RDATA"):
                        port_num = pname[5:] or "0"
                        addr_name = f"RADDR{port_num}" if port_num != "0" else "RADDR"
                        addr_net = cell.inputs.get(addr_name)
                        addr_idx = net_index.get(addr_net.name, -1) if addr_net else -1
                        rdata_idx = net_index.get(pnet.name, -1)
                        mem["reads"].append((addr_idx, rdata_idx))
                # Collect write port
                waddr = cell.inputs.get("WADDR")
                wdata = cell.inputs.get("WDATA")
                we = cell.inputs.get("WE")
                if waddr:
                    mem["waddr_idx"] = net_index.get(waddr.name, -1)
                if wdata:
                    mem["wdata_idx"] = net_index.get(wdata.name, -1)
                if we:
                    mem["we_idx"] = net_index.get(we.name, -1)
                self._memories.append(mem)

        # Topological sort — done once
        order = self._topo_sort(mod)

        # Compile instructions
        self._instructions: list[_Instruction] = []
        self._concat_instrs: list[tuple[int, list[tuple[int, int]]]] = []
        self._repeat_instrs: list[tuple[int, int, int, int]] = []
        self._pmux_instrs: list[tuple[int, int, int, list[int]]] = []

        for cell in order:
            if cell.op in (PrimOp.INPUT, PrimOp.OUTPUT, PrimOp.FF, PrimOp.LATCH, PrimOp.MEMORY):
                continue

            out_net_opt = next(iter(cell.outputs.values()), None) if cell.outputs else None
            if out_net_opt is None:
                continue
            out_net = out_net_opt
            out_idx = net_index.get(out_net.name, -1)
            width = out_net.width
            mask = (1 << width) - 1 if width > 0 else 0

            # CONCAT — special: variable number of inputs
            if cell.op == PrimOp.CONCAT:
                count = int(cell.params.get("count", 0))
                parts: list[tuple[int, int]] = []
                for i in range(count):
                    inp = cell.inputs.get(f"I{i}")
                    if inp:
                        parts.append((net_index.get(inp.name, -1), inp.width))
                    else:
                        parts.append((-1, 1))
                self._concat_instrs.append((out_idx, parts))
                continue

            # REPEAT — special
            if cell.op == PrimOp.REPEAT:
                a_net = cell.inputs.get("A")
                a_idx = net_index.get(a_net.name, -1) if a_net else -1
                n = int(cell.params.get("count", 1))
                a_w = int(cell.params.get("a_width", 1))
                self._repeat_instrs.append((out_idx, a_idx, n, a_w))
                continue

            # PMUX — special: variable select + cases
            if cell.op == PrimOp.PMUX:
                a_net = cell.inputs.get("A")
                s_net = cell.inputs.get("S")
                a_idx = net_index.get(a_net.name, -1) if a_net else -1
                s_idx = net_index.get(s_net.name, -1) if s_net else -1
                count = int(cell.params.get("count", 0))
                case_indices = []
                for i in range(count):
                    inp = cell.inputs.get(f"I{i}")
                    case_indices.append(net_index.get(inp.name, -1) if inp else -1)
                self._pmux_instrs.append((out_idx, a_idx, s_idx, case_indices))
                continue

            func = _DISPATCH.get(cell.op)
            if func is None:
                continue

            a_net = cell.inputs.get("A")
            b_net = cell.inputs.get("B")
            s_net = cell.inputs.get("S")
            a_idx = net_index.get(a_net.name, -1) if a_net else -1
            b_idx = net_index.get(b_net.name, -1) if b_net else -1
            s_idx = net_index.get(s_net.name, -1) if s_net else -1

            params = cell.params
            if cell.op == PrimOp.SSHR:
                params = dict(params)
                params["_width"] = width
            elif cell.op in (PrimOp.LT, PrimOp.LE, PrimOp.GT, PrimOp.GE):
                if cell.params.get("signed"):
                    # Pass input width for signed-to-int conversion
                    a_net_obj = cell.inputs.get("A")
                    cmp_w = a_net_obj.width if a_net_obj else width
                    params = dict(params)
                    params["_cmp_width"] = cmp_w

            self._instructions.append((func, a_idx, b_idx, s_idx, out_idx, mask, params))

    @staticmethod
    def _topo_sort(mod: Module) -> list[Cell]:
        order: list[Cell] = []
        visited: set[str] = set()

        def visit(cell: Cell) -> None:
            """Visit a single node during traversal."""
            if cell.name in visited:
                return
            visited.add(cell.name)
            for inp_net in cell.inputs.values():
                if inp_net.driver and inp_net.driver.name not in visited:
                    if inp_net.driver.op != PrimOp.FF:
                        visit(inp_net.driver)
            order.append(cell)

        for cell in mod.cells.values():
            if cell.op != PrimOp.FF:
                visit(cell)
        return order

    def step(self, input_values: dict[str, int]) -> dict[str, int]:
        """Evaluate one combinational cycle. Returns {net_name: value}."""
        vals = [0] * self._n_nets

        # Load input values
        for port_name, out_idx in self._input_cells:
            if out_idx >= 0:
                vals[out_idx] = input_values.get(port_name, 0)

        # Load any pre-set values (FF state, etc.)
        ni = self._net_index
        for name, val in input_values.items():
            idx = ni.get(name)
            if idx is not None:
                vals[idx] = val

        # Execute compiled instructions
        for func, a_idx, b_idx, s_idx, out_idx, mask, params in self._instructions:
            a = vals[a_idx] if a_idx >= 0 else 0
            b = vals[b_idx] if b_idx >= 0 else 0
            s = vals[s_idx] if s_idx >= 0 else 0
            vals[out_idx] = func(a, b, s, mask, params)

        # Execute CONCAT instructions
        for out_idx, parts in self._concat_instrs:
            val = 0
            shift = 0
            for inp_idx, w in parts:
                v = vals[inp_idx] if inp_idx >= 0 else 0
                val |= (v & ((1 << w) - 1)) << shift
                shift += w
            vals[out_idx] = val

        # Execute REPEAT instructions
        for out_idx, a_idx, n, a_w in self._repeat_instrs:
            a = vals[a_idx] if a_idx >= 0 else 0
            val = 0
            a_masked = a & ((1 << a_w) - 1)
            for i in range(n):
                val |= a_masked << (i * a_w)
            vals[out_idx] = val

        # Execute PMUX instructions
        for out_idx, a_idx, s_idx, case_indices in self._pmux_instrs:
            s = vals[s_idx] if s_idx >= 0 else 0
            result = vals[a_idx] if a_idx >= 0 else 0
            for i, ci in enumerate(case_indices):
                if (s >> i) & 1:
                    result = vals[ci] if ci >= 0 else 0
                    break
            vals[out_idx] = result

        # Execute MEMORY reads (combinational) and writes (clocked)
        for mem in self._memories:
            storage = mem["storage"]
            depth = mem["depth"]
            mask = (1 << mem["width"]) - 1 if mem["width"] > 0 else 0
            # Read ports: address -> data (combinational)
            for addr_idx, rdata_idx in mem["reads"]:
                if addr_idx >= 0 and rdata_idx >= 0:
                    addr = vals[addr_idx] % depth if depth > 0 else 0
                    vals[rdata_idx] = storage[addr] & mask
            # Write port: clocked (apply at end of cycle)
            if mem["we_idx"] >= 0 and mem["waddr_idx"] >= 0 and mem["wdata_idx"] >= 0:
                we = vals[mem["we_idx"]]
                if we:
                    waddr = vals[mem["waddr_idx"]] % depth if depth > 0 else 0
                    storage[waddr] = vals[mem["wdata_idx"]] & mask

        # Build output dict
        return {name: vals[idx] for name, idx in ni.items()}
