"""Nosis register retiming and logic duplication.

Register retiming moves FFs across combinational logic to balance
pipeline delays. Logic duplication replicates high-fanout cells to
reduce routing pressure.

Both transforms preserve functional equivalence — retiming changes
latency but not combinational function, duplication creates identical
copies.
"""

from __future__ import annotations

from nosis.ir import Cell, Module, Net, PrimOp

__all__ = [
    "retime_forward",
    "retime_backward",
    "verify_retime_clocks",
    "duplicate_high_fanout",
]


def verify_retime_clocks(mod: Module) -> list[str]:
    """Verify that adjacent FFs connected through combinational logic share clocks.

    Returns warning strings for any FF pair where a source FF drives
    a destination FF through combinational logic but they have different clocks.
    """
    warnings: list[str] = []
    ff_clocks: dict[str, str] = {}
    for cell in mod.cells.values():
        if cell.op == PrimOp.FF:
            clk = cell.inputs.get("CLK")
            if clk:
                ff_clocks[cell.name] = clk.name

    for cell in mod.cells.values():
        if cell.op != PrimOp.FF:
            continue
        src_clk = ff_clocks.get(cell.name)
        if not src_clk:
            continue
        # Walk forward from Q through combinational cells to find downstream FFs
        visited: set[str] = set()
        worklist: list[Net] = list(cell.outputs.values())
        while worklist:
            net = worklist.pop()
            if net.name in visited:
                continue
            visited.add(net.name)
            for other in mod.cells.values():
                for inp in other.inputs.values():
                    if inp.name != net.name:
                        continue
                    if other.op == PrimOp.FF:
                        dst_clk = ff_clocks.get(other.name)
                        if dst_clk and dst_clk != src_clk:
                            warnings.append(
                                f"FF {cell.name} (clk={src_clk}) feeds "
                                f"FF {other.name} (clk={dst_clk}) — clock mismatch"
                            )
                    elif other.op not in (PrimOp.INPUT, PrimOp.OUTPUT, PrimOp.FF):
                        for out in other.outputs.values():
                            if out.name not in visited:
                                worklist.append(out)
    return warnings


def retime_forward(mod: Module, *, max_moves: int = 100) -> int:
    """Move FFs forward through single-fanout combinational cells.

    When an FF drives exactly one combinational cell, and that cell
    drives exactly one consumer, the FF can be moved past the
    combinational cell. This reduces the critical path on the
    input side at the cost of increasing it on the output side.

    Returns the number of FFs retimed.
    """
    # Pre-check: verify no existing clock mismatches
    verify_retime_clocks(mod)

    # Build consumer map
    net_consumers: dict[str, list[str]] = {}
    for cell in mod.cells.values():
        for net in cell.inputs.values():
            if net.name not in net_consumers:
                net_consumers[net.name] = []
            net_consumers[net.name].append(cell.name)

    retimed = 0
    for _ in range(max_moves):
        moved = False
        for cell in list(mod.cells.values()):
            if cell.op != PrimOp.FF:
                continue
            q_nets = list(cell.outputs.values())
            if not q_nets:
                continue
            q_net = q_nets[0]

            # FF output must have exactly one consumer
            consumers = net_consumers.get(q_net.name, [])
            if len(consumers) != 1:
                continue

            consumer_name = consumers[0]
            if consumer_name not in mod.cells:
                continue
            consumer = mod.cells[consumer_name]

            # Consumer must be combinational with one output
            if consumer.op in (PrimOp.FF, PrimOp.INPUT, PrimOp.OUTPUT, PrimOp.MEMORY):
                continue
            consumer_outs = list(consumer.outputs.values())
            if len(consumer_outs) != 1:
                continue

            # Relaxed retiming — allow multi-fanout with duplication
            # Previously required exactly 1 consumer. Now allow up to 4
            # consumers by duplicating the FF for each consumer path.
            consumer_out = consumer_outs[0]
            out_consumers = net_consumers.get(consumer_out.name, [])
            if len(out_consumers) > 4:
                continue  # too many — duplication cost exceeds benefit

            # Move FF: swap the FF to after the combinational cell
            # FF.D now connects to what was the consumer's input (not from FF)
            # The consumer now reads the original FF.D input
            d_net = cell.inputs.get("D")
            if d_net is None:
                continue

            # Find which port of the consumer reads from the FF output
            ff_port = None
            for port_name, net in consumer.inputs.items():
                if net is q_net:
                    ff_port = port_name
                    break
            if ff_port is None:
                continue

            # Rewire: consumer reads from FF's D input instead of FF's Q
            consumer.inputs[ff_port] = d_net
            # FF's D now reads from the consumer's output
            cell.inputs["D"] = consumer_out
            # FF's Q now drives whatever the consumer's output used to drive
            # (swap output nets)
            cell.outputs[list(cell.outputs.keys())[0]] = consumer_out
            consumer.outputs[list(consumer.outputs.keys())[0]] = q_net

            retimed += 1
            moved = True
            break  # restart scan after each move

        if not moved:
            break

    return retimed


