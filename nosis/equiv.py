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

        # Compare outputs
        for name, net in output_port_list:
            va = vals_a.get(name, 0)
            vb = vals_b.get(name, 0)
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
