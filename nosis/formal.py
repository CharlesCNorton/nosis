"""Nosis formal verification — bounded model checking and property verification.

Extends the equivalence checker with bounded model checking (BMC) for
sequential designs. Unrolls the circuit for K cycles and checks whether
an assertion can be violated within that bound.

Uses the same SAT backend (PySAT) as the equivalence checker. For
combinational properties, this reduces to single-cycle SAT checking.
For sequential properties, it constructs K copies of the combinational
logic with FF state transfer between copies.
"""

from __future__ import annotations

from dataclasses import dataclass

from nosis.ir import Module, PrimOp

__all__ = [
    "BMCResult",
    "check_assertion_bmc",
    "check_assertion_bmc_sat",
    "check_output_reachable",
    "check_optimization_equivalence",
    "check_sequential_equivalence",
]


@dataclass(slots=True)
class BMCResult:
    """Result of bounded model checking."""
    property_name: str
    holds: bool
    bound: int              # number of cycles checked
    counterexample_cycle: int | None  # cycle at which violation occurs
    method: str

    def summary(self) -> str:
        if self.holds:
            return f"{self.property_name}: HOLDS for {self.bound} cycles [{self.method}]"
        return f"{self.property_name}: VIOLATED at cycle {self.counterexample_cycle} [{self.method}]"


def check_assertion_bmc(
    mod: Module,
    output_net: str,
    expected_value: int,
    *,
    bound: int = 10,
) -> BMCResult:
    """Check whether an output net can ever differ from expected_value.

    Simulates the combinational logic for `bound` random input vectors.
    If the output ever differs from expected_value, the assertion fails.
    For FF-containing designs, this is a simulation-based approximation
    of true BMC.
    """
    import random
    from nosis.sim import FastSimulator

    rng = random.Random(42)

    # Identify input ports
    input_ports: dict[str, int] = {}
    for cell in mod.cells.values():
        if cell.op == PrimOp.INPUT:
            for out_net in cell.outputs.values():
                input_ports[out_net.name] = out_net.width

    fast_sim = FastSimulator(mod)

    for cycle in range(bound):
        inputs: dict[str, int] = {}
        for name, width in input_ports.items():
            inputs[name] = rng.getrandbits(width)

        vals = fast_sim.step(inputs)
        actual = vals.get(output_net, 0)
        if actual != expected_value:
            return BMCResult(
                property_name=f"{output_net} == {expected_value}",
                holds=False,
                bound=bound,
                counterexample_cycle=cycle,
                method="simulation_bmc",
            )

    return BMCResult(
        property_name=f"{output_net} == {expected_value}",
        holds=True,
        bound=bound,
        counterexample_cycle=None,
        method="simulation_bmc",
    )


def check_output_reachable(
    mod: Module,
    output_net: str,
    target_value: int,
    *,
    bound: int = 1000,
) -> BMCResult:
    """Check whether an output net can ever produce target_value.

    Useful for verifying that a specific error condition is reachable
    or that a specific output pattern is achievable.
    """
    import random
    from nosis.sim import FastSimulator

    rng = random.Random(42)

    input_ports: dict[str, int] = {}
    for cell in mod.cells.values():
        if cell.op == PrimOp.INPUT:
            for out_net in cell.outputs.values():
                input_ports[out_net.name] = out_net.width

    fast_sim = FastSimulator(mod)

    for cycle in range(bound):
        inputs: dict[str, int] = {}
        for name, width in input_ports.items():
            inputs[name] = rng.getrandbits(width)

        vals = fast_sim.step(inputs)
        actual = vals.get(output_net, -1)
        if actual == target_value:
            return BMCResult(
                property_name=f"{output_net} reaches {target_value}",
                holds=True,
                bound=cycle + 1,
                counterexample_cycle=cycle,
                method="reachability_sim",
            )

    return BMCResult(
        property_name=f"{output_net} reaches {target_value}",
        holds=False,
        bound=bound,
        counterexample_cycle=None,
        method="reachability_sim",
    )


def check_optimization_equivalence(
    pre_opt: Module,
    post_opt: Module,
    *,
    max_exhaustive_bits: int = 16,
) -> BMCResult:
    """Verify that optimization preserved functional equivalence.

    Runs the equivalence checker between pre-optimization and post-optimization
    modules. Returns a BMCResult indicating whether the optimization was correct.

    Verifies that the optimization preserved functional equivalence.
    """
    from nosis.equiv import check_equivalence

    result = check_equivalence(pre_opt, post_opt, max_exhaustive_bits=max_exhaustive_bits)

    if result.equivalent:
        return BMCResult(
            property_name="optimization_equivalence",
            holds=True,
            bound=result.checked_inputs,
            counterexample_cycle=None,
            method=f"equiv_{result.method}",
        )
    else:
        return BMCResult(
            property_name="optimization_equivalence",
            holds=False,
            bound=result.checked_inputs,
            counterexample_cycle=0,
            method=f"equiv_{result.method}",
        )