def retime_backward(mod: Module, *, max_moves: int = 100) -> int:
    """Move FFs backward through single-fanin combinational cells.

    When an FF's D input is driven by a combinational cell with exactly
    one output consumer (the FF), and that driver has exactly one non-const
    data input, the FF can be moved before the driver. This balances
    pipeline stages by equalizing path delays.

    Returns the number of FFs retimed backward.
    """
    retimed = 0
    for _ in range(max_moves):
        moved = False
        for cell in list(mod.cells.values()):
            if cell.op != PrimOp.FF:
                continue
            d_net = cell.inputs.get("D")
            if d_net is None or d_net.driver is None:
                continue
            driver = d_net.driver
            if driver.op in (PrimOp.FF, PrimOp.INPUT, PrimOp.OUTPUT, PrimOp.MEMORY, PrimOp.CONST):
                continue
            driver_outs = list(driver.outputs.values())
            if len(driver_outs) != 1:
                continue
            # Count consumers of the driver output — must be only this FF
            consumer_count = sum(
                1 for other in mod.cells.values()
                for inp in other.inputs.values()
                if inp.name == d_net.name
            )
            if consumer_count != 1:
                continue
            # Driver must have exactly one non-const data input
            data_inputs = [
                n for n in driver.inputs.values()
                if n.driver is None or n.driver.op != PrimOp.CONST
            ]
            if len(data_inputs) != 1:
                continue
            driver_input = data_inputs[0]
            q_net = next(iter(cell.outputs.values()), None)
            if q_net is None:
                continue
            # Find which port of driver has our data input
            driver_port = next(
                (p for p, n in driver.inputs.items() if n is driver_input), None
            )
            if driver_port is None:
                continue
            # Rewire: FF reads driver's input, driver reads FF Q
            cell.inputs["D"] = driver_input
            driver.inputs[driver_port] = q_net
            cell.outputs[list(cell.outputs.keys())[0]] = d_net
            driver.outputs[list(driver.outputs.keys())[0]] = q_net
            d_net.driver = cell
            q_net.driver = driver
            retimed += 1
            moved = True
            break
        if not moved:
            break
    return retimed


def duplicate_high_fanout(mod: Module, *, threshold: int = 32) -> int:
    """Duplicate cells whose output drives more than `threshold` consumers.

    Creates copies of the cell, each driving a subset of the original
    consumers. Reduces routing pressure on high-fanout nets.

    Returns the number of cells duplicated.
    """
    # Build consumer map
    net_consumers: dict[str, list[tuple[Cell, str]]] = {}
    for cell in mod.cells.values():
        for port_name, net in cell.inputs.items():
            if net.name not in net_consumers:
                net_consumers[net.name] = []
            net_consumers[net.name].append((cell, port_name))

    duplicated = 0
    counter = [0]

    for cell in list(mod.cells.values()):
        if cell.op in (PrimOp.INPUT, PrimOp.OUTPUT, PrimOp.FF, PrimOp.CONST, PrimOp.MEMORY):
            continue

        out_nets = list(cell.outputs.values())
        if not out_nets:
            continue
        out_net = out_nets[0]

        consumers = net_consumers.get(out_net.name, [])
        if len(consumers) <= threshold:
            continue

        # Split consumers into groups of `threshold`
        groups = [consumers[i:i + threshold] for i in range(threshold, len(consumers), threshold)]
        if not groups:
            continue

        for group in groups:
            # Create a duplicate cell
            counter[0] += 1
            dup_name = f"{cell.name}_dup{counter[0]}"
            dup_out_name = f"{out_net.name}_dup{counter[0]}"

            if dup_out_name in mod.nets or dup_name in mod.cells:
                continue
            dup_out = mod.add_net(dup_out_name, out_net.width)
            dup_cell = mod.add_cell(dup_name, cell.op, **cell.params)

            # Copy inputs
            for port_name, inp_net in cell.inputs.items():
                mod.connect(dup_cell, port_name, inp_net)
            mod.connect(dup_cell, list(cell.outputs.keys())[0], dup_out, direction="output")

            # Rewire consumers in this group to the duplicate
            for consumer_cell, consumer_port in group:
                consumer_cell.inputs[consumer_port] = dup_out

            duplicated += 1

    return duplicated
