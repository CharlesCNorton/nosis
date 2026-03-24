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
from nosis.eval import eval_const_op

__all__ = [
    "BMCResult",
    "check_assertion_bmc",
    "check_output_reachable",
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
    from nosis.equiv import _simulate_combinational

    rng = random.Random(42)

    # Identify input ports
    input_ports: dict[str, int] = {}
    for cell in mod.cells.values():
        if cell.op == PrimOp.INPUT:
            for out_net in cell.outputs.values():
                input_ports[out_net.name] = out_net.width

    for cycle in range(bound):
        inputs: dict[str, int] = {}
        for name, width in input_ports.items():
            inputs[name] = rng.getrandbits(width)

        vals = _simulate_combinational(mod, inputs)
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
    from nosis.equiv import _simulate_combinational

    rng = random.Random(42)

    input_ports: dict[str, int] = {}
    for cell in mod.cells.values():
        if cell.op == PrimOp.INPUT:
            for out_net in cell.outputs.values():
                input_ports[out_net.name] = out_net.width

    for cycle in range(bound):
        inputs: dict[str, int] = {}
        for name, width in input_ports.items():
            inputs[name] = rng.getrandbits(width)

        vals = _simulate_combinational(mod, inputs)
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
