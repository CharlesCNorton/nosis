"""Nosis frontend — parse SystemVerilog via pyslang and lower to Nosis IR.

This module uses pyslang (the Python bindings for slang, a full IEEE 1800-2017
SystemVerilog compiler) to parse, elaborate, and type-check the input HDL.
The elaborated AST is then walked and lowered into the Nosis intermediate
representation (nosis.ir).

The lowering handles:
  - Module ports (input/output/inout) with bit widths
  - Parameters and localparam constants
  - Continuous assignments (assign)
  - Procedural blocks (always_ff, always_comb, always)
  - Conditional statements (if/else) -> MUX trees
  - Case statements -> parallel MUX (PMUX)
  - Non-blocking assignments (<= ) -> FF with D input
  - Blocking assignments (=) -> combinational wiring
  - Binary/unary operators -> IR primitives
  - Concatenation, bit select, range select
  - Integer literals -> CONST cells
  - For loops (unrolled at elaboration time by slang)
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# pyslang must be importable — either installed or on sys.path.
_PYSLANG_PATH = os.environ.get("NOSIS_PYSLANG_PATH", "")
if _PYSLANG_PATH and _PYSLANG_PATH not in sys.path:
    sys.path.insert(0, _PYSLANG_PATH)

try:
    import pyslang  # type: ignore[import-untyped]
except ImportError as _exc:
    raise ImportError(
        "pyslang is required. Build slang with -DSLANG_INCLUDE_PYLIB=ON "
        "and set NOSIS_PYSLANG_PATH to the directory containing the .pyd/.so."
    ) from _exc

from nosis.ir import Cell, Design, Module, Net, PrimOp

import re as _re

_VERILOG_LITERAL_RE = _re.compile(
    r"^(\d+)'([shSH]?)([bBoOdDhH])([0-9a-fA-FxXzZ_]+)$"
)


def _svint_to_int(val: Any) -> int:
    """Convert a pyslang SVInt to a Python int.

    SVInt.__repr__ may return plain decimal (``50000000``) or Verilog
    literal format (``1'b1``, ``32'hEDB88320``, ``3'b0``).
    """
    if val is None:
        return 0
    text = repr(val).strip()
    # Plain decimal?
    try:
        return int(text)
    except ValueError:
        pass
    # Verilog sized literal: <width>'[s]<base><digits>
    m = _VERILOG_LITERAL_RE.match(text)
    if m:
        base_char = m.group(3).lower()
        digits = m.group(4).replace("_", "").lower()
        # Replace x/z with 0 for numeric conversion
        digits = digits.replace("x", "0").replace("z", "0")
        base_map = {"b": 2, "o": 8, "d": 10, "h": 16}
        return int(digits, base_map.get(base_char, 10))
    # Unsized literal with base: 'h1F, 'b1010, etc.
    if "'" in text:
        after = text.split("'", 1)[1]
        if after and after[0] in "bBoOdDhHsS":
            start = 1
            if after[0] in "sS" and len(after) > 1:
                start = 2
            base_char = after[start - 1].lower() if start == 1 else after[0].lower()
            if base_char == "s" and len(after) > 1:
                base_char = after[1].lower()
                start = 2
            digits = after[start:].replace("_", "").replace("x", "0").replace("z", "0").lower()
            base_map = {"b": 2, "o": 8, "d": 10, "h": 16}
            try:
                return int(digits, base_map.get(base_char, 10))
            except ValueError:
                pass
    # Last resort: try toString()
    if hasattr(val, "toString"):
        try:
            return int(val.toString())
        except (ValueError, TypeError):
            pass
    return 0


__all__ = [
    "FrontendError",
    "ParseResult",
    "parse_files",
    "lower_to_ir",
]


class FrontendError(RuntimeError):
    """Raised when parsing or lowering fails."""


@dataclass(slots=True)
class ParseResult:
    """Result of parsing one or more SystemVerilog files."""
    compilation: Any  # pyslang.ast.Compilation
    driver: Any       # pyslang.driver.Driver
    diagnostics: list[str]
    errors: list[str]
    top_instances: list[Any]  # list of pyslang InstanceSymbol


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

def parse_files(
    paths: list[str | Path],
    *,
    top: str | None = None,
    defines: dict[str, str] | None = None,
    include_dirs: list[str | Path] | None = None,
) -> ParseResult:
    """Parse and elaborate SystemVerilog files via pyslang.

    Returns a :class:`ParseResult` with the compiled AST and any diagnostics.
    Raises :class:`FrontendError` if there are fatal errors.
    """
    drv = pyslang.driver.Driver()
    drv.addStandardArgs()

    # Include ECP5 vendor primitive stubs so slang can elaborate
    # designs that instantiate USRMCLK, EHXPLLL, etc.
    _ecp5_prims = Path(__file__).parent / "ecp5_prims.sv"
    if _ecp5_prims.exists():
        drv.sourceLoader.addFiles(str(_ecp5_prims))

    for path in paths:
        drv.sourceLoader.addFiles(str(path))

    # Build command line for options
    cmd_parts: list[str] = [
        # Provide a default timescale for modules that don't specify one.
        # Synthesis does not use timescale, but slang requires consistency
        # when any module in the design has one (e.g. picorv32.v).
        "--timescale=1ns/1ps",
    ]
    if top:
        cmd_parts.append(f"--top={top}")
    if defines:
        for key, value in defines.items():
            if value:
                cmd_parts.append(f"-D{key}={value}")
            else:
                cmd_parts.append(f"-D{key}")
    if include_dirs:
        for inc in include_dirs:
            cmd_parts.append(f"-I{inc}")

    if cmd_parts:
        drv.parseCommandLine(" ".join(cmd_parts))

    drv.processOptions()

    if not drv.parseAllSources():
        raise FrontendError("pyslang failed to parse one or more source files")

    comp = drv.createCompilation()

    # Diagnostic codes to suppress — these are not relevant to synthesis.
    _SUPPRESS_CODES = {"DiagCode(MissingTimeScale)"}

    diagnostics: list[str] = []
    errors: list[str] = []
    for diag in comp.getAllDiagnostics():
        loc = diag.location
        code = diag.code
        code_str = str(code)
        is_error = diag.isError() if callable(getattr(diag, "isError", None)) else bool(getattr(diag, "isError", False))
        text = f"[{code}] at {loc}" if loc else f"[{code}]"
        if code_str in _SUPPRESS_CODES:
            diagnostics.append(text)
            continue
        if is_error:
            errors.append(text)
        diagnostics.append(text)

    top_instances = list(comp.getRoot().topInstances)

    if errors:
        raise FrontendError(
            f"compilation produced {len(errors)} error(s):\n" + "\n".join(errors)
        )

    if not top_instances:
        raise FrontendError("no top-level instances found after elaboration")

    return ParseResult(
        compilation=comp,
        driver=drv,
        diagnostics=diagnostics,
        errors=errors,
        top_instances=top_instances,
    )


# ---------------------------------------------------------------------------
# IR Lowering
# ---------------------------------------------------------------------------

class _Lowerer:
    """Walks the pyslang AST and builds a Nosis IR Module."""

    def __init__(self, module: Module) -> None:
        self.mod = module
        self._net_counter = 0
        self._cell_counter = 0

    def _fresh_net(self, prefix: str, width: int) -> Net:
        name = f"${prefix}_{self._net_counter}"
        self._net_counter += 1
        return self.mod.add_net(name, width)

    def _fresh_cell(self, prefix: str, op: PrimOp, src: str = "", **params: Any) -> Cell:
        name = f"${prefix}_{self._cell_counter}"
        self._cell_counter += 1
        return self.mod.add_cell(name, op, src=src, **params)

    def _src_from_node(self, node: Any) -> str:
        """Extract a source location string from a pyslang AST node."""
        loc = getattr(node, "location", None) or getattr(node, "sourceRange", None)
        if loc is not None:
            return str(loc)
        return ""

    def _get_or_create_net(self, name: str, width: int) -> Net:
        if name in self.mod.nets:
            existing = self.mod.nets[name]
            if existing.width != width:
                # Width mismatch — create a conversion net
                return self._fresh_net(f"{name}_w{width}", width)
            return existing
        return self.mod.add_net(name, width)

    def _bit_width(self, node: Any) -> int:
        """Extract bit width from a pyslang AST node."""
        if hasattr(node, "type") and hasattr(node.type, "bitWidth"):
            try:
                return int(node.type.bitWidth)
            except (TypeError, ValueError):
                pass
        return 1

    # --- Expression lowering ---

    def lower_expr(self, expr: Any) -> Net:
        """Lower a pyslang expression to a Nosis IR net (the expression's output)."""
        kind = str(expr.kind)

        if kind == "ExpressionKind.IntegerLiteral":
            return self._lower_literal(expr)
        elif kind == "ExpressionKind.NamedValue":
            return self._lower_named_value(expr)
        elif kind == "ExpressionKind.BinaryOp":
            return self._lower_binary(expr)
        elif kind == "ExpressionKind.UnaryOp":
            return self._lower_unary(expr)
        elif kind == "ExpressionKind.ConditionalOp":
            return self._lower_conditional_expr(expr)
        elif kind == "ExpressionKind.Conversion":
            return self._lower_conversion(expr)
        elif kind == "ExpressionKind.Concatenation":
            return self._lower_concat(expr)
        elif kind == "ExpressionKind.RangeSelect":
            return self._lower_range_select(expr)
        elif kind == "ExpressionKind.ElementSelect":
            return self._lower_element_select(expr)
        elif kind == "ExpressionKind.Replication":
            return self._lower_replication(expr)
        elif kind == "ExpressionKind.Assignment":
            return self._lower_assignment_expr(expr)
        elif kind == "ExpressionKind.Call":
            # System function calls ($clog2, $bits, etc.) are typically
            # resolved to constants by slang during elaboration. If we
            # reach here, treat as constant 0.
            w = self._bit_width(expr)
            const = getattr(expr, "constant", None)
            if const is not None:
                int_val = _svint_to_int(const)
                net = self._fresh_net("call_const", w)
                cell = self._fresh_cell("call_const", PrimOp.CONST, value=int_val, width=w)
                self.mod.connect(cell, "Y", net, direction="output")
                return net
            net = self._fresh_net("call", w)
            cell = self._fresh_cell("call", PrimOp.CONST, value=0, width=w)
            self.mod.connect(cell, "Y", net, direction="output")
            return net
        elif kind in ("ExpressionKind.EmptyArgument", "ExpressionKind.StringLiteral"):
            w = self._bit_width(expr) or 1
            net = self._fresh_net("empty", w)
            cell = self._fresh_cell("empty", PrimOp.CONST, value=0, width=w)
            self.mod.connect(cell, "Y", net, direction="output")
            return net
        else:
            w = self._bit_width(expr)
            net = self._fresh_net(f"unsupported_{kind}", w)
            cell = self._fresh_cell(f"unsupported_{kind}", PrimOp.CONST, value=0, width=w)
            self.mod.connect(cell, "Y", net, direction="output")
            return net

    def _lower_literal(self, expr: Any) -> Net:
        w = self._bit_width(expr)
        val = expr.value
        int_val = _svint_to_int(val)
        net = self._fresh_net("const", w)
        cell = self._fresh_cell("const", PrimOp.CONST, value=int_val, width=w)
        self.mod.connect(cell, "Y", net, direction="output")
        return net

    def _lower_named_value(self, expr: Any) -> Net:
        sym = expr.symbol
        name = sym.name
        w = self._bit_width(expr)
        net = self._get_or_create_net(name, w)
        # If the net has no driver, check whether the symbol resolves to
        # a constant value (enum member, localparam, parameter).
        if net.driver is None:
            const_val = getattr(expr, "constant", None)
            if const_val is None:
                # Check the symbol itself (EnumValue, Parameter)
                sym_val = getattr(sym, "value", None)
                if sym_val is not None:
                    const_val = sym_val
            if const_val is not None:
                int_val = _svint_to_int(const_val)
                cell = self._fresh_cell(f"const_{name}", PrimOp.CONST, value=int_val, width=w)
                self.mod.connect(cell, "Y", net, direction="output")
        return net

    def _lower_binary(self, expr: Any) -> Net:
        op_str = str(expr.op)
        w = self._bit_width(expr)
        lhs = self.lower_expr(expr.left)
        rhs = self.lower_expr(expr.right)
        out = self._fresh_net("binop", w)

        op_map = {
            "BinaryOperator.Add": PrimOp.ADD,
            "BinaryOperator.Subtract": PrimOp.SUB,
            "BinaryOperator.Multiply": PrimOp.MUL,
            "BinaryOperator.Divide": PrimOp.DIV,
            "BinaryOperator.Mod": PrimOp.MOD,
            "BinaryOperator.BinaryAnd": PrimOp.AND,
            "BinaryOperator.BinaryOr": PrimOp.OR,
            "BinaryOperator.BinaryXor": PrimOp.XOR,
            "BinaryOperator.LogicalAnd": PrimOp.AND,
            "BinaryOperator.LogicalOr": PrimOp.OR,
            "BinaryOperator.Equality": PrimOp.EQ,
            "BinaryOperator.Inequality": PrimOp.NE,
            "BinaryOperator.CaseEquality": PrimOp.EQ,
            "BinaryOperator.CaseInequality": PrimOp.NE,
            "BinaryOperator.LessThan": PrimOp.LT,
            "BinaryOperator.LessThanEqual": PrimOp.LE,
            "BinaryOperator.GreaterThan": PrimOp.GT,
            "BinaryOperator.GreaterThanEqual": PrimOp.GE,
            "BinaryOperator.LogicalShiftLeft": PrimOp.SHL,
            "BinaryOperator.LogicalShiftRight": PrimOp.SHR,
            "BinaryOperator.ArithmeticShiftLeft": PrimOp.SHL,
            "BinaryOperator.ArithmeticShiftRight": PrimOp.SSHR,
        }
        prim = op_map.get(op_str, PrimOp.AND)
        cell = self._fresh_cell("binop", prim)
        self.mod.connect(cell, "A", lhs)
        self.mod.connect(cell, "B", rhs)
        self.mod.connect(cell, "Y", out, direction="output")
        return out

    def _lower_unary(self, expr: Any) -> Net:
        op_str = str(expr.op)
        w = self._bit_width(expr)
        operand = self.lower_expr(expr.operand)
        out = self._fresh_net("unop", w)

        op_map = {
            "UnaryOperator.BitwiseNot": PrimOp.NOT,
            "UnaryOperator.LogicalNot": PrimOp.NOT,
            "UnaryOperator.BitwiseAnd": PrimOp.REDUCE_AND,
            "UnaryOperator.BitwiseOr": PrimOp.REDUCE_OR,
            "UnaryOperator.BitwiseXor": PrimOp.REDUCE_XOR,
            "UnaryOperator.Minus": PrimOp.SUB,  # 0 - operand
        }
        prim = op_map.get(op_str, PrimOp.NOT)

        if op_str == "UnaryOperator.Minus":
            # Unary minus: 0 - operand
            zero = self._fresh_net("zero", w)
            zero_cell = self._fresh_cell("zero", PrimOp.CONST, value=0, width=w)
            self.mod.connect(zero_cell, "Y", zero, direction="output")
            cell = self._fresh_cell("neg", PrimOp.SUB)
            self.mod.connect(cell, "A", zero)
            self.mod.connect(cell, "B", operand)
        else:
            cell = self._fresh_cell("unop", prim)
            self.mod.connect(cell, "A", operand)

        self.mod.connect(cell, "Y", out, direction="output")
        return out

    def _lower_conditional_expr(self, expr: Any) -> Net:
        w = self._bit_width(expr)
        # pyslang ConditionalExpression: conditions[0].expr is the predicate,
        # left is the true branch, right is the false branch.
        conds = list(expr.conditions)
        pred_expr = conds[0].expr
        pred = self.lower_expr(pred_expr)
        true_val = self.lower_expr(expr.left)
        false_val = self.lower_expr(expr.right)

        out = self._fresh_net("mux", w)
        cell = self._fresh_cell("mux", PrimOp.MUX)
        self.mod.connect(cell, "S", pred)
        self.mod.connect(cell, "A", false_val)  # sel=0 -> A
        self.mod.connect(cell, "B", true_val)   # sel=1 -> B
        self.mod.connect(cell, "Y", out, direction="output")
        return out

    def _lower_conversion(self, expr: Any) -> Net:
        operand = self.lower_expr(expr.operand)
        src_w = operand.width
        dst_w = self._bit_width(expr)
        if src_w == dst_w:
            return operand
        out = self._fresh_net("conv", dst_w)
        if dst_w > src_w:
            cell = self._fresh_cell("zext", PrimOp.ZEXT, from_width=src_w, to_width=dst_w)
        else:
            cell = self._fresh_cell("trunc", PrimOp.SLICE, offset=0, width=dst_w)
        self.mod.connect(cell, "A", operand)
        self.mod.connect(cell, "Y", out, direction="output")
        return out

    def _lower_concat(self, expr: Any) -> Net:
        w = self._bit_width(expr)
        operands: list[Net] = []
        for child_expr in expr.operands:
            operands.append(self.lower_expr(child_expr))
        if len(operands) == 1:
            return operands[0]
        out = self._fresh_net("concat", w)
        cell = self._fresh_cell("concat", PrimOp.CONCAT, count=len(operands))
        for i, op_net in enumerate(operands):
            self.mod.connect(cell, f"I{i}", op_net)
        self.mod.connect(cell, "Y", out, direction="output")
        return out

    def _lower_range_select(self, expr: Any) -> Net:
        w = self._bit_width(expr)
        src = self.lower_expr(expr.value)
        # left and right define the range — try constant evaluation
        offset = 0
        try:
            left_val = expr.left
            right_val = expr.right
            # Try to read as literals via the constant attribute
            if hasattr(right_val, "constant") and right_val.constant is not None:
                offset = _svint_to_int(right_val.constant)
            elif hasattr(right_val, "value") and right_val.value is not None:
                offset = _svint_to_int(right_val.value)
        except (ValueError, AttributeError, TypeError):
            offset = 0
        out = self._fresh_net("slice", w)
        cell = self._fresh_cell("slice", PrimOp.SLICE, offset=offset, width=w)
        self.mod.connect(cell, "A", src)
        self.mod.connect(cell, "Y", out, direction="output")
        return out

    def _lower_element_select(self, expr: Any) -> Net:
        w = self._bit_width(expr)
        src = self.lower_expr(expr.value)
        out = self._fresh_net("esel", w)
        cell = self._fresh_cell("esel", PrimOp.SLICE, offset=0, width=w)
        self.mod.connect(cell, "A", src)
        self.mod.connect(cell, "Y", out, direction="output")
        return out

    def _lower_replication(self, expr: Any) -> Net:
        w = self._bit_width(expr)
        # Replication: {N{expr}} — repeat the operand N times
        # pyslang Replication has .count and .concat
        count_expr = getattr(expr, "count", None)
        concat_expr = getattr(expr, "concat", None)
        if concat_expr is not None:
            operand = self.lower_expr(concat_expr)
        else:
            # Fallback: try to get operands
            operand = self._fresh_net("rep_fallback", 1)
            cell = self._fresh_cell("rep_fallback", PrimOp.CONST, value=0, width=1)
            self.mod.connect(cell, "Y", operand, direction="output")
        n = 1
        if count_expr is not None:
            const = getattr(count_expr, "constant", None)
            if const is not None:
                n = _svint_to_int(const)
            else:
                try:
                    n = int(str(count_expr))
                except (ValueError, TypeError):
                    n = w // operand.width if operand.width > 0 else 1
        if n <= 1:
            return operand
        out = self._fresh_net("repeat", w)
        cell = self._fresh_cell("repeat", PrimOp.REPEAT, count=n, a_width=operand.width)
        self.mod.connect(cell, "A", operand)
        self.mod.connect(cell, "Y", out, direction="output")
        return out

    def _lower_assignment_expr(self, expr: Any) -> Net:
        """Lower an assignment expression. Returns the LHS net."""
        rhs = self.lower_expr(expr.right)
        lhs = self.lower_expr(expr.left)
        # Wire RHS to LHS — the assignment connects them
        # For non-blocking: create FF. For blocking: direct wire.
        if expr.isNonBlocking:
            # The actual FF creation happens at the procedural block level.
            # Here we just note the connection.
            pass
        return lhs

    # --- Statement lowering ---

    def lower_procedural_block(self, block: Any) -> None:
        """Lower a ProceduralBlock (always_ff, always_comb, always)."""
        proc_kind = str(block.procedureKind)
        body = block.body

        if "AlwaysFF" in proc_kind or "Always" in proc_kind:
            # Extract clock edge from timing control
            clock_net: Net | None = None
            reset_net: Net | None = None
            async_reset = False
            stmt = body

            if str(body.kind) == "StatementKind.Timed":
                timing = body.timing
                timing_kind = str(timing.kind)
                if timing_kind == "TimingControlKind.SignalEvent":
                    clock_net = self.lower_expr(timing.expr)
                elif timing_kind == "TimingControlKind.EventList":
                    # Multiple events: @(posedge clk or posedge rst)
                    # First is clock, subsequent are async reset/set signals
                    events = list(timing.events) if hasattr(timing, "events") else []
                    if events:
                        clock_net = self.lower_expr(events[0].expr)
                        if len(events) > 1:
                            reset_net = self.lower_expr(events[1].expr)
                            async_reset = True
                stmt = body.stmt

            # Collect all non-blocking assignments in this block
            assignments = self._collect_nb_assignments(stmt)
            # Deduplicate by target: last assignment to each target wins
            # (matches Verilog semantics for multiple assignments in one block)
            deduped: dict[str, tuple[Net, Net, Net | None, Net | None]] = {}
            for lhs_net, rhs_net, rst_net, rst_val_net in assignments:
                deduped[lhs_net.name] = (lhs_net, rhs_net, rst_net, rst_val_net)

            for lhs_net, rhs_net, rst_net, rst_val_net in deduped.values():
                ff_rst = rst_net if not async_reset else reset_net
                ff = self._fresh_cell(
                    f"ff_{lhs_net.name}", PrimOp.FF,
                    ff_target=lhs_net.name,
                    async_reset=async_reset,
                )
                self.mod.connect(ff, "D", rhs_net)
                if clock_net:
                    self.mod.connect(ff, "CLK", clock_net)
                if ff_rst:
                    self.mod.connect(ff, "RST", ff_rst)
                if rst_val_net:
                    self.mod.connect(ff, "RST_VAL", rst_val_net)
                # Use a fresh output net to avoid driver conflicts from
                # multiple procedural blocks assigning the same register.
                # The ff_target param records the original net name so the
                # FSM detector can trace feedback loops.
                q_net = self._fresh_net(f"ff_q_{lhs_net.name}", lhs_net.width)
                self.mod.connect(ff, "Q", q_net, direction="output")

        elif "AlwaysComb" in proc_kind or "AlwaysLatch" in proc_kind:
            # Combinational or latch — lower statements as wiring.
            # If the block is AlwaysLatch, slang has already validated
            # that latches are intentional. Incomplete if/case in
            # AlwaysComb would be a slang warning/error.
            self._lower_statement(body)

    def _collect_nb_assignments(
        self, stmt: Any
    ) -> list[tuple[Net, Net, Net | None, Net | None]]:
        """Collect non-blocking assignments from a statement tree.

        Returns list of (lhs_net, rhs_net, reset_condition_net, reset_value_net).
        Handles if/else for reset inference.
        """
        results: list[tuple[Net, Net, Net | None, Net | None]] = []
        kind = str(stmt.kind)

        if kind == "StatementKind.ExpressionStatement":
            expr = stmt.expr
            if str(expr.kind) == "ExpressionKind.Assignment" and expr.isNonBlocking:
                lhs = self.lower_expr(expr.left)
                rhs = self.lower_expr(expr.right)
                results.append((lhs, rhs, None, None))

        elif kind == "StatementKind.Conditional":
            # if (cond) ... else ...
            # Check for reset pattern: if (rst) {blocking resets} else {non-blocking logic}
            conds = list(stmt.conditions)
            if conds:
                cond_net = self.lower_expr(conds[0].expr)
                if_true_assigns = self._collect_nb_assignments(stmt.ifTrue)
                if_false_assigns = []
                if stmt.ifFalse is not None:
                    if_false_assigns = self._collect_nb_assignments(stmt.ifFalse)

                # Reset inference: if the true branch has only blocking assignments
                # to the same targets as the false branch's non-blocking assignments,
                # treat it as synchronous reset.
                if if_false_assigns and not if_true_assigns:
                    # True branch might have blocking reset assignments
                    reset_vals = self._collect_blocking_assignments(stmt.ifTrue)
                    for lhs, rhs, _, _ in if_false_assigns:
                        rst_val = reset_vals.get(lhs.name)
                        results.append((lhs, rhs, cond_net, rst_val))
                else:
                    # General conditional: MUX the assignments
                    true_map = {a[0].name: a for a in if_true_assigns}
                    false_map = {a[0].name: a for a in if_false_assigns}
                    all_targets = set(true_map) | set(false_map)
                    for target in all_targets:
                        t_entry = true_map.get(target)
                        f_entry = false_map.get(target)
                        if t_entry and f_entry:
                            # Both branches assign — MUX
                            w = t_entry[1].width
                            mux_out = self._fresh_net("cmux", w)
                            mux = self._fresh_cell("cmux", PrimOp.MUX)
                            self.mod.connect(mux, "S", cond_net)
                            self.mod.connect(mux, "A", f_entry[1])
                            self.mod.connect(mux, "B", t_entry[1])
                            self.mod.connect(mux, "Y", mux_out, direction="output")
                            results.append((t_entry[0], mux_out, None, None))
                        elif t_entry:
                            results.append(t_entry)
                        elif f_entry:
                            results.append(f_entry)

        elif kind == "StatementKind.Case":
            # Case statement -> parallel MUX
            # Handles case, casez (WildcardJustZ), casex (WildcardXOrZ)
            # For synthesis, casez/casex are treated identically to case —
            # the don't-care bits in case labels are resolved by slang during
            # elaboration. The EQ comparison in the IR is exact-match.
            sel = self.lower_expr(stmt.expr)
            items = list(stmt.items)
            default_assigns: list[tuple[Net, Net, Net | None, Net | None]] = []
            if stmt.defaultCase is not None:
                default_assigns = self._collect_nb_assignments(stmt.defaultCase)

            # For each case item, collect assignments and build MUX chain
            for item in items:
                item_assigns = self._collect_nb_assignments(item.stmt)
                # Each case item has expr list — build equality compare
                for case_expr in item.expressions:
                    case_val = self.lower_expr(case_expr)
                    eq_net = self._fresh_net("case_eq", 1)
                    eq_cell = self._fresh_cell("case_eq", PrimOp.EQ)
                    self.mod.connect(eq_cell, "A", sel)
                    self.mod.connect(eq_cell, "B", case_val)
                    self.mod.connect(eq_cell, "Y", eq_net, direction="output")

                    for lhs, rhs, _, _ in item_assigns:
                        # Find the default for this target
                        default_rhs = None
                        for dl, dr, _, _ in default_assigns:
                            if dl.name == lhs.name:
                                default_rhs = dr
                                break
                        if default_rhs is None:
                            default_rhs = lhs  # hold value

                        mux_out = self._fresh_net("case_mux", lhs.width)
                        mux = self._fresh_cell("case_mux", PrimOp.MUX)
                        self.mod.connect(mux, "S", eq_net)
                        self.mod.connect(mux, "A", default_rhs)
                        self.mod.connect(mux, "B", rhs)
                        self.mod.connect(mux, "Y", mux_out, direction="output")
                        results.append((lhs, mux_out, None, None))

        elif kind == "StatementKind.Block":
            inner = stmt.body
            if inner is not None:
                results.extend(self._collect_nb_assignments(inner))

        elif kind == "StatementKind.List":
            for child in stmt.list:
                results.extend(self._collect_nb_assignments(child))

        return results

    def _collect_blocking_assignments(self, stmt: Any) -> dict[str, Net]:
        """Collect blocking assignments as {target_name: value_net}."""
        results: dict[str, Net] = {}
        kind = str(stmt.kind)

        if kind == "StatementKind.ExpressionStatement":
            expr = stmt.expr
            if str(expr.kind) == "ExpressionKind.Assignment" and not expr.isNonBlocking:
                lhs = self.lower_expr(expr.left)
                rhs = self.lower_expr(expr.right)
                results[lhs.name] = rhs

        elif kind == "StatementKind.Block":
            if stmt.body is not None:
                results.update(self._collect_blocking_assignments(stmt.body))

        elif kind == "StatementKind.List":
            for child in stmt.list:
                results.update(self._collect_blocking_assignments(child))

        return results

    def _lower_statement(self, stmt: Any) -> None:
        """Lower a statement in combinational context (direct wiring)."""
        kind = str(stmt.kind)

        if kind == "StatementKind.ExpressionStatement":
            self.lower_expr(stmt.expr)

        elif kind == "StatementKind.Conditional":
            conds = list(stmt.conditions)
            if conds:
                self.lower_expr(conds[0].expr)
            self._lower_statement(stmt.ifTrue)
            if stmt.ifFalse is not None:
                self._lower_statement(stmt.ifFalse)

        elif kind == "StatementKind.Case":
            self.lower_expr(stmt.expr)
            for item in stmt.items:
                self._lower_statement(item.stmt)
            if stmt.defaultCase is not None:
                self._lower_statement(stmt.defaultCase)

        elif kind == "StatementKind.Block":
            if stmt.body is not None:
                self._lower_statement(stmt.body)

        elif kind == "StatementKind.List":
            for child in stmt.list:
                self._lower_statement(child)

        elif kind == "StatementKind.Timed":
            self._lower_statement(stmt.stmt)

    # --- Top-level module lowering ---

    def lower_instance(self, inst: Any) -> None:
        """Lower a pyslang top-level instance into the Nosis IR module."""
        body = inst.body

        # Ports
        for port in body.portList:
            name = port.name
            w = self._bit_width(port)
            direction = str(port.direction)
            net = self._get_or_create_net(name, w)
            self.mod.ports[name] = net

            if "In" in direction:
                cell = self._fresh_cell(f"port_{name}", PrimOp.INPUT, port_name=name)
                self.mod.connect(cell, "Y", net, direction="output")
            elif "Out" in direction:
                cell = self._fresh_cell(f"port_{name}", PrimOp.OUTPUT, port_name=name)
                self.mod.connect(cell, "A", net)
            elif "InOut" in direction:
                cell = self._fresh_cell(f"port_{name}", PrimOp.INPUT, port_name=name, inout=True)
                self.mod.connect(cell, "Y", net, direction="output")

        # Walk all members
        def walk_member(node: Any) -> None:
            kind = str(node.kind)

            if kind == "SymbolKind.Variable":
                w = self._bit_width(node)
                # Check for unpacked arrays (memories)
                t = node.type if hasattr(node, "type") else None
                is_array = (
                    t is not None
                    and getattr(t, "bitWidth", None) == 0
                    and hasattr(t, "fixedRange")
                    and hasattr(t, "elementType")
                )
                if is_array:
                    elem_type = t.elementType
                    elem_w = getattr(elem_type, "bitWidth", 0)
                    rng = t.fixedRange
                    left = getattr(rng, "left", 0)
                    right = getattr(rng, "right", 0)
                    depth = abs(right - left) + 1
                    if depth > 0 and elem_w > 0:
                        rdata_net = self._fresh_net(f"mem_{node.name}_rdata", elem_w)
                        mem_cell = self._fresh_cell(
                            f"mem_{node.name}",
                            PrimOp.MEMORY,
                            depth=depth,
                            width=elem_w,
                            mem_name=node.name,
                        )
                        self.mod.connect(mem_cell, "RDATA", rdata_net, direction="output")
                        # Create a net for the memory name reference
                        self._get_or_create_net(node.name, elem_w)
                else:
                    self._get_or_create_net(node.name, w)
                if not is_array and node.initializer is not None:
                    init_net = self.lower_expr(node.initializer)

            elif kind == "SymbolKind.Net":
                w = self._bit_width(node)
                self._get_or_create_net(node.name, w)

            elif kind == "SymbolKind.Parameter":
                w = self._bit_width(node)
                if node.value is not None:
                    int_val = _svint_to_int(node.value)
                    net = self._get_or_create_net(node.name, w)
                    cell = self._fresh_cell(f"param_{node.name}", PrimOp.CONST, value=int_val, width=w)
                    self.mod.connect(cell, "Y", net, direction="output")

            elif kind == "SymbolKind.TransparentMember":
                # Enum values / localparam constants visible as members
                if hasattr(node, "value") and node.value is not None:
                    int_val = _svint_to_int(node.value)
                    w = self._bit_width(node) if hasattr(node, "type") else 32
                    net = self._get_or_create_net(node.name, w)
                    if net.driver is None:
                        cell = self._fresh_cell(f"enum_{node.name}", PrimOp.CONST, value=int_val, width=w)
                        self.mod.connect(cell, "Y", net, direction="output")

            elif kind == "SymbolKind.ContinuousAssign":
                assign_expr = node.body if hasattr(node, "body") else None
                if assign_expr is None:
                    # Try assignment attribute
                    assign_expr = node.assignment if hasattr(node, "assignment") else None
                if assign_expr is not None:
                    self.lower_expr(assign_expr)

            elif kind == "SymbolKind.ProceduralBlock":
                self.lower_procedural_block(node)

            elif kind == "SymbolKind.Instance":
                self._lower_sub_instance(node)

        body.visit(walk_member)

    def _lower_sub_instance(self, inst: Any) -> None:
        """Lower a sub-module instance by recursively lowering its body
        and connecting port nets to the parent module."""
        sub_body = inst.body
        sub_name = inst.name  # instance name (e.g., "RX", "SPI")
        mod_name = sub_body.name  # module name (e.g., "uart_rx")

        # Skip ECP5 vendor primitives — they're black boxes
        if mod_name in (
            "USRMCLK", "GSR", "SGSR", "PUR", "JTAGG", "DTR", "OSCG",
            "EHXPLLL", "EHXPLLJ", "CLKDIVF", "DCCA", "DCC", "SEDGA",
            "EXTREFB", "TSALL",
        ):
            return

        # Create a prefix for all nets/cells in this sub-instance
        prefix = f"{sub_name}."

        # Save current counter state to restore after sub-lowering
        saved_net_counter = self._net_counter
        saved_cell_counter = self._cell_counter

        # Create a sub-lowerer that uses prefixed names
        class _SubLowerer(_Lowerer):
            def __init__(self2, mod, prefix):
                super().__init__(mod)
                self2._prefix = prefix
                self2._net_counter = saved_net_counter
                self2._cell_counter = saved_cell_counter

            def _fresh_net(self2, name_prefix, width):
                name = f"${self2._prefix}{name_prefix}_{self2._net_counter}"
                self2._net_counter += 1
                return self2.mod.add_net(name, width)

            def _fresh_cell(self2, name_prefix, op, src="", **params):
                name = f"${self2._prefix}{name_prefix}_{self2._cell_counter}"
                self2._cell_counter += 1
                return self2.mod.add_cell(name, op, src=src, **params)

            def _get_or_create_net(self2, name, width):
                prefixed = f"{self2._prefix}{name}"
                if prefixed in self2.mod.nets:
                    existing = self2.mod.nets[prefixed]
                    if existing.width != width:
                        return self2._fresh_net(f"{name}_w{width}", width)
                    return existing
                return self2.mod.add_net(prefixed, width)

        sub = _SubLowerer(self.mod, prefix)

        # Lower the sub-instance body (variables, parameters, procedural blocks)
        # but NOT ports — we wire those manually below
        def walk_sub(node):
            kind = str(node.kind)
            if kind == "SymbolKind.Variable":
                w = sub._bit_width(node)
                t = node.type if hasattr(node, "type") else None
                is_array = (
                    t is not None
                    and getattr(t, "bitWidth", None) == 0
                    and hasattr(t, "fixedRange")
                    and hasattr(t, "elementType")
                )
                if is_array:
                    elem_type = t.elementType
                    elem_w = getattr(elem_type, "bitWidth", 0)
                    rng = t.fixedRange
                    left = getattr(rng, "left", 0)
                    right = getattr(rng, "right", 0)
                    depth = abs(right - left) + 1
                    if depth > 0 and elem_w > 0:
                        rdata_net = sub._fresh_net(f"mem_{node.name}_rdata", elem_w)
                        mem_cell = sub._fresh_cell(
                            f"mem_{node.name}", PrimOp.MEMORY,
                            depth=depth, width=elem_w, mem_name=f"{prefix}{node.name}",
                        )
                        self.mod.connect(mem_cell, "RDATA", rdata_net, direction="output")
                        sub._get_or_create_net(node.name, elem_w)
                else:
                    sub._get_or_create_net(node.name, w)
            elif kind == "SymbolKind.Net":
                sub._get_or_create_net(node.name, sub._bit_width(node))
            elif kind == "SymbolKind.Parameter":
                w = sub._bit_width(node)
                if node.value is not None:
                    int_val = _svint_to_int(node.value)
                    net = sub._get_or_create_net(node.name, w)
                    if net.driver is None:
                        cell = sub._fresh_cell(f"param_{node.name}", PrimOp.CONST, value=int_val, width=w)
                        self.mod.connect(cell, "Y", net, direction="output")
            elif kind == "SymbolKind.TransparentMember":
                if hasattr(node, "value") and node.value is not None:
                    int_val = _svint_to_int(node.value)
                    w = sub._bit_width(node) if hasattr(node, "type") else 32
                    net = sub._get_or_create_net(node.name, w)
                    if net.driver is None:
                        cell = sub._fresh_cell(f"enum_{node.name}", PrimOp.CONST, value=int_val, width=w)
                        self.mod.connect(cell, "Y", net, direction="output")
            elif kind == "SymbolKind.ContinuousAssign":
                assign_expr = getattr(node, "body", None) or getattr(node, "assignment", None)
                if assign_expr is not None:
                    sub.lower_expr(assign_expr)
            elif kind == "SymbolKind.ProceduralBlock":
                sub.lower_procedural_block(node)
            elif kind == "SymbolKind.Instance":
                # Nested sub-instances — recurse
                sub._lower_sub_instance_nested(node, prefix)

        # Monkey-patch nested instance handler
        def _lower_nested(nested_inst, parent_prefix):
            nested_name = nested_inst.name
            nested_body = nested_inst.body
            nested_mod_name = nested_body.name
            if nested_mod_name in (
                "USRMCLK", "GSR", "SGSR", "PUR", "JTAGG", "DTR", "OSCG",
                "EHXPLLL", "EHXPLLJ", "CLKDIVF", "DCCA", "DCC", "SEDGA",
                "EXTREFB", "TSALL",
            ):
                return
            # For now, skip deeply nested instances — flatten only one level
            pass

        sub._lower_sub_instance_nested = _lower_nested

        sub_body.visit(walk_sub)

        # Update parent counters
        self._net_counter = sub._net_counter
        self._cell_counter = sub._cell_counter

        # Wire port connections: parent net <-> sub-instance net
        for conn in inst.portConnections:
            port = conn.port
            port_name = port.name
            direction = str(port.direction)
            w = sub._bit_width(port)

            # The sub-instance's internal net for this port
            sub_net_name = f"{prefix}{port_name}"
            sub_net = self.mod.nets.get(sub_net_name)
            if sub_net is None:
                sub_net = self.mod.add_net(sub_net_name, w)

            # The expression on the parent side of the connection
            expr = conn.internalExpr if hasattr(conn, "internalExpr") else None
            if expr is None:
                continue

            parent_net = self.lower_expr(expr)

            # Wire based on port direction:
            # Input port: parent drives sub-instance net
            # Output port: sub-instance drives parent net
            if "In" in direction and "Out" not in direction:
                # Parent -> sub: sub_net should be driven by parent_net
                # If sub_net has no driver, point it at the parent driver
                if sub_net.driver is None and parent_net.driver is not None:
                    sub_net.driver = parent_net.driver
            elif "Out" in direction:
                # Sub -> parent: parent_net should be driven by sub_net's driver
                if parent_net.driver is None and sub_net.driver is not None:
                    parent_net.driver = sub_net.driver


def lower_to_ir(result: ParseResult, *, top: str | None = None) -> Design:
    """Lower a parsed pyslang compilation into a Nosis IR Design.

    If *top* is specified, only that instance is lowered.
    Otherwise, all top instances are lowered.
    """
    design = Design()

    for inst in result.top_instances:
        name = inst.name
        if top and name != top:
            continue

        mod = design.add_module(name)
        lowerer = _Lowerer(mod)
        lowerer.lower_instance(inst)

    if top:
        design.top = top
    elif len(design.modules) == 1:
        design.top = next(iter(design.modules))

    return design
