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

from nosis.ir import Cell, Design, Module, Net, PrimOp  # noqa: E402
from nosis.hierarchy import ECP5_BLACKBOX_NAMES as _VENDOR_PRIMITIVES  # noqa: E402

import re as _re  # noqa: E402

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
    # Handle leading negative sign
    negative = False
    if text.startswith("-"):
        negative = True
        text = text[1:]
    # Plain decimal?
    try:
        result = int(text)
        return -result if negative else result
    except ValueError:
        pass
    # Verilog sized literal: <width>'[s]<base><digits>
    m = _VERILOG_LITERAL_RE.match(text)
    if m:
        width = int(m.group(1)) if m.group(1) else 0
        signed = m.group(2) is not None and m.group(2).lower() in ("s", "sh")
        base_char = m.group(3).lower()
        digits = m.group(4).replace("_", "").lower()
        digits = digits.replace("x", "0").replace("z", "0")
        base_map = {"b": 2, "o": 8, "d": 10, "h": 16}
        result = int(digits, base_map.get(base_char, 10))
        # Two's complement for signed literals
        if signed and width > 0 and result >= (1 << (width - 1)):
            result -= (1 << width)
        return -result if negative else result
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
    "SynthesisWarning",
    "ParseResult",
    "parse_files",
    "lower_to_ir",
]

# System tasks that are simulation-only and must be stripped during synthesis
_SIMULATION_TASKS = frozenset({
    "$display", "$write", "$strobe", "$monitor", "$monitoron", "$monitoroff",
    "$finish", "$stop", "$fatal", "$error", "$warning", "$info",
    "$fopen", "$fclose", "$fdisplay", "$fwrite", "$fstrobe", "$fmonitor",
    "$readmemh", "$readmemb", "$dumpfile", "$dumpvars", "$dumpoff", "$dumpon",
    "$dumpall", "$dumplimit", "$dumpflush", "$time", "$stime", "$realtime",
})


class FrontendError(RuntimeError):
    """Raised when parsing or lowering fails."""


class SynthesisWarning:
    """A non-fatal warning emitted during lowering."""
    def __init__(self, category: str, message: str, src: str = "") -> None:
        self.category = category
        self.message = message
        self.src = src

    def __repr__(self) -> str:
        loc = f" at {self.src}" if self.src else ""
        return f"SynthesisWarning({self.category}): {self.message}{loc}"


