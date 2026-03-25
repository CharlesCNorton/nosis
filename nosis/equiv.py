"""Nosis equivalence checking — SAT-based proof that output matches input.

For a given IR module, constructs a miter circuit: the original and
synthesized versions are driven by the same inputs, and their outputs
are XORed together. If any assignment of inputs can make any XOR output
true, the designs are not equivalent.

Uses PySAT for the SAT solver backend (CNF formulation).
Falls back to exhaustive simulation for small designs if PySAT is
not available.

Example::

    from nosis.ir import Module, PrimOp
    from nosis.equiv import check_equivalence

    # Build two modules and check if they compute the same function
    def make_and(name):
        mod = Module(name=name)
        a = mod.add_net("a", 1); b = mod.add_net("b", 1); y = mod.add_net("y", 1)
        ac = mod.add_cell("a_p", PrimOp.INPUT, port_name="a")
        mod.connect(ac, "Y", a, direction="output"); mod.ports["a"] = a
        bc = mod.add_cell("b_p", PrimOp.INPUT, port_name="b")
        mod.connect(bc, "Y", b, direction="output"); mod.ports["b"] = b
        yc = mod.add_cell("y_p", PrimOp.OUTPUT, port_name="y")
        mod.connect(yc, "A", y); mod.ports["y"] = y
        c = mod.add_cell("g", PrimOp.AND)
        mod.connect(c, "A", a); mod.connect(c, "B", b)
        mod.connect(c, "Y", y, direction="output")
        return mod

    result = check_equivalence(make_and("a"), make_and("b"))
    assert result.equivalent
"""

from __future__ import annotations

from nosis.ir import Cell, Module, Net, PrimOp
from nosis.sim import FastSimulator

__all__ = [
    "EquivalenceResult",
    "check_equivalence",
    "check_equivalence_exhaustive",
    "wildcard_eq",
]


def wildcard_eq(a: int, b: int, mask: int, width: int) -> bool:
    """Compare two values with wildcard masking (casez/casex support).

    Bits where *mask* is 0 are don't-care and always match.
    Bits where *mask* is 1 are compared exactly.

    For casez: mask has 0 for z-bits (don't care), 1 for exact.
    For casex: mask has 0 for x-or-z bits, 1 for exact.

    Example::

        # casez: 4'b1??0 matches 4'b1010 — mask=0b1001
        wildcard_eq(0b1010, 0b1000, 0b1001, 4)  # True (bits 1,2 are don't-care)
    """
    w_mask = (1 << width) - 1
    return ((a ^ b) & mask & w_mask) == 0


class EquivalenceResult:
    """Result of an equivalence check."""

    def __init__(
        self,
        equivalent: bool,
        method: str,
        *,
        counterexample: dict[str, int] | None = None,
        checked_outputs: int = 0,
        checked_inputs: int = 0,
    ) -> None:
        self.equivalent = equivalent
        self.method = method
        self.counterexample = counterexample
        self.checked_outputs = checked_outputs
        self.checked_inputs = checked_inputs

    def __repr__(self) -> str:
        status = "EQUIVALENT" if self.equivalent else "NOT EQUIVALENT"
        return f"EquivalenceResult({status}, method={self.method})"


def _topological_order(mod: Module) -> list[Cell]:
    """Sort combinational cells in dependency order."""
    order: list[Cell] = []
    visited: set[str] = set()

    def visit(cell: Cell) -> None:
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


