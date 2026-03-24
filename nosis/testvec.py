"""Nosis automatic test vector generation from design port constraints.

Generates test vectors that exercise corner cases based on port widths,
reset patterns, and boundary values rather than purely random inputs.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from nosis.ir import Module, PrimOp

__all__ = [
    "TestVector",
    "generate_test_vectors",
]


@dataclass(slots=True)
class TestVector:
    cycle: int
    inputs: dict[str, int]
    description: str = ""


def generate_test_vectors(
    mod: Module,
    *,
    num_random: int = 50,
    seed: int = 42,
) -> list[TestVector]:
    """Generate test vectors covering corner cases and random values.

    Produces vectors in this order:
      1. All-zeros (reset state)
      2. All-ones (saturation)
      3. Each input one-hot (single-bit activation)
      4. Each input max value (overflow boundary)
      5. Walking ones on each input
      6. Random vectors
    """
    import random
    rng = random.Random(seed)

    # Identify input ports
    input_ports: dict[str, int] = {}  # name -> width
    for cell in mod.cells.values():
        if cell.op == PrimOp.INPUT:
            port_name = str(cell.params.get("port_name", ""))
            for out_net in cell.outputs.values():
                if port_name:
                    input_ports[port_name] = out_net.width
                else:
                    input_ports[out_net.name] = out_net.width

    if not input_ports:
        return []

    vectors: list[TestVector] = []
    cycle = 0

    # 1. All zeros
    vectors.append(TestVector(
        cycle=cycle,
        inputs={name: 0 for name in input_ports},
        description="all_zeros",
    ))
    cycle += 1

    # 2. All ones
    vectors.append(TestVector(
        cycle=cycle,
        inputs={name: (1 << w) - 1 for name, w in input_ports.items()},
        description="all_ones",
    ))
    cycle += 1

    # 3. Each input one-hot (others zero)
    for target_name, target_width in input_ports.items():
        if target_width <= 1:
            vectors.append(TestVector(
                cycle=cycle,
                inputs={name: (1 if name == target_name else 0) for name in input_ports},
                description=f"onehot_{target_name}",
            ))
            cycle += 1
        else:
            for bit in range(min(target_width, 8)):
                vectors.append(TestVector(
                    cycle=cycle,
                    inputs={
                        name: ((1 << bit) if name == target_name else 0)
                        for name in input_ports
                    },
                    description=f"onehot_{target_name}_bit{bit}",
                ))
                cycle += 1

    # 4. Each input at max (others zero)
    for target_name, target_width in input_ports.items():
        vectors.append(TestVector(
            cycle=cycle,
            inputs={
                name: ((1 << target_width) - 1 if name == target_name else 0)
                for name in input_ports
            },
            description=f"max_{target_name}",
        ))
        cycle += 1

    # 5. Walking ones
    for target_name, target_width in input_ports.items():
        if target_width > 1:
            for shift in range(min(target_width, 8)):
                vectors.append(TestVector(
                    cycle=cycle,
                    inputs={
                        name: ((1 << shift) if name == target_name else 0)
                        for name in input_ports
                    },
                    description=f"walk_{target_name}_{shift}",
                ))
                cycle += 1

    # 6. Random
    for _ in range(num_random):
        vectors.append(TestVector(
            cycle=cycle,
            inputs={name: rng.getrandbits(w) for name, w in input_ports.items()},
            description="random",
        ))
        cycle += 1

    return vectors
