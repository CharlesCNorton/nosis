"""Nosis equivalence checking — SAT-based proof that output matches input.

For a given IR module, constructs a miter circuit: the original and
synthesized versions are driven by the same inputs, and their outputs
are XORed together. If any assignment of inputs can make any XOR output
true, the designs are not equivalent.

Uses PySAT for the SAT solver backend (CNF formulation).
Falls back to exhaustive simulation for small designs if PySAT is
not available.
"""

from __future__ import annotations

from nosis.eval import eval_cell as _shared_eval_cell
from nosis.ir import Cell, Module, Net, PrimOp

__all__ = [
    "EquivalenceResult",
    "check_equivalence",
    "check_equivalence_exhaustive",
]


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


def _eval_cell(cell: Cell, input_values: dict[str, int]) -> dict[str, int]:
    """Evaluate a single combinational cell given input net values.

    Delegates to the shared evaluator in nosis.eval.
    Returns {output_port_name: value}.
    """
    return _shared_eval_cell(cell, input_values)


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


def _simulate_combinational(
    mod: Module,
    input_values: dict[str, int],
) -> dict[str, int]:
    """Simulate the combinational logic of a module for one set of inputs.

    Returns {net_name: value} for all nets.
    """
    net_values: dict[str, int] = dict(input_values)

    # Set INPUT cell outputs from the provided input values
    for cell in mod.cells.values():
        if cell.op == PrimOp.INPUT:
            port_name = str(cell.params.get("port_name", ""))
            val = input_values.get(port_name, 0)
            for out_net in cell.outputs.values():
                net_values[out_net.name] = val

    # Set constants
    for cell in mod.cells.values():
        if cell.op == PrimOp.CONST:
            results = _eval_cell(cell, net_values)
            for pname, val in results.items():
                out_net = cell.outputs.get(pname)
                if out_net:
                    net_values[out_net.name] = val

    # Propagate through combinational logic in topological order
    for cell in _topological_order(mod):
        if cell.op in (PrimOp.INPUT, PrimOp.OUTPUT, PrimOp.CONST):
            continue  # already handled above
        results = _eval_cell(cell, net_values)
        for pname, val in results.items():
            out_net = cell.outputs.get(pname)
            if out_net:
                net_values[out_net.name] = val

    return net_values


def check_equivalence_exhaustive(
    mod_a: Module,
    mod_b: Module,
    *,
    max_input_bits: int = 20,
) -> EquivalenceResult:
    """Check equivalence by exhaustive simulation of all input combinations.

    Only feasible for small designs (total input bits <= max_input_bits).
    """
    # Identify input and output ports by checking which cells drive/consume them
    input_ports_a: dict[str, Net] = {}
    output_ports_a: dict[str, Net] = {}
    for name, net in mod_a.ports.items():
        is_input = False
        for cell in mod_a.cells.values():
            if cell.op == PrimOp.INPUT:
                for out_net in cell.outputs.values():
                    if out_net.name == name:
                        is_input = True
                        break
            if is_input:
                break
        if is_input:
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

    for combo in range(total_combinations):
        # Build input assignment
        inputs: dict[str, int] = {}
        bit_pos = 0
        for name, net in input_port_list:
            val = (combo >> bit_pos) & ((1 << net.width) - 1)
            inputs[name] = val
            bit_pos += net.width

        # Simulate both modules
        vals_a = _simulate_combinational(mod_a, inputs)
        vals_b = _simulate_combinational(mod_b, inputs)

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
        from pysat.solvers import Glucose3
        from pysat.formula import CNF
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

            a_vars = net_vars.get(cell.inputs["A"].name, []) if "A" in cell.inputs else []
            b_vars = net_vars.get(cell.inputs["B"].name, []) if "B" in cell.inputs else []

            out_vars = [new_var() for _ in range(width)]
            net_vars[out_net.name] = out_vars

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
                else:
                    # Unsupported op — leave unconstrained
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
    input_ports: dict[str, Net] = {}
    for name, net in mod_a.ports.items():
        for cell in mod_a.cells.values():
            if cell.op == PrimOp.INPUT:
                for out_net in cell.outputs.values():
                    if out_net.name == name:
                        input_ports[name] = net
                        break
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

    for _ in range(num_tests):
        inputs: dict[str, int] = {}
        for name, net in input_port_list:
            inputs[name] = rng.getrandbits(net.width)

        vals_a = _simulate_combinational(mod_a, inputs)
        vals_b = _simulate_combinational(mod_b, inputs)

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