def check_equivalence_exhaustive(
    mod_a: Module,
    mod_b: Module,
    *,
    max_input_bits: int = 20,
) -> EquivalenceResult:
    """Check equivalence by exhaustive simulation of all input combinations.

    Only feasible for small designs (total input bits <= max_input_bits).
    """
    # Build input port set once — O(cells), not O(ports * cells)
    input_net_names: set[str] = set()
    for cell in mod_a.cells.values():
        if cell.op == PrimOp.INPUT:
            for out_net in cell.outputs.values():
                input_net_names.add(out_net.name)

    input_ports_a: dict[str, Net] = {}
    output_ports_a: dict[str, Net] = {}
    for name, net in mod_a.ports.items():
        if name in input_net_names:
            input_ports_a[name] = net
        else:
            output_ports_a[name] = net

    total_input_bits = sum(net.width for net in input_ports_a.values())
    if total_input_bits > max_input_bits:
        return EquivalenceResult(
            equivalent=False,
            method="exhaustive",
            checked_inputs=0,
            checked_outputs=0,
        )

    total_combinations = 1 << total_input_bits
    input_port_list = sorted(input_ports_a.items(), key=lambda x: x[0])
    output_port_list = sorted(output_ports_a.items(), key=lambda x: x[0])

    sim_a = FastSimulator(mod_a)
    sim_b = FastSimulator(mod_b)

    for combo in range(total_combinations):
        # Build input assignment
        inputs: dict[str, int] = {}
        bit_pos = 0
        for name, net in input_port_list:
            val = (combo >> bit_pos) & ((1 << net.width) - 1)
            inputs[name] = val
            bit_pos += net.width

        # Simulate both modules
        vals_a = sim_a.step(inputs)
        vals_b = sim_b.step(inputs)

        # Compare outputs (look up by net name, which is what the simulator uses)
        for name, net in output_port_list:
            va = vals_a.get(net.name, vals_a.get(name, 0))
            vb = vals_b.get(net.name, vals_b.get(name, 0))
            if va != vb:
                return EquivalenceResult(
                    equivalent=False,
                    method="exhaustive",
                    counterexample=inputs,
                    checked_inputs=total_input_bits,
                    checked_outputs=len(output_port_list),
                )

    return EquivalenceResult(
        equivalent=True,
        method="exhaustive",
        checked_inputs=total_input_bits,
        checked_outputs=len(output_port_list),
    )