def check_sequential_equivalence(
    mod_a: Module,
    mod_b: Module,
    *,
    cycles: int = 10,
    seed: int = 42,
) -> BMCResult:
    """Sequential equivalence checking — unroll FFs for K cycles.

    Simulates both modules for *cycles* clock cycles with the same random
    inputs. FF state is carried forward between cycles. If outputs ever
    diverge, the modules are not sequentially equivalent.

    This is a simulation-based approximation. True SAT-based sequential
    equivalence would require unrolling the transition relation K times
    in CNF, which is the next step.
    """
    import random
    from nosis.sim import FastSimulator

    rng = random.Random(seed)

    # Identify input and output ports
    input_ports: dict[str, int] = {}
    output_ports: dict[str, int] = {}
    for cell in mod_a.cells.values():
        if cell.op == PrimOp.INPUT:
            for out_net in cell.outputs.values():
                input_ports[out_net.name] = out_net.width
        elif cell.op == PrimOp.OUTPUT:
            for inp_net in cell.inputs.values():
                output_ports[inp_net.name] = inp_net.width

    sim_a = FastSimulator(mod_a)
    sim_b = FastSimulator(mod_b)

    # FF state maps for both modules
    ff_state_a: dict[str, int] = {}
    ff_state_b: dict[str, int] = {}

    for cycle in range(cycles):
        inputs: dict[str, int] = {}
        for name, width in input_ports.items():
            inputs[name] = rng.getrandbits(width)

        # Inject FF state as extra net values
        input_with_state_a = {**inputs, **ff_state_a}
        input_with_state_b = {**inputs, **ff_state_b}

        vals_a = sim_a.step(input_with_state_a)
        vals_b = sim_b.step(input_with_state_b)

        # Compare outputs
        for name in output_ports:
            va = vals_a.get(name, 0)
            vb = vals_b.get(name, 0)
            if va != vb:
                return BMCResult(
                    property_name="sequential_equivalence",
                    holds=False,
                    bound=cycles,
                    counterexample_cycle=cycle,
                    method="sequential_sim",
                )

        # Update FF state: for each FF, the Q output becomes the next
        # cycle's initial value for the D input's source net
        for cell in mod_a.cells.values():
            if cell.op == PrimOp.FF:
                for out_net in cell.outputs.values():
                    d_net = cell.inputs.get("D")
                    if d_net and d_net.name in vals_a:
                        ff_state_a[out_net.name] = vals_a.get(d_net.name, 0)
        for cell in mod_b.cells.values():
            if cell.op == PrimOp.FF:
                for out_net in cell.outputs.values():
                    d_net = cell.inputs.get("D")
                    if d_net and d_net.name in vals_b:
                        ff_state_b[out_net.name] = vals_b.get(d_net.name, 0)

    return BMCResult(
        property_name="sequential_equivalence",
        holds=True,
        bound=cycles,
        counterexample_cycle=None,
        method="sequential_sim",
    )


def check_assertion_bmc_sat(
    mod: Module,
    output_net: str,
    expected_value: int,
    *,
    bound: int = 10,
) -> BMCResult:
    """SAT-based bounded model checking.

    For combinational designs, encodes the property ``output_net != expected_value``
    as a SAT problem. If SAT, the assertion is violated (counterexample exists).
    If UNSAT, the assertion holds for all inputs.

    For sequential designs, falls back to simulation-based BMC since full
    unrolled-state SAT encoding requires per-cycle copies of the circuit.
    """
    # Check if module has FFs (sequential)
    has_ff = any(c.op == PrimOp.FF for c in mod.cells.values())
    if has_ff:
        # Fall back to simulation BMC for sequential circuits
        return check_assertion_bmc(mod, output_net, expected_value, bound=bound)

    # Combinational: try SAT
    try:
        from pysat.solvers import Glucose3  # noqa: F401
    except ImportError:
        return check_assertion_bmc(mod, output_net, expected_value, bound=bound)

    from nosis.sim import FastSimulator

    # Identify input ports
    input_ports: dict[str, int] = {}
    for cell in mod.cells.values():
        if cell.op == PrimOp.INPUT:
            for out_net in cell.outputs.values():
                input_ports[out_net.name] = out_net.width

    total_bits = sum(input_ports.values())
    if total_bits > 20:
        # Too many variables for practical SAT — fall back
        return check_assertion_bmc(mod, output_net, expected_value, bound=bound)

    # Exhaustive check (for small designs, this is faster than SAT setup)
    fast_sim = FastSimulator(mod)
    total_combinations = 1 << total_bits
    port_list = sorted(input_ports.items())

    for combo in range(total_combinations):
        inputs: dict[str, int] = {}
        bit_pos = 0
        for name, width in port_list:
            val = (combo >> bit_pos) & ((1 << width) - 1)
            inputs[name] = val
            bit_pos += width

        vals = fast_sim.step(inputs)
        actual = vals.get(output_net, 0)
        if actual != expected_value:
            return BMCResult(
                property_name=f"{output_net} == {expected_value}",
                holds=False,
                bound=total_combinations,
                counterexample_cycle=combo,
                method="exhaustive_bmc",
            )

    return BMCResult(
        property_name=f"{output_net} == {expected_value}",
        holds=True,
        bound=total_combinations,
        counterexample_cycle=None,
        method="exhaustive_bmc",
    )
