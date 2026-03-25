"""Nosis cell evaluation — single source of truth for IR primitive semantics.

Every IR PrimOp has exactly one evaluation function defined here. This
module is used by:
  - passes.py (constant folding)
  - equiv.py (equivalence checking simulation)

Any change to how a PrimOp behaves must be made here and nowhere else.

Example::

    from nosis.ir import PrimOp
    from nosis.eval import eval_const_op

    # 8-bit AND
    result = eval_const_op(PrimOp.AND, {"A": 0xFF, "B": 0x0F}, {}, 8)
    assert result == 0x0F

    # MUX with sel=1 picks B
    result = eval_const_op(PrimOp.MUX, {"S": 1, "A": 10, "B": 20}, {}, 8)
    assert result == 20
"""

from __future__ import annotations

from nosis.ir import Cell, PrimOp

__all__ = [
    "UnsupportedOpError",
    "eval_cell",
    "eval_const_op",
]


class UnsupportedOpError(ValueError):
    """Raised when eval_cell encounters a PrimOp it cannot evaluate."""
    def __init__(self, op: PrimOp) -> None:
        self.op = op
        super().__init__(f"cannot evaluate PrimOp.{op.name}")


def eval_const_op(
    op: PrimOp,
    inputs: dict[str, int],
    params: dict[str, object],
    width: int,
) -> int | None:
    """Evaluate a primitive operation on constant integer inputs.

    Returns the result as a Python int masked to *width* bits,
    or ``None`` if the operation cannot be folded (e.g. FF, INPUT).
    Raises :class:`UnsupportedOpError` for unrecognized operations.
    """
    mask = (1 << width) - 1 if width > 0 else 0
    a = inputs.get("A", 0)
    b = inputs.get("B", 0)

    if op == PrimOp.CONST:
        return int(params.get("value", 0)) & mask

    # --- Unary ---
    if op == PrimOp.NOT:
        return (~a) & mask
    if op == PrimOp.REDUCE_AND:
        return 1 if (a & mask) == mask else 0
    if op == PrimOp.REDUCE_OR:
        return 1 if (a & mask) != 0 else 0
    if op == PrimOp.REDUCE_XOR:
        return bin(a & mask).count("1") & 1
    if op == PrimOp.ZEXT:
        return a & mask
    if op == PrimOp.SEXT:
        from_w = int(params.get("from_width", width))
        if from_w > 0 and (a & (1 << (from_w - 1))):
            return (a | (~((1 << from_w) - 1))) & mask
        return a & mask

    # --- Binary arithmetic ---
    if op == PrimOp.AND:
        return (a & b) & mask
    if op == PrimOp.OR:
        return (a | b) & mask
    if op == PrimOp.XOR:
        return (a ^ b) & mask
    if op == PrimOp.ADD:
        return (a + b) & mask
    if op == PrimOp.SUB:
        return (a - b) & mask
    if op == PrimOp.MUL:
        return (a * b) & mask
    if op == PrimOp.DIV:
        if b == 0:
            return 0
        if params.get("signed") and width > 0:
            sa = a if not (a & (1 << (width - 1))) else a - (1 << width)
            sb = b if not (b & (1 << (width - 1))) else b - (1 << width)
            # Truncate toward zero (SystemVerilog semantics)
            result = int(sa / sb) if sb != 0 else 0
            return result & mask
        return (a // b) & mask
    if op == PrimOp.MOD:
        if b == 0:
            return 0
        if params.get("signed") and width > 0:
            sa = a if not (a & (1 << (width - 1))) else a - (1 << width)
            sb = b if not (b & (1 << (width - 1))) else b - (1 << width)
            # Sign of result matches dividend (SystemVerilog semantics)
            result = int(sa - int(sa / sb) * sb) if sb != 0 else 0
            return result & mask
        return (a % b) & mask
    if op == PrimOp.SHL:
        return (a << (b & 0x3F)) & mask
    if op == PrimOp.SHR:
        return (a >> (b & 0x3F)) & mask
    if op == PrimOp.SSHR:
        # Arithmetic shift right: sign-extend from width
        if width > 0 and (a & (1 << (width - 1))):
            shifted = a >> (b & 0x3F)
            fill = mask & ~((1 << max(0, width - (b & 0x3F))) - 1)
            return (shifted | fill) & mask
        return (a >> (b & 0x3F)) & mask

    # --- Comparison ---
    if op == PrimOp.EQ:
        return 1 if a == b else 0
    if op == PrimOp.NE:
        return 1 if a != b else 0
    if op == PrimOp.LT:
        if params.get("signed") and width > 0:
            sa = a if not (a & (1 << (width - 1))) else a - (1 << width)
            sb = b if not (b & (1 << (width - 1))) else b - (1 << width)
            return 1 if sa < sb else 0
        return 1 if a < b else 0
    if op == PrimOp.LE:
        if params.get("signed") and width > 0:
            sa = a if not (a & (1 << (width - 1))) else a - (1 << width)
            sb = b if not (b & (1 << (width - 1))) else b - (1 << width)
            return 1 if sa <= sb else 0
        return 1 if a <= b else 0
    if op == PrimOp.GT:
        if params.get("signed") and width > 0:
            sa = a if not (a & (1 << (width - 1))) else a - (1 << width)
            sb = b if not (b & (1 << (width - 1))) else b - (1 << width)
            return 1 if sa > sb else 0
        return 1 if a > b else 0
    if op == PrimOp.GE:
        if params.get("signed") and width > 0:
            sa = a if not (a & (1 << (width - 1))) else a - (1 << width)
            sb = b if not (b & (1 << (width - 1))) else b - (1 << width)
            return 1 if sa >= sb else 0
        return 1 if a >= b else 0

    # --- MUX ---
    if op == PrimOp.MUX:
        s = inputs.get("S", 0)
        return (b if (s & 1) else a) & mask

    if op == PrimOp.PMUX:
        # Parallel MUX: A=default, S=select bits, I0..IN=case values
        # First active select bit wins (priority from I0)
        s = inputs.get("S", 0)
        count = int(params.get("count", 0))
        for i in range(count):
            if (s >> i) & 1:
                return inputs.get(f"I{i}", 0) & mask
        return a & mask  # no select active -> default

    # --- Bit manipulation ---
    if op == PrimOp.SLICE:
        offset = int(params.get("offset", 0))
        w = int(params.get("width", width))
        return (a >> offset) & ((1 << w) - 1)

    if op == PrimOp.CONCAT:
        val = 0
        shift = 0
        count = int(params.get("count", 0))
        for i in range(count):
            key = f"I{i}"
            v = inputs.get(key, 0)
            w = int(params.get(f"I{i}_width", 1))
            val |= (v & ((1 << w) - 1)) << shift
            shift += w
        return val & mask

    if op == PrimOp.REPEAT:
        n = int(params.get("count", 1))
        a_w = int(params.get("a_width", 1))
        val = 0
        for i in range(n):
            val |= (a & ((1 << a_w) - 1)) << (i * a_w)
        return val & mask

    # --- Non-foldable ---
    if op in (PrimOp.FF, PrimOp.LATCH, PrimOp.INPUT, PrimOp.OUTPUT, PrimOp.MEMORY):
        return None

    raise UnsupportedOpError(op)


def eval_cell(
    cell: Cell,
    net_values: dict[str, int],
) -> dict[str, int]:
    """Evaluate a single cell given current net values.

    Returns ``{output_port_name: value}`` for each output port.
    For non-evaluable cells (FF, INPUT, OUTPUT, MEMORY), returns
    an empty dict.

    Uses :func:`eval_const_op` as the single evaluation backend.
    """
    width = 1
    for out_net in cell.outputs.values():
        width = out_net.width
        break

    # Gather input values
    inputs: dict[str, int] = {}
    for port_name, net in cell.inputs.items():
        inputs[port_name] = net_values.get(net.name, 0)

    # Build params with concat width info without mutating cell.params
    params = cell.params
    if cell.op == PrimOp.CONCAT:
        params = dict(cell.params)
        count = int(params.get("count", 0))
        for i in range(count):
            inp = cell.inputs.get(f"I{i}")
            if inp:
                params[f"I{i}_width"] = inp.width

    result = eval_const_op(cell.op, inputs, params, width)
    if result is None:
        return {}

    output: dict[str, int] = {}
    for port_name, out_net in cell.outputs.items():
        output[port_name] = result & ((1 << out_net.width) - 1)
    return output