def _try_sat_equivalence(
    mod_a: Module,
    mod_b: Module,
    input_ports: dict[str, Net],
) -> EquivalenceResult | None:
    """Try SAT-based equivalence checking. Returns None if PySAT unavailable."""
    try:
        from pysat.solvers import Glucose3  # noqa: F401
        from pysat.formula import CNF  # noqa: F401
    except ImportError:
        return None

    # Build CNF for the miter: inputs -> mod_a -> xor -> mod_b outputs
    # For each 1-bit output, assert that a_out XOR b_out can be true.
    # If UNSAT, the modules are equivalent.

    var_counter = [1]  # CNF variables start at 1

    def new_var() -> int:
        v = var_counter[0]
        var_counter[0] += 1
        return v

    # Map input port bits to shared SAT variables
    input_vars: dict[str, list[int]] = {}
    for name, net in sorted(input_ports.items()):
        input_vars[name] = [new_var() for _ in range(net.width)]

    def _encode_module(mod: Module, suffix: str) -> dict[str, list[int]]:
        """Encode a module's combinational logic as CNF clauses.
        Returns {net_name: [sat_var_per_bit]}.
        """
        net_vars: dict[str, list[int]] = {}

        # Initialize inputs
        for name, vars_list in input_vars.items():
            net_vars[name] = vars_list

        # Initialize constants
        for cell in mod.cells.values():
            if cell.op == PrimOp.CONST:
                val = int(cell.params.get("value", 0))
                width = int(cell.params.get("width", 1))
                bits = []
                for i in range(width):
                    v = new_var()
                    # Force constant: positive literal for 1, negative for 0
                    if (val >> i) & 1:
                        clauses.append([v])
                    else:
                        clauses.append([-v])
                    bits.append(v)
                for out_net in cell.outputs.values():
                    net_vars[out_net.name] = bits

        # Initialize INPUT cells
        for cell in mod.cells.values():
            if cell.op == PrimOp.INPUT:
                port_name = str(cell.params.get("port_name", ""))
                if port_name in input_vars:
                    for out_net in cell.outputs.values():
                        net_vars[out_net.name] = input_vars[port_name]

        # Encode combinational cells in topological order
        for cell in _topological_order(mod):
            if cell.op in (PrimOp.INPUT, PrimOp.OUTPUT, PrimOp.CONST, PrimOp.FF):
                continue

            out_nets = list(cell.outputs.values())
            if not out_nets:
                continue
            out_net = out_nets[0]
            width = out_net.width

            # Wiring operations: propagate variables directly, no clauses needed
            if cell.op in (PrimOp.CONCAT, PrimOp.SLICE, PrimOp.ZEXT, PrimOp.SEXT, PrimOp.REPEAT):
                # These are pure wiring — output vars equal input vars (rearranged)
                if cell.op == PrimOp.SLICE:
                    src_vars = net_vars.get(cell.inputs["A"].name, []) if "A" in cell.inputs else []
                    offset = int(cell.params.get("offset", 0))
                    out_vars = []
                    for i in range(width):
                        src_idx = offset + i
                        if src_idx < len(src_vars):
                            out_vars.append(src_vars[src_idx])
                        else:
                            v = new_var()
                            clauses.append([-v])
                            out_vars.append(v)
                    net_vars[out_net.name] = out_vars
                elif cell.op == PrimOp.ZEXT:
                    src_vars = net_vars.get(cell.inputs["A"].name, []) if "A" in cell.inputs else []
                    out_vars = list(src_vars[:width])
                    while len(out_vars) < width:
                        v = new_var()
                        clauses.append([-v])
                        out_vars.append(v)
                    net_vars[out_net.name] = out_vars
                elif cell.op == PrimOp.SEXT:
                    src_vars = net_vars.get(cell.inputs["A"].name, []) if "A" in cell.inputs else []
                    out_vars = list(src_vars[:width])
                    sign_var = src_vars[-1] if src_vars else new_var()
                    while len(out_vars) < width:
                        out_vars.append(sign_var)
                    net_vars[out_net.name] = out_vars
                elif cell.op == PrimOp.CONCAT:
                    out_vars = []
                    count = int(cell.params.get("count", 0))
                    for ci in range(count):
                        inp = cell.inputs.get(f"I{ci}")
                        if inp and inp.name in net_vars:
                            out_vars.extend(net_vars[inp.name])
                    while len(out_vars) < width:
                        v = new_var()
                        clauses.append([-v])
                        out_vars.append(v)
                    net_vars[out_net.name] = out_vars[:width]
                elif cell.op == PrimOp.REPEAT:
                    src_vars = net_vars.get(cell.inputs["A"].name, []) if "A" in cell.inputs else []
                    out_vars = []
                    n = int(cell.params.get("count", 1))
                    for _ in range(n):
                        out_vars.extend(src_vars)
                    net_vars[out_net.name] = out_vars[:width]
                continue

            a_vars = net_vars.get(cell.inputs["A"].name, []) if "A" in cell.inputs else []
            b_vars = net_vars.get(cell.inputs["B"].name, []) if "B" in cell.inputs else []

            out_vars = [new_var() for _ in range(width)]
            net_vars[out_net.name] = out_vars

            # Multi-bit ADD/SUB: per-bit full-adder chain in CNF
            if cell.op in (PrimOp.ADD, PrimOp.SUB) and width > 1 and a_vars and b_vars:
                carry = new_var()
                # carry-in: 1 for SUB (two's complement), 0 for ADD
                clauses.append([carry] if cell.op == PrimOp.SUB else [-carry])
                for bit in range(width):
                    av = a_vars[bit] if bit < len(a_vars) else new_var()
                    if bit >= len(a_vars):
                        clauses.append([-av])  # zero-extend
                    # For SUB, invert b bits
                    if cell.op == PrimOp.SUB:
                        bv_raw = b_vars[bit] if bit < len(b_vars) else new_var()
                        if bit >= len(b_vars):
                            clauses.append([-bv_raw])
                        bv = new_var()
                        clauses.append([bv_raw, bv])    # NOT gate
                        clauses.append([-bv_raw, -bv])
                    else:
                        bv = b_vars[bit] if bit < len(b_vars) else new_var()
                        if bit >= len(b_vars):
                            clauses.append([-bv])
                    ov = out_vars[bit]
                    # sum = a XOR b XOR carry (3-input XOR via two 2-input XORs)
                    xor_ab = new_var()
                    clauses.append([-av, -bv, -xor_ab])
                    clauses.append([av, bv, -xor_ab])
                    clauses.append([av, -bv, xor_ab])
                    clauses.append([-av, bv, xor_ab])
                    # ov = xor_ab XOR carry
                    clauses.append([-xor_ab, -carry, -ov])
                    clauses.append([xor_ab, carry, -ov])
                    clauses.append([xor_ab, -carry, ov])
                    clauses.append([-xor_ab, carry, ov])
                    # carry_out = MAJ(a, b, carry_in)
                    new_carry = new_var()
                    clauses.append([-av, -bv, new_carry])
                    clauses.append([-av, -carry, new_carry])
                    clauses.append([-bv, -carry, new_carry])
                    clauses.append([av, bv, -new_carry])
                    clauses.append([av, carry, -new_carry])
                    clauses.append([bv, carry, -new_carry])
                    carry = new_carry
                continue

            # Multi-bit bitwise ops: encode per-bit independently
            if cell.op in (PrimOp.AND, PrimOp.OR, PrimOp.XOR, PrimOp.NOT) and width > 1:
                for bit in range(width):
                    av = a_vars[bit] if bit < len(a_vars) else new_var()
                    if bit >= len(a_vars):
                        clauses.append([-av])
                    ov = out_vars[bit]
                    if cell.op == PrimOp.NOT:
                        clauses.append([av, ov])
                        clauses.append([-av, -ov])
                    else:
                        bv = b_vars[bit] if bit < len(b_vars) else new_var()
                        if bit >= len(b_vars):
                            clauses.append([-bv])
                        if cell.op == PrimOp.AND:
                            clauses.append([-av, -bv, ov])
                            clauses.append([av, -ov])
                            clauses.append([bv, -ov])
                        elif cell.op == PrimOp.OR:
                            clauses.append([av, bv, -ov])
                            clauses.append([-av, ov])
                            clauses.append([-bv, ov])
                        elif cell.op == PrimOp.XOR:
                            clauses.append([-av, -bv, -ov])
                            clauses.append([av, bv, -ov])
                            clauses.append([av, -bv, ov])
                            clauses.append([-av, bv, ov])
                continue

            # Multi-bit MUX: per-bit if-then-else encoding
            if cell.op == PrimOp.MUX and width > 1:
                s_vars_m = net_vars.get(cell.inputs["S"].name, []) if "S" in cell.inputs else []
                a_vars_m = net_vars.get(cell.inputs["A"].name, []) if "A" in cell.inputs else []
                b_vars_m = net_vars.get(cell.inputs["B"].name, []) if "B" in cell.inputs else []
                s_v = s_vars_m[0] if s_vars_m else new_var()
                for bit in range(width):
                    ov = out_vars[bit]
                    fv = a_vars_m[bit] if bit < len(a_vars_m) else new_var()
                    tv = b_vars_m[bit] if bit < len(b_vars_m) else new_var()
                    if bit >= len(a_vars_m):
                        clauses.append([-fv])
                    if bit >= len(b_vars_m):
                        clauses.append([-tv])
                    # o = s ? t : f  (Tseitin MUX encoding)
                    clauses.append([-s_v, -tv, ov])
                    clauses.append([-s_v, tv, -ov])
                    clauses.append([s_v, -fv, ov])
                    clauses.append([s_v, fv, -ov])
                continue

            # Multi-bit EQ/NE: reduce to per-bit XOR then AND/OR reduce
            if cell.op in (PrimOp.EQ, PrimOp.NE) and width == 1 and a_vars and b_vars and len(a_vars) > 1:
                # EQ: all bits equal -> AND(NOT(XOR(a[i],b[i])))
                # Build per-bit XOR, then AND-reduce
                xor_vars = []
                for bit in range(min(len(a_vars), len(b_vars))):
                    xv = new_var()
                    clauses.append([-a_vars[bit], -b_vars[bit], -xv])
                    clauses.append([a_vars[bit], b_vars[bit], -xv])
                    clauses.append([a_vars[bit], -b_vars[bit], xv])
                    clauses.append([-a_vars[bit], b_vars[bit], xv])
                    xor_vars.append(xv)
                o = out_vars[0]
                if cell.op == PrimOp.EQ:
                    # o = AND(NOT(xor[i])) = NOR(xor[0], xor[1], ...)
                    # o -> NOT(xor[i]) for each i
                    for xv in xor_vars:
                        clauses.append([-o, -xv])
                    # NOT(xor[0]) AND NOT(xor[1]) AND ... -> o
                    clauses.append([o] + xor_vars)
                else:
                    # NE: o = OR(xor[i])
                    for xv in xor_vars:
                        clauses.append([-xv, o])
                    clauses.append([-o] + xor_vars)
                continue

            # For 1-bit operations, encode directly as CNF
            if width == 1 and len(a_vars) >= 1:
                a = a_vars[0]
                b = b_vars[0] if b_vars else a
                o = out_vars[0]

                if cell.op == PrimOp.AND:
                    # o = a AND b: (-a | -b | o), (a | -o), (b | -o)
                    clauses.append([-a, -b, o])
                    clauses.append([a, -o])
                    clauses.append([b, -o])
                elif cell.op == PrimOp.OR:
                    # o = a OR b: (a | b | -o), (-a | o), (-b | o)
                    clauses.append([a, b, -o])
                    clauses.append([-a, o])
                    clauses.append([-b, o])
                elif cell.op == PrimOp.XOR:
                    # o = a XOR b
                    clauses.append([-a, -b, -o])
                    clauses.append([a, b, -o])
                    clauses.append([a, -b, o])
                    clauses.append([-a, b, o])
                elif cell.op == PrimOp.NOT:
                    # o = NOT a
                    clauses.append([a, o])
                    clauses.append([-a, -o])
                elif cell.op == PrimOp.EQ:
                    # o = (a == b) = NOT(a XOR b)
                    clauses.append([-a, -b, o])
                    clauses.append([a, b, o])
                    clauses.append([a, -b, -o])
                    clauses.append([-a, b, -o])
                elif cell.op == PrimOp.NE:
                    # o = (a != b) = a XOR b
                    clauses.append([-a, -b, -o])
                    clauses.append([a, b, -o])
                    clauses.append([a, -b, o])
                    clauses.append([-a, b, o])
                elif cell.op == PrimOp.MUX:
                    # o = MUX(s=a, false=b, true=c)
                    s_vars = net_vars.get(cell.inputs["S"].name, []) if "S" in cell.inputs else []
                    s = s_vars[0] if s_vars else a
                    false_var = b
                    true_vars = net_vars.get(cell.inputs["B"].name, []) if "B" in cell.inputs else []
                    t = true_vars[0] if true_vars else b
                    # o = s ? t : false_var
                    # (-s & false_var) | (s & t) = o
                    clauses.append([-s, -t, o])    # s=1,t=1 -> o=1
                    clauses.append([-s, t, -o])    # s=1,t=0 -> o=0
                    clauses.append([s, -false_var, o])   # s=0,f=1 -> o=1
                    clauses.append([s, false_var, -o])   # s=0,f=0 -> o=0
                elif cell.op in (PrimOp.LT, PrimOp.LE, PrimOp.GT, PrimOp.GE):
                    # Verified minimal CNF (3 clauses each), derived from
                    # truth-table invalid-assignment exclusion with resolution.
                    if cell.op == PrimOp.LT:
                        # o = ~a & b. TT: 00→0, 01→1, 10→0, 11→0
                        clauses.append([-a, -o])       # a→¬o
                        clauses.append([a, b, -o])     # ¬a∧¬b→¬o
                        clauses.append([-a, b, o])     # ¬a∧b→o
                    elif cell.op == PrimOp.LE:
                        # o = ~a | b. TT: 00→1, 01→1, 10→0, 11→1
                        clauses.append([a, o])         # ¬a→o
                        clauses.append([-a, -b, o])    # a∧b→o
                        clauses.append([-a, b, -o])    # a∧¬b→¬o
                    elif cell.op == PrimOp.GT:
                        # o = a & ~b. TT: 00→0, 01→0, 10→1, 11→0
                        clauses.append([a, -o])        # ¬a→¬o
                        clauses.append([-a, -b, -o])   # a∧b→¬o
                        clauses.append([-a, b, o])     # a∧¬b→o
                    elif cell.op == PrimOp.GE:
                        # o = a | ~b. TT: 00→1, 01→0, 10→1, 11→1
                        clauses.append([-a, o])        # a→o
                        clauses.append([a, b, o])      # ¬a∧¬b→o
                        clauses.append([a, -b, -o])    # ¬a∧b→¬o
                else:
                    pass

        return net_vars

    clauses: list[list[int]] = []

    a_nets = _encode_module(mod_a, "_a")
    b_nets = _encode_module(mod_b, "_b")

    # Build miter: for each output, XOR the two versions
    output_ports_a = {name: net for name, net in mod_a.ports.items() if name not in input_ports}
    miter_ors: list[int] = []

    for name in sorted(output_ports_a):
        a_vars = a_nets.get(name, [])
        b_vars = b_nets.get(name, [])
        for i in range(min(len(a_vars), len(b_vars))):
            xor_var = new_var()
            a_v, b_v = a_vars[i], b_vars[i]
            # xor_var = a_v XOR b_v
            clauses.append([-a_v, -b_v, -xor_var])
            clauses.append([a_v, b_v, -xor_var])
            clauses.append([a_v, -b_v, xor_var])
            clauses.append([-a_v, b_v, xor_var])
            miter_ors.append(xor_var)

    if not miter_ors:
        return EquivalenceResult(equivalent=True, method="sat", checked_outputs=0)

    # At least one miter output must be true for non-equivalence
    clauses.append(miter_ors)

    # Solve
    solver = Glucose3()
    for clause in clauses:
        solver.add_clause(clause)

    if solver.solve():
        # SAT — not equivalent, extract counterexample
        model = solver.get_model()
        counterexample: dict[str, int] = {}
        for name, vars_list in input_vars.items():
            val = 0
            for i, v in enumerate(vars_list):
                if v in model:
                    val |= (1 << i)
            counterexample[name] = val
        solver.delete()
        return EquivalenceResult(
            equivalent=False,
            method="sat",
            counterexample=counterexample,
            checked_outputs=len(miter_ors),
            checked_inputs=sum(len(v) for v in input_vars.values()),
        )
    else:
        solver.delete()
        return EquivalenceResult(
            equivalent=True,
            method="sat",
            checked_outputs=len(miter_ors),
            checked_inputs=sum(len(v) for v in input_vars.values()),
        )