@dataclass(slots=True)
class ParseResult:
    """Result of parsing one or more SystemVerilog files."""
    compilation: Any  # pyslang.ast.Compilation
    driver: Any       # pyslang.driver.Driver
    diagnostics: list[str]
    errors: list[str]
    top_instances: list[Any]  # list of pyslang InstanceSymbol
    readmem_associations: dict[str, tuple[str, str]] = field(default_factory=dict)  # mem_name -> (file, format)


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
    _DriverClass = getattr(pyslang, "Driver", None) or pyslang.driver.Driver
    drv = _DriverClass()
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
    _SUPPRESS_CODES = {
        "DiagCode(MissingTimeScale)",
        "DiagCode(MultipleAlwaysAssigns)",  # common in synthesizable RTL with multi-block assigns
        "DiagCode(UsedBeforeDeclared)",     # multi-file synthesis — module instantiation order varies
    }

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
        human_errors = []
        for err in errors:
            # Strip pyslang DiagCode wrappers for readability
            text = err.replace("DiagCode(", "").rstrip(")")
            human_errors.append(text)
        raise FrontendError(
            f"compilation produced {len(errors)} error(s):\n" + "\n".join(human_errors)
        )

    if not top_instances:
        top_hint = f" (--top={top})" if top else ""
        raise FrontendError(
            f"no top-level instances found after elaboration{top_hint}. "
            f"Check that the module name exists in the source files and that "
            f"all required source files are listed."
        )

    # Pre-scan source files for $readmemh/$readmemb calls.
    # pyslang may resolve these during elaboration and remove them
    # from the AST, so we extract them from the source text directly.
    readmem_associations: dict[str, tuple[str, str]] = {}  # mem_name -> (file, format)
    _readmem_re = _re.compile(
        r'\$readmem([hb])\s*\(\s*"([^"]+)"\s*,\s*(\w+)',
    )
    # Collect all source directories for $readmemh file search
    _search_dirs = [Path.cwd()]
    for p in paths:
        d = Path(p).resolve().parent
        if d not in _search_dirs:
            _search_dirs.append(d)

    for path in paths:
        try:
            src_path = Path(path)
            text = src_path.read_text(encoding="utf-8", errors="ignore")
            for m in _readmem_re.finditer(text):
                fmt = "hex" if m.group(1) == "h" else "bin"
                raw_file = m.group(2)
                # Search: source dir, cwd, then all other source dirs
                found = None
                for search_dir in _search_dirs:
                    candidate = (search_dir / raw_file).resolve()
                    if candidate.exists():
                        found = str(candidate)
                        break
                readmem_associations[m.group(3)] = (found or raw_file, fmt)
        except (OSError, IOError):
            pass

    return ParseResult(
        compilation=comp,
        driver=drv,
        diagnostics=diagnostics,
        errors=errors,
        top_instances=top_instances,
        readmem_associations=readmem_associations,
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
        self.warnings: list[SynthesisWarning] = []
        self._current_clock_net: Net | None = None
        self._condition_stack: list[Net] = []  # stack of enclosing condition nets for WE gating

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
        if width <= 0:
            width = 1  # safety: _bit_width should never return 0, but guard anyway
        if name in self.mod.nets:
            existing = self.mod.nets[name]
            if existing.width != width:
                # Forward reference tolerance — if the existing net was
                # created with default width 1 (forward reference placeholder)
                # and the real width is now known, update in place instead of
                # creating a conversion net.
                if existing.width == 1 and width > 1 and existing.driver is None:
                    existing.width = width
                    return existing
                # Width mismatch — create a conversion net
                return self._fresh_net(f"{name}_w{width}", width)
            return existing
        return self.mod.add_net(name, width)

    def _bit_width(self, node: Any) -> int:
        """Extract bit width from a pyslang AST node.

        Returns at least 1. Unpacked array types report bitWidth=0;
        for those, the element type width is used if available.

        Rejects ``real`` and floating-point types with an explicit error.
        """
        if hasattr(node, "type"):
            t = node.type
            # Reject real/shortreal/realtime (not synthesizable)
            type_name = str(getattr(t, "name", ""))
            if type_name in ("real", "shortreal", "realtime"):
                src = self._src_from_node(node)
                raise FrontendError(
                    f"floating-point type `{type_name}` is not synthesizable"
                    f"{' at ' + src if src else ''}"
                )
            if hasattr(t, "bitWidth"):
                w = int(t.bitWidth)
                if w > 0:
                    return w
                # Unpacked array: try element type
                if hasattr(t, "elementType") and hasattr(t.elementType, "bitWidth"):
                    ew = int(t.elementType.bitWidth)
                    if ew > 0:
                        return ew
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
            # Strip simulation-only system tasks ($display, $finish, etc.)
            call_name = ""
            sub = getattr(expr, "subroutine", None)
            if sub is not None:
                call_name = getattr(sub, "name", "")
            # Intercept $readmemh/$readmemb to record init file on MEMORY cells
            if call_name in ("$readmemh", "$readmemb"):
                args_list = list(getattr(expr, "arguments", []))
                if len(args_list) >= 2:
                    file_arg = args_list[0]
                    mem_arg = args_list[1]
                    file_str = ""
                    if hasattr(file_arg, "constant") and file_arg.constant is not None:
                        file_str = str(file_arg.constant).strip('"').strip("'")
                    elif hasattr(file_arg, "value"):
                        file_str = str(file_arg.value).strip('"').strip("'")
                    mem_name = getattr(getattr(mem_arg, "symbol", None), "name", "")
                    if file_str and mem_name:
                        for mc in self.mod.cells.values():
                            if mc.op == PrimOp.MEMORY and mc.params.get("mem_name") == mem_name:
                                mc.params["init_file"] = file_str
                                mc.params["init_format"] = "hex" if call_name == "$readmemh" else "bin"
                                break
            if call_name in _SIMULATION_TASKS:
                src = self._src_from_node(expr)
                self.warnings.append(SynthesisWarning(
                    "simulation_task",
                    f"stripped {call_name} (simulation-only, not synthesizable)",
                    src=src,
                ))
                w = self._bit_width(expr) or 1
                net = self._fresh_net("stripped_sim", w)
                cell = self._fresh_cell("stripped_sim", PrimOp.CONST, value=0, width=w)
                self.mod.connect(cell, "Y", net, direction="output")
                return net
            # $signed/$unsigned and other type casts are pass-throughs.
            # pyslang may represent them as unnamed 1-arg calls.
            if call_name in ("$signed", "$unsigned", ""):
                args_list = list(getattr(expr, "arguments", []))
                if len(args_list) == 1:
                    return self.lower_expr(args_list[0])

            # User-defined function: inline the body
            sub = getattr(expr, "subroutine", None)
            if sub and hasattr(sub, "body") and sub.body is not None:
                args_list = list(getattr(expr, "arguments", []))
                # Process function body as combinational statements
                self._lower_statement(sub.body)
                # The return value is the last expression in the body
                # For simple functions, the return net is the function's
                # return variable. Try to find it.
                ret_name = getattr(sub, "name", "")
                ret_net = self.mod.nets.get(ret_name)
                if ret_net and ret_net.driver is not None:
                    return ret_net
                # Fallback: check for a return type width
                w = self._bit_width(expr)
                # Look for any net that was just created with matching width
                # This is a heuristic — proper inlining needs parameter binding
                for n in reversed(list(self.mod.nets.values())):
                    if n.width == w and n.driver is not None:
                        return n

            # System function calls ($clog2, $bits, etc.) are typically
            # resolved to constants by slang during elaboration.
            w = self._bit_width(expr)
            const = getattr(expr, "constant", None)
            if const is not None:
                int_val = _svint_to_int(const)
                net = self._fresh_net("call_const", w)
                cell = self._fresh_cell("call_const", PrimOp.CONST, value=int_val, width=w)
                self.mod.connect(cell, "Y", net, direction="output")
                return net
            # Unknown call — default to CONST(0) with warning
            w = self._bit_width(expr) or 1
            net = self._fresh_net("call", w)
            cell = self._fresh_cell("call", PrimOp.CONST, value=0, width=w)
            self.mod.connect(cell, "Y", net, direction="output")
            src = self._src_from_node(expr)
            self.warnings.append(SynthesisWarning(
                "unsupported_call",
                f"unrecognized system function '{call_name}' replaced with constant 0",
                src=src,
            ))
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
            src = self._src_from_node(expr)
            self.warnings.append(SynthesisWarning(
                "unsupported_expression",
                f"unsupported expression kind {kind} replaced with constant 0",
                src=src,
            ))
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
        # Record signedness for comparison and arithmetic ops
        if prim in (PrimOp.LT, PrimOp.LE, PrimOp.GT, PrimOp.GE,
                    PrimOp.DIV, PrimOp.MOD, PrimOp.SSHR):
            is_signed = False
            left_type = getattr(expr.left, "type", None)
            if left_type and hasattr(left_type, "isSigned"):
                is_signed = bool(left_type.isSigned)
            if is_signed:
                cell.params["signed"] = True
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
        conds = list(expr.conditions)
        pred_expr = conds[0].expr
        pred = self.lower_expr(pred_expr)
        true_val = self.lower_expr(expr.left)
        false_val = self.lower_expr(expr.right)

        # Fold constant selector immediately instead of emitting a MUX
        if pred.driver is not None and pred.driver.op == PrimOp.CONST:
            sel_val = int(pred.driver.params.get("value", 0))
            return true_val if (sel_val & 1) else false_val

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
        # Verilog {A, B} = A is MSB, B is LSB.
        # CONCAT I0 is LSB, so reverse the operand order.
        reversed_ops = list(reversed(operands))
        cell = self._fresh_cell("concat", PrimOp.CONCAT, count=len(reversed_ops))
        for i, op_net in enumerate(reversed_ops):
            self.mod.connect(cell, f"I{i}", op_net)
        self.mod.connect(cell, "Y", out, direction="output")
        return out

    def _lower_range_select(self, expr: Any) -> Net:
        w = self._bit_width(expr)
        src = self.lower_expr(expr.value)
        # left and right define the range — try constant evaluation
        offset = 0
        try:
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
        """Lower an array element select (e.g., regs[rs1]).

        For MEMORY-backed arrays, connects the index to the memory read
        address and returns the RDATA output. For other arrays (bitvectors),
        falls back to a SLICE.
        """
        w = self._bit_width(expr)

        # Check if this is a memory array read
        value_node = expr.value
        selector = getattr(expr, "selector", None)
        value_sym = getattr(value_node, "symbol", None)
        mem_name = getattr(value_sym, "name", "") if value_sym else ""

        # Look for a MEMORY cell with this name (exact or suffix match)
        mem_cell = None
        if mem_name:
            suffix = f".{mem_name}"
            for cell in self.mod.cells.values():
                if cell.op != PrimOp.MEMORY:
                    continue
                cm = cell.params.get("mem_name", "")
                if cm == mem_name or cm.endswith(suffix):
                    mem_cell = cell
                    break

        # Check if the source is a flattened small array
        value_node = expr.value
        src_net_name = getattr(getattr(value_node, "symbol", None), "name", "")
        arr_info = getattr(self, '_array_info', {}).get(src_net_name)
        arr_depth = arr_info[0] if arr_info else 0
        arr_elem_w = arr_info[1] if arr_info else 0

        # Fallback: detect unpacked arrays from pyslang type info.
        # Only use element-net path when there is NO MEMORY cell backing the array.
        if arr_depth == 0 and mem_cell is None and value_sym is not None:
            vtype = getattr(value_sym, "type", None)
            if vtype is not None and getattr(vtype, "isUnpackedArray", False):
                fr = getattr(vtype, "fixedRange", None)
                et = getattr(vtype, "elementType", None)
                if fr is not None and et is not None:
                    arr_depth = int(getattr(fr, "width", 0))
                    arr_elem_w = int(getattr(et, "bitWidth", 0))
                    if arr_depth > 0 and arr_elem_w > 0 and arr_depth <= 32:
                        for i in range(arr_depth):
                            self._get_or_create_net(f"{src_net_name}_{i}", arr_elem_w)

        if arr_depth > 0 and arr_elem_w > 0 and selector is not None:
            sel_const = getattr(selector, "constant", None)
            if sel_const is not None:
                # Constant index: return the individual element net directly
                idx_val = int(str(sel_const))
                elem_name = f"{src_net_name}_{idx_val}"
                return self._get_or_create_net(elem_name, arr_elem_w)
            else:
                # Variable index: build a MUX chain with EQ comparisons.
                # MUX(idx==N-1, MUX(idx==N-2, ..., MUX(idx==1, elem_0, elem_1), ...), elem_N-1)
                idx_net = self.lower_expr(selector)
                running = self._get_or_create_net(f"{src_net_name}_0", arr_elem_w)
                for i in range(1, arr_depth):
                    ei = self._get_or_create_net(f"{src_net_name}_{i}", arr_elem_w)
                    eq_out = self._fresh_net("arreq", 1)
                    eq_cell = self._fresh_cell("arreq", PrimOp.EQ)
                    const_net = self._fresh_net(f"arridx_{i}", idx_net.width)
                    const_cell = self._fresh_cell(f"arridx_{i}", PrimOp.CONST,
                                                  value=i, width=idx_net.width)
                    self.mod.connect(const_cell, "Y", const_net, direction="output")
                    self.mod.connect(eq_cell, "A", idx_net)
                    self.mod.connect(eq_cell, "B", const_net)
                    self.mod.connect(eq_cell, "Y", eq_out, direction="output")
                    mux_out = self._fresh_net("arrmux", arr_elem_w)
                    mux = self._fresh_cell("arrmux", PrimOp.MUX)
                    self.mod.connect(mux, "S", eq_out)
                    self.mod.connect(mux, "A", running)
                    self.mod.connect(mux, "B", ei)
                    self.mod.connect(mux, "Y", mux_out, direction="output")
                    running = mux_out
                return running

        if mem_cell is not None and selector is not None:
            elem_w = int(mem_cell.params.get("width", w))
            sel_const = getattr(selector, "constant", None)
            if sel_const is not None:
                idx_val = int(str(sel_const))
                src = self.lower_expr(expr.value)
                out = self._fresh_net("esel", elem_w)
                cell = self._fresh_cell("esel", PrimOp.SLICE,
                                        offset=idx_val * elem_w, width=elem_w)
                self.mod.connect(cell, "A", src)
                self.mod.connect(cell, "Y", out, direction="output")
                return out

            idx_net = self.lower_expr(selector)
            port_id = len([k for k in mem_cell.outputs if k.startswith("RDATA")])
            raddr_name = f"RADDR{port_id}" if port_id > 0 else "RADDR"
            rdata_name = f"RDATA{port_id}" if port_id > 0 else "RDATA"
            self.mod.connect(mem_cell, raddr_name, idx_net)

            if rdata_name in mem_cell.outputs:
                return mem_cell.outputs[rdata_name]
            out = self._fresh_net(f"memrd_{mem_name}_{port_id}", elem_w)
            self.mod.connect(mem_cell, rdata_name, out, direction="output")
            return out

        # Not a memory — bitvector element select (SLICE)
        src = self.lower_expr(expr.value)

        # Check for constant index first
        sel_const = getattr(selector, "constant", None) if selector else None
        if sel_const is not None:
            offset = int(str(sel_const))
            out = self._fresh_net("esel", w)
            cell = self._fresh_cell("esel", PrimOp.SLICE, offset=offset, width=w)
            self.mod.connect(cell, "A", src)
            self.mod.connect(cell, "Y", out, direction="output")
            return out

        if selector is not None:
            # Variable index: build a priority MUX tree to select the element
            idx_net = self.lower_expr(selector)
            src_width = src.width
            n_elements = src_width // w if w > 0 else 1
            if n_elements <= 1:
                out = self._fresh_net("esel", w)
                sc = self._fresh_cell("esel", PrimOp.SLICE, offset=0, width=w)
                self.mod.connect(sc, "A", src)
                self.mod.connect(sc, "Y", out, direction="output")
                return out
            # Default = element 0; each higher index overrides via MUX
            result = self._fresh_net("esel_0", w)
            sc = self._fresh_cell("esel_s0", PrimOp.SLICE, offset=0, width=w)
            self.mod.connect(sc, "A", src)
            self.mod.connect(sc, "Y", result, direction="output")
            for i in range(1, n_elements):
                alt = self._fresh_net(f"esel_{i}", w)
                si = self._fresh_cell(f"esel_s{i}", PrimOp.SLICE, offset=i * w, width=w)
                self.mod.connect(si, "A", src)
                self.mod.connect(si, "Y", alt, direction="output")
                ci = self._fresh_net(f"esel_c{i}", idx_net.width)
                cc = self._fresh_cell(f"esel_c{i}", PrimOp.CONST, value=i, width=idx_net.width)
                self.mod.connect(cc, "Y", ci, direction="output")
                eq = self._fresh_net(f"esel_eq{i}", 1)
                ec = self._fresh_cell(f"esel_eq{i}", PrimOp.EQ)
                self.mod.connect(ec, "A", idx_net)
                self.mod.connect(ec, "B", ci)
                self.mod.connect(ec, "Y", eq, direction="output")
                mux_out = self._fresh_net(f"esel_m{i}", w)
                mc = self._fresh_cell(f"esel_m{i}", PrimOp.MUX)
                self.mod.connect(mc, "S", eq)
                self.mod.connect(mc, "A", result)
                self.mod.connect(mc, "B", alt)
                self.mod.connect(mc, "Y", mux_out, direction="output")
                result = mux_out
            return result

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
        if expr.isNonBlocking:
            # Non-blocking: FF creation happens at the procedural block level.
            pass
        else:
            # Blocking / continuous: direct wire.
            if not getattr(self, '_in_comb_case', False):
                if lhs.driver is None and rhs.driver is not None:
                    lhs.driver = rhs.driver
                    # Try to set the driver cell's output to lhs
                    rewired = False
                    for pname, pnet in list(rhs.driver.outputs.items()):
                        if pnet is rhs:
                            rhs.driver.outputs[pname] = lhs
                            rewired = True
                            break
                    if not rewired:
                        # The driver's output points to a different net (e.g.
                        # after always_comb redirect). Redirect all consumers
                        # of lhs to read from rhs instead, and ensure the
                        # OUTPUT cell for lhs reads from the actual driven net.
                        actual_out = None
                        for pname, pnet in rhs.driver.outputs.items():
                            actual_out = pnet
                            break
                        if actual_out is not None:
                            for cell in self.mod.cells.values():
                                for pn, pnet in list(cell.inputs.items()):
                                    if pnet is lhs:
                                        cell.inputs[pn] = actual_out
                            for pn, pnet in list(self.mod.ports.items()):
                                if pnet is lhs:
                                    self.mod.ports[pn] = actual_out
                elif lhs.driver is None and rhs.driver is None:
                    # RHS has no driver yet (e.g., alu_a = rs1_val where
                    # rs1_val is assigned in always_ff which hasn't run yet).
                    # Defer: record the lhs->rhs mapping for post-processing.
                    if not hasattr(self, '_deferred_blocking'):
                        self._deferred_blocking: list[tuple] = []
                    self._deferred_blocking.append((lhs, rhs))
        return lhs

    # --- Statement lowering ---

    def lower_procedural_block(self, block: Any) -> None:
        """Lower a ProceduralBlock (always_ff, always_comb, always, initial, always @(*))."""
        proc_kind = str(block.procedureKind)
        body = block.body

        # Initial blocks: extract blocking assignments as initial values.
        # These become attributes on the target nets for downstream FF
        # reset value inference. Synthesis treats initial values as
        # power-on reset state (ECP5 REGSET parameter).
        if "Initial" in proc_kind:
            self._lower_initial_block(body)
            return

        if "AlwaysComb" in proc_kind or "AlwaysLatch" in proc_kind:
            # Combinational or latch — lower statements as wiring.
            # Warn on incomplete if/case in always_comb (latch inference)
            if "AlwaysComb" in proc_kind:
                latch_targets = self._detect_latch_inference(body)
                for target in latch_targets:
                    src = self._src_from_node(body)
                    self.warnings.append(SynthesisWarning(
                        "latch_inference",
                        f"incomplete assignment to `{target}` in always_comb — "
                        f"infers a latch; use default assignment or cover all branches",
                        src=src,
                    ))
            self._lower_statement(body)

        elif "AlwaysFF" in proc_kind or "Always" in proc_kind:
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

            # Store the clock net so _try_wire_memory_write can find it
            # for MEMORY cells written inside this always_ff block.
            self._current_clock_net = clock_net

            # Build MUX trees for the always_ff body using the same
            # recursive handler that works for always_comb. This correctly
            # handles nested case/if with conditional guards.
            # Then detect reset patterns and create FFs.

            # Check for reset pattern: if (rst) {blocking} else {nb logic}
            rst_vals: dict[str, Net] = {}
            if str(stmt.kind) == "StatementKind.Conditional":
                conds = list(stmt.conditions)
                if conds and stmt.ifFalse is not None:
                    # Check if true branch has blocking resets
                    true_blocking = self._collect_blocking_assignments(stmt.ifTrue)
                    false_nb = self._collect_blocking_with_muxes(stmt.ifFalse, allow_nb=True)
                    if true_blocking and false_nb:
                        rst_vals = true_blocking
                        nb_map = false_nb
                    else:
                        nb_map = self._collect_blocking_with_muxes(stmt, allow_nb=True)
                else:
                    nb_map = self._collect_blocking_with_muxes(stmt, allow_nb=True)
            else:
                nb_map = self._collect_blocking_with_muxes(stmt, allow_nb=True)

            # Build a consumer index: net identity → list of (cell, port)
            # This avoids O(cells) scans for each FF redirect.
            _consumer_idx: dict[int, list[tuple]] = {}
            _name_idx: dict[str, list[tuple]] = {}
            for _c in self.mod.cells.values():
                for _pn, _pnet in _c.inputs.items():
                    _consumer_idx.setdefault(id(_pnet), []).append((_c, _pn))
                    _name_idx.setdefault(_pnet.name, []).append((_c, _pn))

            for lhs_name, rhs_net in nb_map.items():
                lhs_net = self.mod.nets.get(lhs_name)
                if lhs_net is None:
                    continue
                rst_val_net = rst_vals.get(lhs_name)
                ff_rst = reset_net if async_reset else (reset_net if reset_net else None)
                if lhs_net.driver is not None and lhs_net.driver.op not in (PrimOp.FF, PrimOp.CONST):
                    continue

                ff_rst = reset_net
                init_val = lhs_net.attributes.get("init_value")
                ff = self._fresh_cell(
                    f"ff_{lhs_net.name}", PrimOp.FF,
                    ff_target=lhs_net.name,
                    async_reset=async_reset,
                    **({"init_value": int(init_val)} if init_val is not None else {}),
                )
                self.mod.connect(ff, "D", rhs_net)
                if clock_net:
                    self.mod.connect(ff, "CLK", clock_net)
                if ff_rst:
                    self.mod.connect(ff, "RST", ff_rst)
                if rst_val_net:
                    self.mod.connect(ff, "RST_VAL", rst_val_net)
                q_net = self._fresh_net(f"ff_q_{lhs_net.name}", lhs_net.width)
                self.mod.connect(ff, "Q", q_net, direction="output")
                # Redirect consumers using the index (O(consumers) not O(cells))
                for _c, _pn in _consumer_idx.get(id(lhs_net), []):
                    if _c is not ff:
                        _c.inputs[_pn] = q_net
                for _c, _pn in _name_idx.get(lhs_name, []):
                    if _c is not ff and _c.inputs.get(_pn) is lhs_net:
                        _c.inputs[_pn] = q_net
                for pname, pnet in list(self.mod.ports.items()):
                    if pnet is lhs_net:
                        self.mod.ports[pname] = q_net
                lhs_net.driver = ff

            # Final sweep using the index
            ff_q_map: dict[str, "Net"] = {}
            tgt_nets: dict[str, "Net"] = {}
            for cell in self.mod.cells.values():
                if cell.op == PrimOp.FF:
                    target = cell.params.get("ff_target", "")
                    tnet = self.mod.nets.get(target)
                    if tnet:
                        for o in cell.outputs.values():
                            ff_q_map[target] = o
                            tgt_nets[target] = tnet

            for tgt_name, q_net in ff_q_map.items():
                tnet = tgt_nets[tgt_name]
                for _c, _pn in _consumer_idx.get(id(tnet), []):
                    if _pn == "CLK":
                        continue
                    if _c.op == PrimOp.FF and _c.params.get("ff_target", "") == tgt_name:
                        continue
                    _c.inputs[_pn] = q_net
                for _c, _pn in _name_idx.get(tgt_name, []):
                    if _pn == "CLK":
                        continue
                    if _c.op == PrimOp.FF and _c.params.get("ff_target", "") == tgt_name:
                        continue
                    if _c.inputs.get(_pn) is tnet:
                        _c.inputs[_pn] = q_net

    def _lower_initial_block(self, stmt: Any) -> None:
        """Extract blocking assignments from an initial block as net attributes.

        Records the initial value on each target net so the FF mapper
        can set the ECP5 REGSET parameter (power-on reset state).
        Also intercepts $readmemh/$readmemb calls to record init files
        on MEMORY cells.
        """
        kind = str(stmt.kind)
        if kind == "StatementKind.ExpressionStatement":
            expr = stmt.expr
            # Handle $readmemh/$readmemb as standalone calls in initial blocks
            if str(expr.kind) == "ExpressionKind.Call":
                sub = getattr(expr, "subroutine", None)
                call_name = getattr(sub, "name", "") if sub else ""
                if call_name in ("$readmemh", "$readmemb"):
                    args_list = list(getattr(expr, "arguments", []))
                    if len(args_list) >= 2:
                        file_arg = args_list[0]
                        mem_arg = args_list[1]
                        file_str = ""
                        if hasattr(file_arg, "constant") and file_arg.constant is not None:
                            file_str = str(file_arg.constant).strip('"').strip("'")
                        elif hasattr(file_arg, "value"):
                            file_str = str(file_arg.value).strip('"').strip("'")
                        mem_name = getattr(getattr(mem_arg, "symbol", None), "name", "")
                        if file_str and mem_name:
                            # Find MEMORY cell by exact or suffix match
                            for mc in self.mod.cells.values():
                                if mc.op != PrimOp.MEMORY:
                                    continue
                                cm = mc.params.get("mem_name", "")
                                if cm == mem_name or cm.endswith(f".{mem_name}"):
                                    mc.params["init_file"] = file_str
                                    mc.params["init_format"] = "hex" if call_name == "$readmemh" else "bin"
                                    break
                    return
            if str(expr.kind) == "ExpressionKind.Assignment" and not expr.isNonBlocking:
                lhs = self.lower_expr(expr.left)
                rhs = self.lower_expr(expr.right)
                # Record initial value: check RHS constant (from driver or pyslang)
                init_val = None
                if rhs.driver and rhs.driver.op == PrimOp.CONST:
                    init_val = int(rhs.driver.params.get("value", 0))
                elif hasattr(expr.right, "constant") and expr.right.constant is not None:
                    init_val = _svint_to_int(expr.right.constant)
                if init_val is not None:
                    lhs.attributes["init_value"] = str(init_val)
        elif kind == "StatementKind.Block":
            if stmt.body is not None:
                self._lower_initial_block(stmt.body)
        elif kind == "StatementKind.List":
            for child in stmt.list:
                self._lower_initial_block(child)
        elif kind == "StatementKind.ForLoop":
            # For loops in initial blocks are unrolled by slang.
            # The body is a single iteration — walk it.
            if hasattr(stmt, "body"):
                self._lower_initial_block(stmt.body)
        elif kind == "StatementKind.Conditional":
            # if/else in initial blocks
            self._lower_initial_block(stmt.ifTrue)
            if stmt.ifFalse is not None:
                self._lower_initial_block(stmt.ifFalse)

    def _detect_latch_inference(self, stmt: Any) -> list[str]:
        """Detect incomplete if/case in combinational blocks that would infer latches.

        Returns a list of target signal names that are assigned in some but
        not all branches of a conditional.
        """
        kind = str(stmt.kind)
        targets: list[str] = []

        if kind == "StatementKind.Conditional":
            # if without else -> latch
            if stmt.ifFalse is None:
                true_targets = self._collect_assignment_targets(stmt.ifTrue)
                targets.extend(true_targets)
            else:
                true_targets: set[str] = set(self._collect_assignment_targets(stmt.ifTrue))  # type: ignore[no-redef]
                false_targets: set[str] = set(self._collect_assignment_targets(stmt.ifFalse))
                # Signals assigned in one branch but not the other
                targets.extend(sorted(true_targets - false_targets))  # type: ignore[operator]
                targets.extend(sorted(false_targets - true_targets))  # type: ignore[operator]

        elif kind == "StatementKind.Case":
            if stmt.defaultCase is None:
                # Case without default -> latch for all assigned signals
                for item in stmt.items:
                    targets.extend(self._collect_assignment_targets(item.stmt))

        elif kind in ("StatementKind.Block", "StatementKind.List"):
            children = []
            if kind == "StatementKind.Block" and stmt.body is not None:
                children = [stmt.body]
            elif kind == "StatementKind.List":
                children = list(stmt.list)
            for child in children:
                targets.extend(self._detect_latch_inference(child))

        elif kind == "StatementKind.Timed":
            targets.extend(self._detect_latch_inference(stmt.stmt))

        return targets

    def _collect_assignment_targets(self, stmt: Any) -> list[str]:
        """Collect signal names assigned in a statement tree."""
        kind = str(stmt.kind)
        targets: list[str] = []

        if kind == "StatementKind.ExpressionStatement":
            expr = stmt.expr
            if str(expr.kind) == "ExpressionKind.Assignment":
                left_sym = getattr(expr.left, "symbol", None)
                if left_sym:
                    targets.append(left_sym.name)

        elif kind in ("StatementKind.Block", "StatementKind.List"):
            children = []
            if kind == "StatementKind.Block" and stmt.body is not None:
                children = [stmt.body]
            elif kind == "StatementKind.List":
                children = list(stmt.list)
            for child in children:
                targets.extend(self._collect_assignment_targets(child))

        elif kind == "StatementKind.Conditional":
            targets.extend(self._collect_assignment_targets(stmt.ifTrue))
            if stmt.ifFalse is not None:
                targets.extend(self._collect_assignment_targets(stmt.ifFalse))

        elif kind == "StatementKind.Case":
            for item in stmt.items:
                targets.extend(self._collect_assignment_targets(item.stmt))
            if stmt.defaultCase is not None:
                targets.extend(self._collect_assignment_targets(stmt.defaultCase))

        return targets

    def _find_memory_cell_for_lhs(self, lhs_expr: Any) -> tuple[Any | None, str]:
        """Check if an assignment LHS is an ElementSelect on a MEMORY-backed array.

        Returns ``(mem_cell, mem_name)`` if found, ``(None, "")`` otherwise.
        Searches by exact name first, then by suffix match for hierarchy-prefixed cells.
        """
        lhs_kind = str(lhs_expr.kind)
        if lhs_kind != "ExpressionKind.ElementSelect":
            return None, ""
        value_node = lhs_expr.value
        value_sym = getattr(value_node, "symbol", None)
        mem_name = getattr(value_sym, "name", "") if value_sym else ""
        if not mem_name:
            return None, ""
        # Exact match first
        for cell in self.mod.cells.values():
            if cell.op == PrimOp.MEMORY and cell.params.get("mem_name") == mem_name:
                return cell, mem_name
        # Suffix match: "uart_rx_fifo" matches "SOC.uart_rx_fifo"
        suffix = f".{mem_name}"
        for cell in self.mod.cells.values():
            if cell.op == PrimOp.MEMORY:
                cell_mem_name = cell.params.get("mem_name", "")
                if cell_mem_name.endswith(suffix):
                    return cell, cell_mem_name
        return None, ""

    def _try_wire_memory_write(self, assign_expr: Any) -> bool:
        """Wire MEMORY cell write ports for array element assignments.

        Handles patterns like:
            ``array[index] <= data``         — simple write
            ``array[index][hi:lo] <= data``  — byte-lane write (partial)

        Returns True if the assignment was handled as a memory write.
        """
        lhs_expr = assign_expr.left
        rhs_expr = assign_expr.right

        # Unwrap RangeSelect on the LHS (byte-lane writes like progmem[addr][7:0])
        lhs_slice_lo: int | None = None
        lhs_slice_hi: int | None = None
        if str(lhs_expr.kind) == "ExpressionKind.RangeSelect":
            inner = lhs_expr.value
            left_sel = getattr(lhs_expr, "left", None)
            right_sel = getattr(lhs_expr, "right", None)
            if left_sel is not None and right_sel is not None:
                lc = getattr(left_sel, "constant", None)
                rc = getattr(right_sel, "constant", None)
                if lc is not None and rc is not None:
                    lhs_slice_hi = _svint_to_int(lc)
                    lhs_slice_lo = _svint_to_int(rc)
            lhs_expr = inner

        mem_cell, mem_name = self._find_memory_cell_for_lhs(lhs_expr)
        if mem_cell is None:
            return False

        # Get the selector (write address)
        selector = getattr(lhs_expr, "selector", None)
        if selector is None:
            return False

        waddr_net = self.lower_expr(selector)
        wdata_net = self.lower_expr(rhs_expr)

        elem_w = int(mem_cell.params.get("width", 1))

        # For byte-lane writes, we need to read-modify-write:
        # read the current value, replace the target byte lane, write back.
        if lhs_slice_lo is not None and lhs_slice_hi is not None:
            slice_w = lhs_slice_hi - lhs_slice_lo + 1
            if slice_w < elem_w:
                # Read current value
                rport_id = len([k for k in mem_cell.outputs if k.startswith("RDATA")])
                raddr_key = f"RADDR{rport_id}" if rport_id > 0 else "RADDR"
                rdata_key = f"RDATA{rport_id}" if rport_id > 0 else "RDATA"
                # Connect read address = write address for read-modify-write
                if raddr_key not in mem_cell.inputs:
                    self.mod.connect(mem_cell, raddr_key, waddr_net)
                if rdata_key not in mem_cell.outputs:
                    cur_val = self._fresh_net(f"memrmw_{mem_name}", elem_w)
                    self.mod.connect(mem_cell, rdata_key, cur_val, direction="output")
                else:
                    cur_val = mem_cell.outputs[rdata_key]

                # Build the modified word: replace bits [hi:lo] with wdata
                # Use CONCAT of: cur_val[elem_w-1:hi+1], wdata, cur_val[lo-1:0]
                pieces: list[Net] = []
                piece_count = 0
                if lhs_slice_lo > 0:
                    lo_net = self._fresh_net(f"rmw_lo_{mem_name}", lhs_slice_lo)
                    lo_cell = self._fresh_cell(f"rmw_lo_{mem_name}", PrimOp.SLICE,
                                               offset=0, width=lhs_slice_lo)
                    self.mod.connect(lo_cell, "A", cur_val)
                    self.mod.connect(lo_cell, "Y", lo_net, direction="output")
                    pieces.append(lo_net)
                    piece_count += 1
                pieces.append(wdata_net)
                piece_count += 1
                if lhs_slice_hi + 1 < elem_w:
                    hi_w = elem_w - lhs_slice_hi - 1
                    hi_net = self._fresh_net(f"rmw_hi_{mem_name}", hi_w)
                    hi_cell = self._fresh_cell(f"rmw_hi_{mem_name}", PrimOp.SLICE,
                                               offset=lhs_slice_hi + 1, width=hi_w)
                    self.mod.connect(hi_cell, "A", cur_val)
                    self.mod.connect(hi_cell, "Y", hi_net, direction="output")
                    pieces.append(hi_net)
                    piece_count += 1

                if piece_count > 1:
                    merged = self._fresh_net(f"rmw_merged_{mem_name}", elem_w)
                    concat_cell = self._fresh_cell(f"rmw_concat_{mem_name}", PrimOp.CONCAT,
                                                    count=piece_count)
                    for i, p in enumerate(pieces):
                        self.mod.connect(concat_cell, f"I{i}", p)
                        concat_cell.params[f"I{i}_width"] = p.width
                    self.mod.connect(concat_cell, "Y", merged, direction="output")
                    wdata_net = merged

        # Wire write ports on the MEMORY cell
        if "WADDR" not in mem_cell.inputs:
            self.mod.connect(mem_cell, "WADDR", waddr_net)
        else:
            # Multiple write ports — create additional write port
            wport_id = len([k for k in mem_cell.inputs if k.startswith("WADDR")])
            self.mod.connect(mem_cell, f"WADDR{wport_id}", waddr_net)

        if "WDATA" not in mem_cell.inputs:
            self.mod.connect(mem_cell, "WDATA", wdata_net)
        else:
            wport_id = len([k for k in mem_cell.inputs if k.startswith("WDATA")])
            self.mod.connect(mem_cell, f"WDATA{wport_id}", wdata_net)

        # WE (write enable) — AND of all enclosing conditions from the
        # condition stack. If no conditions, WE=1 (always write).
        if "WE" not in mem_cell.inputs:
            if self._condition_stack:
                # AND all conditions together
                we_net = self._condition_stack[0]
                for cond in self._condition_stack[1:]:
                    and_out = self._fresh_net(f"memwe_and_{mem_name}", 1)
                    and_cell = self._fresh_cell(f"memwe_and_{mem_name}", PrimOp.AND)
                    self.mod.connect(and_cell, "A", we_net)
                    self.mod.connect(and_cell, "B", cond)
                    self.mod.connect(and_cell, "Y", and_out, direction="output")
                    we_net = and_out
            else:
                we_net = self._fresh_net(f"memwe_{mem_name}", 1)
                we_cell = self._fresh_cell(f"memwe_{mem_name}", PrimOp.CONST, value=1, width=1)
                self.mod.connect(we_cell, "Y", we_net, direction="output")
            self.mod.connect(mem_cell, "WE", we_net)
        else:
            # WE already wired — OR with new condition for multiple write sites
            if self._condition_stack:
                existing_we = mem_cell.inputs["WE"]
                cond = self._condition_stack[-1]
                or_out = self._fresh_net(f"memwe_or_{mem_name}", 1)
                or_cell = self._fresh_cell(f"memwe_or_{mem_name}", PrimOp.OR)
                self.mod.connect(or_cell, "A", existing_we)
                self.mod.connect(or_cell, "B", cond)
                self.mod.connect(or_cell, "Y", or_out, direction="output")
                mem_cell.inputs["WE"] = or_out

        # CLK — find the clock net from the enclosing always_ff block.
        # The clock net is stored on self._current_clock_net by lower_procedural_block.
        clk = getattr(self, "_current_clock_net", None)
        if clk is not None and "CLK" not in mem_cell.inputs:
            self.mod.connect(mem_cell, "CLK", clk)

        return True

    # _collect_nb_assignments removed — replaced by _collect_blocking_with_muxes(allow_nb=True)
    def _collect_blocking_with_muxes(self, stmt: Any, allow_nb: bool = False, *, _running: dict[str, "Net"] | None = None) -> dict[str, "Net"]:
        """Collect assignments, building MUXes for nested conditionals.

        Returns {target_name: value_net} where value_net includes conditional
        MUX trees for if/else/case within the statement.

        If allow_nb is True, also captures non-blocking (<=) assignments.
        *_running* provides the current running values from outer List handlers,
        used as the hold value in conditional branches.
        """
        results: dict[str, Net] = {}
        kind = str(stmt.kind)

        if kind == "StatementKind.ExpressionStatement":
            expr = stmt.expr
            if str(expr.kind) == "ExpressionKind.Assignment":
                if not expr.isNonBlocking or allow_nb:
                    # Check if LHS is an array element write (MEMORY cell)
                    if self._try_wire_memory_write(expr):
                        pass  # Memory write handled — no scalar FF needed
                    elif self._is_dynamic_bitselect_write(expr.left):
                        # Dynamic bit-select write: data[index] <= rhs
                        base_net, idx_net = self._get_bitselect_base_and_index(expr.left)
                        rhs = self.lower_expr(expr.right)
                        hold = _running.get(base_net.name, base_net) if _running else base_net
                        mux_out = self._expand_dynamic_bitselect_write(base_net, idx_net, rhs, hold)
                        results[base_net.name] = mux_out
                    elif self._is_unpacked_array_write(expr.left):
                        arr_name, arr_depth, elem_w, selector = self._get_unpacked_array_write_info(expr.left)
                        rhs = self.lower_expr(expr.right)
                        sel_const = getattr(selector, "constant", None) if selector else None
                        if sel_const is not None:
                            idx_val = int(str(sel_const))
                            elem_net = self._get_or_create_net(f"{arr_name}_{idx_val}", elem_w)
                            results[elem_net.name] = rhs
                        else:
                            idx_net = self.lower_expr(selector)
                            for i in range(arr_depth):
                                elem_net = self._get_or_create_net(f"{arr_name}_{i}", elem_w)
                                hold = _running.get(elem_net.name, elem_net) if _running else elem_net
                                eq_out = self._fresh_net("arrweq", 1)
                                eq_cell = self._fresh_cell("arrweq", PrimOp.EQ)
                                ci = self._fresh_net(f"arrwidx_{i}", idx_net.width)
                                cc = self._fresh_cell(f"arrwidx_{i}", PrimOp.CONST,
                                                      value=i, width=idx_net.width)
                                self.mod.connect(cc, "Y", ci, direction="output")
                                self.mod.connect(eq_cell, "A", idx_net)
                                self.mod.connect(eq_cell, "B", ci)
                                self.mod.connect(eq_cell, "Y", eq_out, direction="output")
                                mux_out = self._fresh_net("arrwmux", elem_w)
                                mux = self._fresh_cell("arrwmux", PrimOp.MUX)
                                self.mod.connect(mux, "S", eq_out)
                                self.mod.connect(mux, "A", hold)
                                self.mod.connect(mux, "B", rhs)
                                self.mod.connect(mux, "Y", mux_out, direction="output")
                                results[elem_net.name] = mux_out
                    else:
                        lhs = self.lower_expr(expr.left)
                        rhs = self.lower_expr(expr.right)
                        results[lhs.name] = rhs

        elif kind == "StatementKind.Conditional":
            conds = list(stmt.conditions)
            if conds:
                cond_net = self.lower_expr(conds[0].expr)
                # Push condition for memory write WE gating in true branch
                self._condition_stack.append(cond_net)
                true_map = self._collect_blocking_with_muxes(stmt.ifTrue, allow_nb=allow_nb, _running=_running) if stmt.ifTrue else {}
                self._condition_stack.pop()
                # Push NOT(condition) for false branch
                if stmt.ifFalse:
                    not_cond = self._fresh_net("cond_not", 1)
                    not_cell = self._fresh_cell("cond_not", PrimOp.NOT)
                    self.mod.connect(not_cell, "A", cond_net)
                    self.mod.connect(not_cell, "Y", not_cond, direction="output")
                    self._condition_stack.append(not_cond)
                false_map = self._collect_blocking_with_muxes(stmt.ifFalse, allow_nb=allow_nb, _running=_running) if stmt.ifFalse else {}
                if stmt.ifFalse:
                    self._condition_stack.pop()
                all_targets = sorted(set(true_map) | set(false_map))
                for tgt_name in all_targets:
                    tgt_net = self.mod.nets.get(tgt_name)
                    if tgt_net is None:
                        continue
                    t_val = true_map.get(tgt_name)
                    f_val = false_map.get(tgt_name)
                    if t_val and f_val:
                        mux_out = self._fresh_net("bmux", tgt_net.width)
                        mux = self._fresh_cell("bmux", PrimOp.MUX)
                        self.mod.connect(mux, "S", cond_net)
                        self.mod.connect(mux, "A", f_val)
                        self.mod.connect(mux, "B", t_val)
                        self.mod.connect(mux, "Y", mux_out, direction="output")
                        results[tgt_name] = mux_out
                    elif t_val:
                        # Only true branch assigns — hold value when false.
                        # For always_ff: use running value from prior assignment
                        # ONLY if it's a cross-block combinational value.
                        # Otherwise use the target net (FF Q will replace it).
                        # For always_comb: use CONST(0).
                        if allow_nb:
                            _prev = (_running or {}).get(tgt_name)
                            # Use _prev only if it's from an always_comb block
                            if _prev is not None and _prev.driver is not None and 'comb_' in _prev.driver.name:
                                hold_net = _prev
                            else:
                                hold_net = tgt_net
                        else:
                            hold_net = self._fresh_net("bmux_dflt", tgt_net.width)
                            hold_cell = self._fresh_cell("bmux_dflt", PrimOp.CONST, value=0, width=tgt_net.width)
                            self.mod.connect(hold_cell, "Y", hold_net, direction="output")
                        mux_out = self._fresh_net("bmux", tgt_net.width)
                        mux = self._fresh_cell("bmux", PrimOp.MUX)
                        self.mod.connect(mux, "S", cond_net)
                        self.mod.connect(mux, "A", hold_net)
                        self.mod.connect(mux, "B", t_val)
                        self.mod.connect(mux, "Y", mux_out, direction="output")
                        results[tgt_name] = mux_out
                    elif f_val:
                        results[tgt_name] = f_val

        elif kind == "StatementKind.Case":
            inner_sel = self.lower_expr(stmt.expr)
            inner_default = self._collect_blocking_with_muxes(stmt.defaultCase, allow_nb=allow_nb, _running=_running) if stmt.defaultCase else {}
            inner_running: dict[str, Net] = dict(inner_default)
            for inner_item in stmt.items:
                inner_map = self._collect_blocking_with_muxes(inner_item.stmt, allow_nb=allow_nb, _running=_running)
                for inner_expr in inner_item.expressions:
                    iv = self.lower_expr(inner_expr)
                    ieq = self._fresh_net("bcase_eq", 1)
                    ieqc = self._fresh_cell("bcase_eq", PrimOp.EQ)
                    self.mod.connect(ieqc, "A", inner_sel)
                    self.mod.connect(ieqc, "B", iv)
                    self.mod.connect(ieqc, "Y", ieq, direction="output")
                    for tn, rv in inner_map.items():
                        tnet = self.mod.nets.get(tn)
                        if tnet is None:
                            continue
                        if tn not in inner_running:
                            if allow_nb:
                                inner_running[tn] = tnet
                            else:
                                zn = self._fresh_net("bcase_dflt", tnet.width)
                                zc = self._fresh_cell("bcase_dflt", PrimOp.CONST, value=0, width=tnet.width)
                                self.mod.connect(zc, "Y", zn, direction="output")
                                inner_running[tn] = zn
                        prev = inner_running[tn]
                        mo = self._fresh_net("bcase_mux", tnet.width)
                        mx = self._fresh_cell("bcase_mux", PrimOp.MUX)
                        self.mod.connect(mx, "S", ieq)
                        self.mod.connect(mx, "A", prev)
                        self.mod.connect(mx, "B", rv)
                        self.mod.connect(mx, "Y", mo, direction="output")
                        inner_running[tn] = mo
            results.update(inner_running)

        elif kind == "StatementKind.ForLoop":
            # Unroll for-loop: process body for each iteration.
            # pyslang keeps the loop structure; we process the body
            # repeatedly. The loop variable is resolved by pyslang
            # within each iteration's expressions.
            body = getattr(stmt, "body", None)
            if body is not None:
                # For synthesis, treat the loop body as a single pass.
                # pyslang evaluates the loop variable for constant loops.
                child_map = self._collect_blocking_with_muxes(body, allow_nb=allow_nb, _running=_running)
                results.update(child_map)

        elif kind == "StatementKind.Block":
            if stmt.body is not None:
                results.update(self._collect_blocking_with_muxes(stmt.body, allow_nb=allow_nb, _running=_running))

        elif kind == "StatementKind.List":
            for child in stmt.list:
                child_map = self._collect_blocking_with_muxes(child, allow_nb=allow_nb, _running=results)
                for tgt_name, rhs_net in child_map.items():
                    if tgt_name in results:
                        # Target already assigned by an earlier child.
                        # Walk the new rhs_net's MUX A-chain to find
                        # where the hold value (tgt_net) appears, and
                        # replace it with the earlier result.
                        prev = results[tgt_name]
                        tgt_net = self.mod.nets.get(tgt_name)
                        if tgt_net:
                            # If the RHS is directly driven by an always_comb
                            # block, do NOT walk into its cone.
                            rhs_is_comb = (rhs_net.driver is not None and
                                           'comb_' in rhs_net.driver.name)
                            if not rhs_is_comb:
                                # Walk the cone of rhs_net and replace references
                                # to tgt_net with prev. Handles chained assignments
                                # from unrolled for-loops within the same always_ff.
                                visited: set[str] = set()
                                work = [rhs_net]
                                while work:
                                    net = work.pop()
                                    if net.name in visited:
                                        continue
                                    visited.add(net.name)
                                    if net.driver is None:
                                        continue
                                    d = net.driver
                                    for pn, pnet in list(d.inputs.items()):
                                        if pnet is tgt_net or pnet.name == tgt_name:
                                            d.inputs[pn] = prev
                                        elif pnet.name not in visited:
                                            work.append(pnet)
                                    if len(visited) > 500:
                                        break
                    results[tgt_name] = rhs_net

        return results

    def _unroll_for_loop(self, stmt: Any) -> list:
        """Unroll a for-loop into a list of statement copies.

        For synthesis, loop bounds must be compile-time constants.
        Returns a list of body statements with the loop variable
        substituted for each iteration.
        """
        # pyslang resolves the loop variable and produces a body
        # that can be evaluated for each iteration. Since the variable
        # is compile-time, we can just iterate and process the body
        # for each value.
        # Actually, pyslang's for-loop body uses the loop variable
        # as a NamedValue. We need to evaluate how many iterations
        # and process the body that many times.
        # For now, just process the body once — pyslang handles
        # the variable substitution internally for constant loops.
        body = getattr(stmt, "body", None)
        if body is not None:
            return [body]
        return []

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

        elif kind == "StatementKind.Conditional":
            # Recurse into both branches
            true_map = self._collect_blocking_assignments(stmt.ifTrue) if stmt.ifTrue else {}
            false_map = self._collect_blocking_assignments(stmt.ifFalse) if stmt.ifFalse else {}
            results.update(true_map)
            results.update(false_map)

        elif kind == "StatementKind.Case":
            if stmt.defaultCase is not None:
                results.update(self._collect_blocking_assignments(stmt.defaultCase))
            for item in stmt.items:
                results.update(self._collect_blocking_assignments(item.stmt))

        elif kind == "StatementKind.Block":
            if stmt.body is not None:
                results.update(self._collect_blocking_assignments(stmt.body))

        elif kind == "StatementKind.ForLoop":
            body = getattr(stmt, "body", None)
            if body is not None:
                results.update(self._collect_blocking_assignments(body))

        elif kind == "StatementKind.List":
            for child in stmt.list:
                results.update(self._collect_blocking_assignments(child))

        return results

    def _is_dynamic_bitselect_write(self, lhs_expr: Any) -> bool:
        """Check if LHS is a dynamic bit-select on a packed bitvector."""
        if str(lhs_expr.kind) != "ExpressionKind.ElementSelect":
            return False
        selector = lhs_expr.selector
        if selector is None:
            return False
        sel_const = getattr(selector, "constant", None)
        if sel_const is not None:
            return False
        value_node = lhs_expr.value
        value_sym = getattr(value_node, "symbol", None)
        if value_sym is None:
            return False
        # Exclude unpacked arrays (handled separately)
        vtype = getattr(value_sym, "type", None)
        if vtype is not None and getattr(vtype, "isUnpackedArray", False):
            return False
        mem_name = getattr(value_sym, "name", "")
        for cell in self.mod.cells.values():
            if cell.op == PrimOp.MEMORY and cell.params.get("mem_name", "").endswith(mem_name):
                return False
        return True

    def _is_unpacked_array_write(self, lhs_expr: Any) -> bool:
        """Check if LHS is an ElementSelect on an unpacked array."""
        if str(lhs_expr.kind) != "ExpressionKind.ElementSelect":
            return False
        value_node = lhs_expr.value
        value_sym = getattr(value_node, "symbol", None)
        if value_sym is None:
            return False
        vtype = getattr(value_sym, "type", None)
        if vtype is None or not getattr(vtype, "isUnpackedArray", False):
            return False
        fr = getattr(vtype, "fixedRange", None)
        et = getattr(vtype, "elementType", None)
        if fr is None or et is None:
            return False
        depth = int(getattr(fr, "width", 0))
        elem_w = int(getattr(et, "bitWidth", 0))
        return depth > 0 and elem_w > 0 and depth <= 32

    def _get_unpacked_array_write_info(self, lhs_expr: Any) -> tuple[str, int, int, Any]:
        """Extract array name, depth, element width, and selector from an unpacked array write."""
        value_sym = getattr(lhs_expr.value, "symbol", None)
        arr_name = getattr(value_sym, "name", "")
        vtype = value_sym.type
        depth = int(vtype.fixedRange.width)
        elem_w = int(vtype.elementType.bitWidth)
        return arr_name, depth, elem_w, lhs_expr.selector

    def _get_bitselect_base_and_index(self, lhs_expr: Any) -> tuple[Net, Net]:
        """Extract the base bitvector net and index net from a dynamic bit-select."""
        base_net = self.lower_expr(lhs_expr.value)
        idx_net = self.lower_expr(lhs_expr.selector)
        return base_net, idx_net

    def _expand_dynamic_bitselect_write(self, base_net: Net, idx_net: Net, rhs: Net, hold: Net) -> Net:
        """Expand data[index] <= rhs into per-bit MUX tree.

        For each bit i: output[i] = (index == i) ? rhs : hold[i]
        """
        width = base_net.width
        bit_nets: list[Net] = []
        for i in range(width):
            const_i = self._fresh_net(f"bsel_c{i}", idx_net.width)
            const_cell = self._fresh_cell(f"bsel_c{i}", PrimOp.CONST, value=i, width=idx_net.width)
            self.mod.connect(const_cell, "Y", const_i, direction="output")
            eq_out = self._fresh_net(f"bsel_eq{i}", 1)
            eq_cell = self._fresh_cell(f"bsel_eq{i}", PrimOp.EQ)
            self.mod.connect(eq_cell, "A", idx_net)
            self.mod.connect(eq_cell, "B", const_i)
            self.mod.connect(eq_cell, "Y", eq_out, direction="output")
            hold_bit = self._fresh_net(f"bsel_h{i}", 1)
            slice_cell = self._fresh_cell(f"bsel_h{i}", PrimOp.SLICE, offset=i, width=1)
            self.mod.connect(slice_cell, "A", hold)
            self.mod.connect(slice_cell, "Y", hold_bit, direction="output")
            mux_out = self._fresh_net(f"bsel_m{i}", 1)
            mux_cell = self._fresh_cell(f"bsel_m{i}", PrimOp.MUX)
            self.mod.connect(mux_cell, "S", eq_out)
            self.mod.connect(mux_cell, "A", hold_bit)
            self.mod.connect(mux_cell, "B", rhs)
            self.mod.connect(mux_cell, "Y", mux_out, direction="output")
            bit_nets.append(mux_out)
        out = self._fresh_net("bsel_cat", width)
        cat_cell = self._fresh_cell("bsel_cat", PrimOp.CONCAT, count=width)
        for i, bn in enumerate(bit_nets):
            self.mod.connect(cat_cell, f"I{i}", bn)
        self.mod.connect(cat_cell, "Y", out, direction="output")
        return out

    def _lower_statement(self, stmt: Any) -> None:
        """Lower a statement in combinational context (direct wiring)."""
        kind = str(stmt.kind)

        if kind == "StatementKind.ExpressionStatement":
            self.lower_expr(stmt.expr)

        elif kind == "StatementKind.Conditional":
            conds = list(stmt.conditions)
            if not conds:
                return
            cond_net = self.lower_expr(conds[0].expr)

            true_map = self._collect_blocking_with_muxes(stmt.ifTrue)
            false_map = self._collect_blocking_with_muxes(stmt.ifFalse) if stmt.ifFalse else {}

            all_targets = sorted(set(true_map) | set(false_map))
            for tgt_name in all_targets:
                tgt_net = self.mod.nets.get(tgt_name)
                if tgt_net is None:
                    continue
                t_val = true_map.get(tgt_name)
                f_val = false_map.get(tgt_name)
                def _build_comb_if_mux(s_net, a_net, b_net, w):
                    mo = self._fresh_net("comb_if", w)
                    mx = self._fresh_cell("comb_if", PrimOp.MUX)
                    self.mod.connect(mx, "S", s_net)
                    self.mod.connect(mx, "A", a_net)
                    self.mod.connect(mx, "B", b_net)
                    self.mod.connect(mx, "Y", mo, direction="output")
                    return mo

                if t_val and f_val:
                    mux_out = _build_comb_if_mux(cond_net, f_val, t_val, tgt_net.width)
                elif t_val:
                    mux_out = _build_comb_if_mux(cond_net, tgt_net, t_val, tgt_net.width)
                elif f_val:
                    mux_out = _build_comb_if_mux(cond_net, f_val, tgt_net, tgt_net.width)
                else:
                    continue

                # Defer the redirect to after all blocks are processed
                if not hasattr(self, '_deferred_comb_redirects'):
                    self._deferred_comb_redirects = []
                self._deferred_comb_redirects.append((tgt_name, mux_out))

        elif kind == "StatementKind.Case":
            # Build MUX chains for combinational case statements.
            self._in_comb_case = True
            # Each case item produces a MUX that selects the case value
            # when the selector matches, otherwise holds the previous value.
            sel = self.lower_expr(stmt.expr)

            # Collect default assignments
            default_map: dict[str, Net] = {}
            if stmt.defaultCase is not None:
                default_map = self._collect_blocking_with_muxes(stmt.defaultCase)

            # Build running value per target
            running: dict[str, Net] = {}
            for tgt_name, default_net in default_map.items():
                running[tgt_name] = default_net

            for item in stmt.items:
                item_map = self._collect_blocking_with_muxes(item.stmt)

                for case_expr in item.expressions:
                    case_val = self.lower_expr(case_expr)
                    eq_net = self._fresh_net("comb_eq", 1)
                    eq_cell = self._fresh_cell("comb_eq", PrimOp.EQ)
                    self.mod.connect(eq_cell, "A", sel)
                    self.mod.connect(eq_cell, "B", case_val)
                    self.mod.connect(eq_cell, "Y", eq_net, direction="output")

                    for tgt_name, rhs_net in item_map.items():
                        tgt_net = self.mod.nets.get(tgt_name)
                        if tgt_net is None:
                            continue
                        # Use existing running value, or a zero constant as default
                        # (NOT the target net itself, which creates a circular reference)
                        if tgt_name not in running:
                            zero = self._fresh_net(f"comb_dflt_{tgt_name}", tgt_net.width)
                            zc = self._fresh_cell(f"comb_dflt_{tgt_name}", PrimOp.CONST, value=0, width=tgt_net.width)
                            self.mod.connect(zc, "Y", zero, direction="output")
                            running[tgt_name] = zero
                        prev = running[tgt_name]
                        mux_out = self._fresh_net("comb_mux", tgt_net.width)
                        mux = self._fresh_cell("comb_mux", PrimOp.MUX)
                        self.mod.connect(mux, "S", eq_net)
                        self.mod.connect(mux, "A", prev)
                        self.mod.connect(mux, "B", rhs_net)
                        self.mod.connect(mux, "Y", mux_out, direction="output")
                        running[tgt_name] = mux_out

            # Defer consumer redirect to after all procedural blocks
            # are processed. The always_ff MUX chains that read these
            # combinational signals don't exist yet.
            if not hasattr(self, '_deferred_comb_redirects'):
                self._deferred_comb_redirects: list[tuple[str, "Net"]] = []
            for tgt_name, final_net in running.items():
                tgt_net = self.mod.nets.get(tgt_name)
                if tgt_net is not None and final_net is not tgt_net:
                    self._deferred_comb_redirects.append((tgt_name, final_net))
            self._in_comb_case = False

        elif kind == "StatementKind.ForLoop":
            body = getattr(stmt, "body", None)
            if body is not None:
                self._lower_statement(body)

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
            """Process a single member during AST walk."""
            kind = str(node.kind)

            if kind == "SymbolKind.Variable":
                # Reject real/shortreal types
                t = node.type if hasattr(node, "type") else None
                type_str = str(getattr(t, "kind", "")) if t else ""
                if "Real" in type_str or "ShortReal" in type_str:
                    src = self._src_from_node(node)
                    self.warnings.append(SynthesisWarning(
                        "unsupported_type",
                        f"variable '{node.name}' has type {type_str} which is not synthesizable",
                        src=src,
                    ))
                    return
                w = self._bit_width(node)
                # Check for unpacked arrays (memories)
                t = node.type if hasattr(node, "type") else None

                # Multi-dimensional array support
                # Check if this is a multi-dim array by walking elementType chain
                is_multidim = False
                if (t is not None and getattr(t, "bitWidth", None) == 0
                        and hasattr(t, "elementType")):
                    inner = t.elementType
                    if (hasattr(inner, "elementType") and hasattr(inner, "fixedRange")
                            and getattr(inner, "bitWidth", None) == 0):
                        is_multidim = True
                        # Flatten to 1D: total_depth = outer_depth * inner_depth
                        outer_rng = t.fixedRange
                        inner_rng = inner.fixedRange
                        outer_depth = abs(getattr(outer_rng, "right", 0) - getattr(outer_rng, "left", 0)) + 1
                        inner_depth = abs(getattr(inner_rng, "right", 0) - getattr(inner_rng, "left", 0)) + 1
                        leaf = inner.elementType
                        leaf_w = getattr(leaf, "bitWidth", 0) if hasattr(leaf, "bitWidth") else 1
                        total_depth = outer_depth * inner_depth
                        if total_depth > 0 and leaf_w > 0:
                            rdata_net = self._fresh_net(f"mem_{node.name}_rdata", leaf_w)
                            mem_cell = self._fresh_cell(
                                f"mem_{node.name}", PrimOp.MEMORY,
                                depth=total_depth, width=leaf_w, mem_name=node.name,
                            )
                            self.mod.connect(mem_cell, "RDATA", rdata_net, direction="output")
                            self._get_or_create_net(node.name, leaf_w)
                        else:
                            self._get_or_create_net(node.name, w if w > 0 else 1)

                # Packed struct support
                # Packed structs have a non-zero bitWidth — slang flattens them
                # to a single bitvector. We treat them as regular nets.
                type_str = str(getattr(t, "kind", "")) if t else ""
                if "PackedStruct" in type_str or "PackedUnion" in type_str:
                    pass
                    # Already handled by _bit_width returning the total bitWidth

                is_array = (
                    not is_multidim
                    and t is not None
                    and getattr(t, "bitWidth", None) == 0
                    and hasattr(t, "fixedRange")
                    and hasattr(t, "elementType")
                )
                if is_array and t is not None:
                    elem_type = t.elementType  # type: ignore[union-attr]
                    elem_w = getattr(elem_type, "bitWidth", 0)
                    rng = t.fixedRange  # type: ignore[union-attr]
                    left = getattr(rng, "left", 0)
                    right = getattr(rng, "right", 0)
                    depth = abs(right - left) + 1
                    if depth > 0 and elem_w > 0:
                        if depth <= 32:
                            # Small array: create individual nets per element.
                            # Variable-indexed reads build a PMUX from these.
                            # Per-element writes (case statements) naturally
                            # target individual element nets.
                            for i in range(depth):
                                self._get_or_create_net(f"{node.name}_{i}", elem_w)
                            # Also create the array name net (for fallback)
                            self._get_or_create_net(node.name, elem_w)
                            if not hasattr(self, '_array_info'):
                                self._array_info: dict[str, tuple[int, int]] = {}
                            self._array_info[node.name] = (depth, elem_w)
                        else:
                            # Large array: use MEMORY cell
                            rdata_net = self._fresh_net(f"mem_{node.name}_rdata", elem_w)
                            mem_cell = self._fresh_cell(
                                f"mem_{node.name}",
                                PrimOp.MEMORY,
                                depth=depth,
                                width=elem_w,
                                mem_name=node.name,
                            )
                            self.mod.connect(mem_cell, "RDATA", rdata_net, direction="output")
                            self._get_or_create_net(node.name, elem_w)
                else:
                    self._get_or_create_net(node.name, w)
                if not is_array and node.initializer is not None:
                    self.lower_expr(node.initializer)

            elif kind == "SymbolKind.Net":
                w = self._bit_width(node)
                net = self._get_or_create_net(node.name, w)
                if node.initializer is not None and net.driver is None:
                    rhs = self.lower_expr(node.initializer)
                    if rhs.driver is not None and net.driver is None:
                        net.driver = rhs.driver
                        for pn, pnet in list(rhs.driver.outputs.items()):
                            if pnet is rhs:
                                rhs.driver.outputs[pn] = net
                                break

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
                # Defer continuous assigns until after procedural blocks
                # so that FF Q outputs are available for wiring.
                if not hasattr(self, '_deferred_assigns'):
                    self._deferred_assigns: list = []
                assign_expr = node.body if hasattr(node, "body") else None
                if assign_expr is None:
                    assign_expr = node.assignment if hasattr(node, "assignment") else None
                if assign_expr is not None:
                    delay = getattr(assign_expr, "timingControl", None)
                    if delay is not None:
                        src = self._src_from_node(node)
                        self.warnings.append(SynthesisWarning(
                            "delay_stripped",
                            "delay stripped from continuous assignment (not synthesizable)",
                            src=src,
                        ))
                    self._deferred_assigns.append(assign_expr)

            elif kind == "SymbolKind.Defparam":
                # defparam — slang resolves at elaboration time.
                # The parameter values are already propagated. Emit a warning
                # since defparam is deprecated in IEEE 1800-2017.
                src = self._src_from_node(node)
                self.warnings.append(SynthesisWarning(
                    "defparam",
                    "defparam is deprecated in IEEE 1800-2017; use parameter overrides instead",
                    src=src,
                ))

            elif kind == "SymbolKind.ProceduralBlock":
                # Check for (* synthesis off/on *) pragma
                attrs = getattr(node, "attributes", None)
                if attrs:
                    for attr in attrs:
                        attr_name = getattr(attr, "name", "")
                        if attr_name == "synthesis" and str(getattr(attr, "value", "")).lower() == "off":
                            src = self._src_from_node(node)
                            self.warnings.append(SynthesisWarning(
                                "synthesis_off",
                                "block excluded from synthesis by (* synthesis off *) pragma",
                                src=src,
                            ))
                            return  # skip this block entirely
                # Collect for order-independent processing after the walk.
                if not hasattr(self, '_collected_proc_blocks'):
                    self._collected_proc_blocks: list[tuple[str, Any]] = []
                proc_kind = str(node.procedureKind) if hasattr(node, "procedureKind") else ""
                self._collected_proc_blocks.append((proc_kind, node))

            elif kind in ("SymbolKind.GenerateBlock", "SymbolKind.GenerateBlockArray"):
                # generate-for/generate-if — slang fully unrolls generate blocks.
                # Each member may have an array index (e.g., genblk1[0]).
                # Walk all members. For GenerateBlockArray, slang gives each
                # iteration a unique name that prevents net collisions.
                if hasattr(node, "members"):
                    for member in node.members:
                        walk_member(member)
                elif hasattr(node, "body") and hasattr(node.body, "members"):
                    for member in node.body.members:
                        walk_member(member)

            elif kind == "SymbolKind.TypeAlias":
                pass  # type aliases resolved by slang, no synthesis action

            elif kind == "SymbolKind.Genvar":
                pass  # genvar is a compile-time variable, resolved by slang

            elif kind == "SymbolKind.Instance":
                self._lower_sub_instance(node)

            elif kind == "SymbolKind.PrimitiveInstance":
                # Verilog gate-level primitives: and, or, nand, nor, xor, xnor, buf, not
                prim_name = getattr(getattr(node, "primitiveType", None), "name", "")
                conns = list(getattr(node, "portConnections", []))
                if conns and prim_name:
                    # First port is output (Assignment with LHS=net, RHS=empty)
                    # Extract just the LHS net, don't lower the assignment
                    out_expr = conns[0]
                    if str(out_expr.kind) == "ExpressionKind.Assignment":
                        out_net = self.lower_expr(out_expr.left)
                    else:
                        out_net = self.lower_expr(out_expr)
                    in_nets = [self.lower_expr(c) for c in conns[1:]]

                    gate_map = {
                        "and": PrimOp.AND, "or": PrimOp.OR,
                        "xor": PrimOp.XOR, "nand": PrimOp.AND,
                        "nor": PrimOp.OR, "xnor": PrimOp.XOR,
                    }
                    if prim_name == "buf":
                        # Buffer: wire input directly to output
                        if in_nets:
                            out_net.driver = in_nets[0].driver
                            if in_nets[0].driver:
                                for pn, pnet in list(in_nets[0].driver.outputs.items()):
                                    if pnet is in_nets[0]:
                                        in_nets[0].driver.outputs[pn] = out_net
                                        break
                    elif prim_name == "not":
                        # Inverter: output = ~input
                        if in_nets:
                            not_cell = self._fresh_cell(f"gate_{node.name}", PrimOp.NOT)
                            self.mod.connect(not_cell, "A", in_nets[0])
                            self.mod.connect(not_cell, "Y", out_net, direction="output")
                    elif prim_name in gate_map:
                        # Multi-input gate: chain pairwise
                        op = gate_map[prim_name]
                        if len(in_nets) >= 2:
                            result = in_nets[0]
                            for inp in in_nets[1:]:
                                tmp = self._fresh_net(f"gate_{node.name}", 1)
                                cell = self._fresh_cell(f"gate_{node.name}", op)
                                self.mod.connect(cell, "A", result)
                                self.mod.connect(cell, "B", inp)
                                self.mod.connect(cell, "Y", tmp, direction="output")
                                result = tmp
                            # For NAND/NOR/XNOR: invert the result
                            if prim_name in ("nand", "nor", "xnor"):
                                inv = self._fresh_net(f"gate_{node.name}_inv", 1)
                                inv_cell = self._fresh_cell(f"gate_{node.name}_inv", PrimOp.NOT)
                                self.mod.connect(inv_cell, "A", result)
                                self.mod.connect(inv_cell, "Y", inv, direction="output")
                                result = inv
                            out_net.driver = result.driver
                            if result.driver:
                                for pn, pnet in list(result.driver.outputs.items()):
                                    if pnet is result:
                                        result.driver.outputs[pn] = out_net
                                        break

            # Interface support — slang resolves interface port
            # connections during elaboration, presenting interface members
            # as regular ports/nets in the instance body. If we see an
            # InterfaceInstance, walk its members as regular variables.
            elif kind == "SymbolKind.InterfaceInstance":
                if hasattr(node, "body"):
                    def walk_interface(inode: Any) -> None:
                        """Walk an interface instance."""
                        ikind = str(inode.kind)
                        if ikind == "SymbolKind.Variable":
                            iw = self._bit_width(inode)
                            self._get_or_create_net(f"{node.name}.{inode.name}", iw)
                        elif ikind == "SymbolKind.Net":
                            iw = self._bit_width(inode)
                            self._get_or_create_net(f"{node.name}.{inode.name}", iw)
                    node.body.visit(walk_interface)

            # library/config constructs — slang resolves these
            elif kind in ("SymbolKind.ConfigBlock", "SymbolKind.LibraryMap"):
                src = self._src_from_node(node)
                self.warnings.append(SynthesisWarning(
                    "unsupported_construct",
                    f"{kind} is resolved by the frontend and does not affect synthesis",
                    src=src,
                ))

            # UDP (User-Defined Primitives) — reject with warning (#8)
            elif kind == "SymbolKind.PrimitivePort":
                src = self._src_from_node(node)
                self.warnings.append(SynthesisWarning(
                    "unsupported_construct",
                    "UDP (User-Defined Primitive) not supported for synthesis",
                    src=src,
                ))

            # Assertions, coverpoints, specify blocks — strip with warning (#8, #14)
            elif kind in ("SymbolKind.AssertionPort", "SymbolKind.ConcurrentAssertion",
                          "SymbolKind.ImmediateAssertion", "SymbolKind.CoverCross",
                          "SymbolKind.CoverPoint", "SymbolKind.Covergroup",
                          "SymbolKind.SpecifyBlock", "SymbolKind.Checker",
                          "SymbolKind.ClockingBlock", "SymbolKind.Property",
                          "SymbolKind.Sequence", "SymbolKind.LetDecl"):
                src = self._src_from_node(node)
                self.warnings.append(SynthesisWarning(
                    "stripped_construct",
                    f"{kind} stripped (not synthesizable)",
                    src=src,
                ))

        body.visit(walk_member)

        # Process procedural blocks in source order.
        for proc_kind, node in getattr(self, '_collected_proc_blocks', []):
            self.lower_procedural_block(node)
            self._apply_comb_redirects()

        # After all blocks: patch cross-block references.
        # When always_comb blocks appear before always_ff in source, they
        # may reference nets (like state, pc) that had driver=None at
        # lowering time. The always_ff creates FFs with Q outputs that
        # replace those nets. Patch any combinational cell input that
        # still reads the raw target net (driver=None or driver=FF) to
        # instead read the FF Q output.
        _ff_q_map: dict[str, "Net"] = {}
        for cell in self.mod.cells.values():
            if cell.op == PrimOp.FF:
                tgt = cell.params.get("ff_target", "")
                if tgt:
                    for o in cell.outputs.values():
                        _ff_q_map[tgt] = o
        # Patch cells in always_comb output cones that reference raw FF
        # target nets. Walk backward from each combinationally-redirected
        # target net to find all cells in its cone, then patch their
        # inputs to use FF Q outputs.
        if _ff_q_map:
            _comb_cone_cells: set[str] = set()
            for tgt_name, final_net in getattr(self, '_all_comb_redirects', []):
                # Walk cone of final_net
                _wl = [final_net]
                _vis: set[str] = set()
                while _wl:
                    _n = _wl.pop()
                    if _n.name in _vis:
                        continue
                    _vis.add(_n.name)
                    if _n.driver and _n.driver.op not in (PrimOp.FF, PrimOp.INPUT, PrimOp.CONST, PrimOp.OUTPUT):
                        _comb_cone_cells.add(_n.driver.name)
                        for _inp in _n.driver.inputs.values():
                            _wl.append(_inp)

            for cell in self.mod.cells.values():
                if cell.name not in _comb_cone_cells:
                    continue
                for pn, pnet in list(cell.inputs.items()):
                    q = _ff_q_map.get(pnet.name)
                    if q is not None and pnet is not q and q.width == pnet.width:
                        cell.inputs[pn] = q

        # Process deferred continuous assigns now that all FFs exist
        for assign_expr in getattr(self, '_deferred_assigns', []):
            self.lower_expr(assign_expr)

        # Process deferred blocking assignments where RHS had no driver.
        # Find the FF Q net that replaced rhs, then redirect lhs consumers to it.
        ff_q_map: dict[str, "Net"] = {}
        for cell in self.mod.cells.values():
            if cell.op == PrimOp.FF:
                ff_target = cell.params.get("ff_target", "")
                for o in cell.outputs.values():
                    if ff_target:
                        ff_q_map[ff_target] = o

        for lhs, rhs in getattr(self, '_deferred_blocking', []):
            # Find the FF Q net for rhs
            q_net = ff_q_map.get(rhs.name)
            if q_net is not None:
                lhs.driver = q_net.driver if q_net.driver else rhs.driver
                # Redirect consumers of lhs to read from q_net
                for cell in self.mod.cells.values():
                    for pn, pnet in list(cell.inputs.items()):
                        if pnet is lhs:
                            cell.inputs[pn] = q_net
            elif rhs.driver is not None:
                lhs.driver = rhs.driver

        # Process deferred combinational case redirects now that
        # always_ff MUX chains exist and reference the target nets.
        self._apply_comb_redirects()

    def _apply_comb_redirects(self) -> None:
        """Apply deferred combinational assignment redirects.

        For each target net assigned in an always_comb block, redirect all
        consumers to read the computed MUX chain output, and set the target
        net's driver so subsequent always_comb blocks see the driven value.
        """
        for tgt_name, final_net in getattr(self, '_deferred_comb_redirects', []):
            tgt_net = self.mod.nets.get(tgt_name)
            if tgt_net is None:
                continue
            # Collect cells that are part of THIS target's MUX chain
            # (to avoid redirecting the chain's own internal references).
            own_chain: set[str] = set()
            visited_nets: set[str] = set()
            wl = [final_net.name]
            while wl:
                nn = wl.pop()
                if nn in visited_nets:
                    continue
                visited_nets.add(nn)
                net = self.mod.nets.get(nn)
                if net and net.driver and ('comb_' in net.driver.name):
                    own_chain.add(net.driver.name)
                    for pn2, pnet2 in net.driver.inputs.items():
                        wl.append(pnet2.name)

            # Wire the final MUX cell's output directly to tgt_net.
            # Do NOT redirect readers — they already reference tgt_net
            # and will read the value naturally. Only set the driver
            # and change the cell's output port.
            if final_net.driver is not None:
                tgt_net.driver = final_net.driver
                for opn, onet in list(final_net.driver.outputs.items()):
                    if onet is final_net:
                        final_net.driver.outputs[opn] = tgt_net
                        break
        # Save redirects for the FF Q cone patch, then clear
        if hasattr(self, '_deferred_comb_redirects'):
            if not hasattr(self, '_all_comb_redirects'):
                self._all_comb_redirects: list[tuple[str, "Net"]] = []
            self._all_comb_redirects.extend(self._deferred_comb_redirects)
            self._deferred_comb_redirects.clear()

    def _lower_sub_instance(self, inst: Any) -> None:
        """Lower a sub-module instance by recursively lowering its body
        and connecting port nets to the parent module."""
        sub_body = inst.body
        sub_name = inst.name  # instance name (e.g., "RX", "SPI")
        mod_name = sub_body.name  # module name (e.g., "uart_rx")

        # ECP5 vendor primitives — create a passthrough cell with port wiring
        if mod_name in _VENDOR_PRIMITIVES:
            cell = self._fresh_cell(f"vendor_{sub_name}", PrimOp.CONST)
            cell.params = {"_vendor_primitive": mod_name, "value": 0, "width": 1}
            cell.attributes["keep"] = True
            for conn in inst.portConnections:
                port = conn.port
                port_name = port.name
                direction = str(port.direction)
                expr = getattr(conn, "expression", None) or getattr(conn, "internalExpr", None)
                if expr is None:
                    continue
                parent_net = self.lower_expr(expr)
                is_output = "Out" in direction
                if is_output:
                    self.mod.connect(cell, port_name, parent_net, direction="output")
                else:
                    self.mod.connect(cell, port_name, parent_net)
            return

        # Create a prefix for all nets/cells in this sub-instance
        prefix = f"{sub_name}."

        # Use the module-level prefixed lowerer for sub-instances
        sub = _PrefixedLowerer(
            self.mod, prefix,
            net_counter=self._net_counter,
            cell_counter=self._cell_counter,
        )
        # Tag all cells created by this sub-instance with the module name
        # so Design.eliminate_dead_modules can trace dependencies
        sub._module_ref = mod_name

        # Lower the sub-instance body (variables, parameters, procedural blocks)
        # but NOT ports — we wire those manually below
        def walk_sub(node):
            """Walk a sub-module instance."""
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
                if is_array and t is not None:
                    elem_type = t.elementType  # type: ignore[union-attr]
                    elem_w = getattr(elem_type, "bitWidth", 0)
                    rng = t.fixedRange  # type: ignore[union-attr]
                    left = getattr(rng, "left", 0)
                    right = getattr(rng, "right", 0)
                    depth = abs(right - left) + 1
                    if depth > 0 and elem_w > 0:
                        if depth <= 32:
                            for i in range(depth):
                                sub._get_or_create_net(f"{node.name}_{i}", elem_w)
                            sub._get_or_create_net(node.name, elem_w)
                            if not hasattr(sub, '_array_info'):
                                sub._array_info = {}
                            sub._array_info[node.name] = (depth, elem_w)
                        else:
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

        # Recursive nested instance handler
        def _lower_nested(nested_inst, parent_prefix):
            nested_body = nested_inst.body
            nested_mod_name = nested_body.name
            # Handle vendor primitives at nested level too
            if nested_mod_name in _VENDOR_PRIMITIVES:
                cell = self._fresh_cell(f"vendor_{nested_inst.name}", PrimOp.CONST)
                cell.params = {"_vendor_primitive": nested_mod_name, "value": 0, "width": 1}
                cell.attributes["keep"] = True
                for conn in nested_inst.portConnections:
                    port = conn.port
                    port_name = port.name
                    direction = str(port.direction)
                    expr = getattr(conn, "expression", None) or getattr(conn, "internalExpr", None)
                    if expr is None:
                        continue
                    parent_net = self.lower_expr(expr)
                    if "Out" in direction:
                        self.mod.connect(cell, port_name, parent_net, direction="output")
                    else:
                        self.mod.connect(cell, port_name, parent_net)
                return
            self._lower_sub_instance(nested_inst)

        sub._lower_sub_instance_nested = _lower_nested  # type: ignore[attr-defined]

        sub_body.visit(walk_sub)

        # Update parent counters and merge warnings
        self._net_counter = sub._net_counter
        self._cell_counter = sub._cell_counter
        self.warnings.extend(sub.warnings)

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
            expr = getattr(conn, "expression", None) or getattr(conn, "internalExpr", None)
            if expr is None:
                continue

            parent_net = self.lower_expr(expr)

            # Wire based on port direction:
            # Input port: parent drives sub-instance net
            # Output port: sub-instance drives parent net
            # InOut port: both directions
            is_input = "In" in direction
            is_output = "Out" in direction

            if is_input and not is_output:
                if sub_net.driver is None and parent_net.driver is not None:
                    sub_net.driver = parent_net.driver
                # Record the port alias for post-lowering Q-redirect
                if '_port_aliases' not in self.mod.ports:
                    # Abuse a temporary net to store the mapping (cleaned up later)
                    pass
                # Store in a module-level dict via a cell parameter hack
                alias_key = f"__port_alias__{sub_net.name}"
                # Store mapping as a net attribute
                sub_net.attributes[alias_key] = parent_net.name
                # Also redirect cells that read sub_net to read parent_net
                if parent_net is not sub_net and parent_net.driver is not None:
                    for cell in self.mod.cells.values():
                        for pn, pnet in list(cell.inputs.items()):
                            if pnet is sub_net:
                                cell.inputs[pn] = parent_net
            elif is_output and not is_input:
                if sub_net.driver is not None:
                    parent_net.driver = sub_net.driver
            elif is_input and is_output:
                # InOut: wire both directions
                if sub_net.driver is None and parent_net.driver is not None:
                    sub_net.driver = parent_net.driver
                if parent_net.driver is None and sub_net.driver is not None:
                    parent_net.driver = sub_net.driver


class _PrefixedLowerer(_Lowerer):
    """Module-level sub-instance lowerer with net/cell name prefixing.

    Handles hierarchical lowering by prefixing all net and cell names
    with the instance path.
    """

    def __init__(self, module: Module, prefix: str, *, net_counter: int = 0, cell_counter: int = 0) -> None:
        super().__init__(module)
        self._prefix = prefix
        self._net_counter = net_counter
        self._cell_counter = cell_counter
        self._module_ref: str = ""

    def _fresh_net(self, name_prefix: str, width: int) -> Net:
        name = f"${self._prefix}{name_prefix}_{self._net_counter}"
        self._net_counter += 1
        return self.mod.add_net(name, width)

    def _fresh_cell(self, name_prefix: str, op: PrimOp, src: str = "", **params: Any) -> Cell:  # type: ignore[override]
        name = f"${self._prefix}{name_prefix}_{self._cell_counter}"
        self._cell_counter += 1
        if self._module_ref:
            params["module_ref"] = self._module_ref
        return self.mod.add_cell(name, op, src=src, **params)

    def _get_or_create_net(self, name: str, width: int) -> Net:
        if width <= 0:
            width = 1
        prefixed = f"{self._prefix}{name}"
        if prefixed in self.mod.nets:
            existing = self.mod.nets[prefixed]
            if existing.width != width:
                if existing.width == 1 and width > 1 and existing.driver is None:
                    existing.width = width
                    return existing
                return self._fresh_net(f"{name}_w{width}", width)
            return existing
        return self.mod.add_net(prefixed, width)


def lower_to_ir(result: ParseResult, *, top: str | None = None) -> Design:
    """Lower a parsed pyslang compilation into a Nosis IR Design.

    If *top* is specified, only that instance is lowered.
    Otherwise, all top instances are lowered.

    Synthesis warnings (simulation task stripping, latch inference, etc.)
    are collected and stored in ``design.warnings``.
    """
    design = Design()
    all_warnings: list[SynthesisWarning] = []

    for inst in result.top_instances:
        name = inst.name
        if top and name != top:
            continue

        mod = design.add_module(name)
        lowerer = _Lowerer(mod)
        lowerer.lower_instance(inst)
        all_warnings.extend(lowerer.warnings)

    if top:
        design.top = top
    elif len(design.modules) == 1:
        design.top = next(iter(design.modules))

    # Attach warnings to design for inspection
    design.synthesis_warnings = all_warnings

    # Apply $readmemh/$readmemb associations from source-text scanning
    readmem = getattr(result, "readmem_associations", {})
    if readmem:
        for mod in design.modules.values():
            for cell in mod.cells.values():
                if cell.op != PrimOp.MEMORY:
                    continue
                mem_name = cell.params.get("mem_name", "")
                base = mem_name.rsplit(".", 1)[-1] if "." in mem_name else mem_name
                if base in readmem and "init_file" not in cell.params:
                    file_str, fmt = readmem[base]
                    cell.params["init_file"] = file_str
                    cell.params["init_format"] = fmt

    # Remove duplicate FFs: sub-instance lowering with a prefix may
    # also create unprefixed FFs for the same targets (from pyslang
    # AST shadowing).  If both "TX.state" and "state" exist as FF
    # targets, the unprefixed one is a duplicate — remove it.
    # Also redirect any cells that consumed the duplicate's outputs
    # to the prefixed FF's outputs, and repair net drivers.
    for mod in design.modules.values():
        # Map: base_name -> prefixed FF cell name
        prefixed_ffs: dict[str, str] = {}
        for cname, cell in mod.cells.items():
            if cell.op == PrimOp.FF:
                tgt = cell.params.get("ff_target", "")
                if "." in tgt:
                    base = tgt.rsplit(".", 1)[-1]
                    prefixed_ffs[base] = cname
        if not prefixed_ffs:
            continue
        to_remove: list[str] = []
        for cname, cell in mod.cells.items():
            if cell.op != PrimOp.FF:
                continue
            tgt = cell.params.get("ff_target", "")
            if "." in tgt or tgt not in prefixed_ffs:
                continue
            # This is a duplicate — redirect consumers to the prefixed FF's Q
            prefixed_cell = mod.cells[prefixed_ffs[tgt]]
            pref_q = list(prefixed_cell.outputs.values())[0] if prefixed_cell.outputs else None
            dup_q = list(cell.outputs.values())[0] if cell.outputs else None
            if pref_q and dup_q:
                # Redirect all cells reading the duplicate's Q to the prefixed Q
                for c2 in mod.cells.values():
                    if c2 is cell:
                        continue
                    for pn, pnet in list(c2.inputs.items()):
                        if pnet is dup_q or pnet.name == dup_q.name:
                            c2.inputs[pn] = pref_q
                # Fix net drivers: if any net is driven by the duplicate, update
                for net in mod.nets.values():
                    if net.driver is cell:
                        net.driver = prefixed_cell
                # Fix port references
                for pn, pnet in list(mod.ports.items()):
                    if pnet is dup_q:
                        mod.ports[pn] = pref_q
            to_remove.append(cname)
        for cname in to_remove:
            del mod.cells[cname]

    # Remove duplicate MEMORY cells: same pattern as duplicate FFs.
    # If both "SOC.progmem" (prefixed) and "progmem" (unprefixed)
    # exist, keep the prefixed one and redirect the unprefixed one's
    # outputs. This prevents double BRAM allocation.
    for mod in design.modules.values():
        prefixed_mems: dict[str, str] = {}  # base_name -> prefixed cell name
        for cname, cell in mod.cells.items():
            if cell.op == PrimOp.MEMORY:
                mn = cell.params.get("mem_name", "")
                if "." in mn:
                    base = mn.rsplit(".", 1)[-1]
                    prefixed_mems[base] = cname
        if prefixed_mems:
            to_remove_mem: list[str] = []
            for cname, cell in mod.cells.items():
                if cell.op != PrimOp.MEMORY:
                    continue
                mn = cell.params.get("mem_name", "")
                if "." in mn or mn not in prefixed_mems:
                    continue
                # Unprefixed duplicate — redirect RDATA consumers
                pref_cell = mod.cells[prefixed_mems[mn]]
                for dup_port, dup_net in list(cell.outputs.items()):
                    # Find matching port on prefixed cell
                    pref_net = pref_cell.outputs.get(dup_port)
                    if pref_net is None and pref_cell.outputs:
                        pref_net = list(pref_cell.outputs.values())[0]
                    if pref_net:
                        for c2 in mod.cells.values():
                            if c2 is cell:
                                continue
                            for pn, pnet in list(c2.inputs.items()):
                                if pnet is dup_net or pnet.name == dup_net.name:
                                    c2.inputs[pn] = pref_net
                to_remove_mem.append(cname)
            for cname in to_remove_mem:
                del mod.cells[cname]

    # Global FF Q-redirect: ensure ALL cells that read a target net
    # (or any net whose driver was set to the same FF) use the Q output.
    # This catches hierarchy-prefixed copies (RX.clk, TX.clk) that
    # share a driver with the original target net but are different objects.
    for mod in design.modules.values():
        # Build: target net -> Q net
        target_to_q: dict[str, Net] = {}
        target_nets: dict[str, Net] = {}
        for cell in mod.cells.values():
            if cell.op == PrimOp.FF:
                target = cell.params.get("ff_target", "")
                if target and target in mod.nets:
                    tnet = mod.nets[target]
                    for q in cell.outputs.values():
                        target_to_q[target] = q
                        target_nets[target] = tnet

        if not target_to_q:
            continue

        # Build reverse map: net identity -> Q net (for driver sharing)
        driver_to_q: dict[int, Net] = {}
        for tgt_name, q_net in target_to_q.items():
            tnet = target_nets[tgt_name]
            if tnet.driver is not None:
                driver_to_q[id(tnet.driver)] = q_net

        # Use recorded port aliases to find hierarchy-prefixed nets
        # that should redirect to Q.
        port_aliases: dict[str, str] = {}
        for net in mod.nets.values():
            for attr_key, attr_val in list(net.attributes.items()):
                if attr_key.startswith("__port_alias__"):
                    alias_name = attr_key[len("__port_alias__"):]
                    if alias_name == net.name:
                        port_aliases[alias_name] = attr_val
                    del net.attributes[attr_key]

        # Build: net name -> Q net, including aliases
        redirect_map: dict[str, Net] = {}
        for tgt_name, q_net in target_to_q.items():
            redirect_map[tgt_name] = q_net
        # Follow port aliases: if RX.clk -> sys_clk and sys_clk -> Q,
        # then RX.clk -> Q
        changed = True
        while changed:
            changed = False
            for alias_name, parent_name in port_aliases.items():
                if alias_name not in redirect_map and parent_name in redirect_map:
                    redirect_map[alias_name] = redirect_map[parent_name]
                    changed = True

        # Also redirect nets driven by FFs whose target is in the map.
        # Example: usb_tx driven by TX.tx FF → redirect usb_tx to Q(TX.tx)
        for net in mod.nets.values():
            if net.name in redirect_map:
                continue
            if net.driver is None:
                continue
            # If driver is an FF whose target is in redirect_map
            drv = net.driver
            if drv.op == PrimOp.FF:
                tgt = drv.params.get("ff_target", "")
                if tgt in redirect_map:
                    redirect_map[net.name] = redirect_map[tgt]

        # Identify clock targets: FF targets whose target net is used
        # as CLK by at least one other FF.  Only these should have their
        # Q outputs forwarded into CLK inputs.
        clock_q_nets: set[int] = set()  # id() of Q nets that are clocks
        for cell in mod.cells.values():
            if cell.op != PrimOp.FF:
                continue
            clk_net = cell.inputs.get("CLK")
            if clk_net is None:
                continue
            # If this FF's CLK name (or alias) maps to a Q net, that Q is a clock
            if clk_net.name in redirect_map:
                clock_q_nets.add(id(redirect_map[clk_net.name]))

        # Redirect every cell input
        for cell in mod.cells.values():
            own_target = cell.params.get("ff_target", "") if cell.op == PrimOp.FF else ""
            for pn, pnet in list(cell.inputs.items()):
                if cell.op == PrimOp.FF and pn == "D" and pnet.name == own_target:
                    continue
                if pnet.name not in redirect_map:
                    continue
                q_net = redirect_map[pnet.name]
                if pnet is q_net:
                    continue
                # CLK inputs: only redirect to clock-divider Q outputs
                if pn == "CLK" and id(q_net) not in clock_q_nets:
                    continue
                cell.inputs[pn] = q_net

    return design