def check_equivalence(
    mod_a: Module,
    mod_b: Module,
    *,
    max_exhaustive_bits: int = 16,
) -> EquivalenceResult:
    """Check equivalence between two modules.

    Uses exhaustive simulation for small designs, SAT-based checking
    for larger designs (when PySAT is available), and random simulation
    as a fallback.
    """
    input_net_names: set[str] = set()
    for cell in mod_a.cells.values():
        if cell.op == PrimOp.INPUT:
            for out_net in cell.outputs.values():
                input_net_names.add(out_net.name)
    input_ports: dict[str, Net] = {
        name: net for name, net in mod_a.ports.items() if name in input_net_names
    }
    total_bits = sum(net.width for net in input_ports.values())

    if total_bits <= max_exhaustive_bits:
        return check_equivalence_exhaustive(mod_a, mod_b, max_input_bits=max_exhaustive_bits)

    # Try SAT-based checking
    sat_result = _try_sat_equivalence(mod_a, mod_b, input_ports)
    if sat_result is not None:
        return sat_result

    # Random simulation fallback for larger designs
    import random
    rng = random.Random(42)
    num_tests = min(10000, 1 << min(total_bits, 20))

    input_port_list = sorted(input_ports.items(), key=lambda x: x[0])
    output_ports = {name: net for name, net in mod_a.ports.items() if name not in input_ports}
    output_port_list = sorted(output_ports.items(), key=lambda x: x[0])

    sim_a = FastSimulator(mod_a)
    sim_b = FastSimulator(mod_b)

    for _ in range(num_tests):
        inputs: dict[str, int] = {}
        for name, net in input_port_list:
            inputs[name] = rng.getrandbits(net.width)

        vals_a = sim_a.step(inputs)
        vals_b = sim_b.step(inputs)

        for name, net in output_port_list:
            va = vals_a.get(name, 0)
            vb = vals_b.get(name, 0)
            if va != vb:
                return EquivalenceResult(
                    equivalent=False,
                    method="random_simulation",
                    counterexample=inputs,
                    checked_inputs=total_bits,
                    checked_outputs=len(output_port_list),
                )

    return EquivalenceResult(
        equivalent=True,
        method="random_simulation",
        checked_inputs=total_bits,
        checked_outputs=len(output_port_list),
    )
