"""Nosis logic cone extraction — isolate the combinational fan-in of a net.

Given a target net, extract the transitive fan-in through combinational
logic, stopping at FF outputs and primary inputs. The result is a
sub-module containing only the cells and nets that contribute to the
target net's value.

Useful for targeted equivalence checking of individual outputs without
analyzing the entire design.
"""

from __future__ import annotations

from nosis.ir import Cell, Module, Net, PrimOp

__all__ = [
    "extract_cone",
]


def extract_cone(mod: Module, target_net_name: str) -> Module:
    """Extract the combinational logic cone driving a target net.

    Returns a new Module containing only the cells and nets in the
    transitive fan-in of the target. FF outputs and INPUT cells
    become the cone's primary inputs.
    """
    if target_net_name not in mod.nets:
        raise ValueError(f"net '{target_net_name}' not found in module '{mod.name}'")

    # Backward traversal from target
    visited_nets: set[str] = set()
    visited_cells: set[str] = set()
    cone_inputs: set[str] = set()  # nets that are cone boundaries (FF Q, INPUT)
    worklist: list[str] = [target_net_name]

    while worklist:
        net_name = worklist.pop()
        if net_name in visited_nets:
            continue
        visited_nets.add(net_name)

        net = mod.nets.get(net_name)
        if net is None:
            continue

        driver = net.driver
        if driver is None:
            cone_inputs.add(net_name)
            continue

        if driver.op in (PrimOp.FF, PrimOp.INPUT):
            cone_inputs.add(net_name)
            visited_cells.add(driver.name)
            continue

        if driver.op == PrimOp.MEMORY:
            cone_inputs.add(net_name)
            visited_cells.add(driver.name)
            continue

        visited_cells.add(driver.name)
        for inp_net in driver.inputs.values():
            if inp_net.name not in visited_nets:
                worklist.append(inp_net.name)

    # Build the cone module
    cone = Module(name=f"{mod.name}_cone_{target_net_name}")

    # Copy nets
    for name in visited_nets:
        net = mod.nets.get(name)
        if net:
            cone_net = cone.add_net(name, net.width)
            if name in cone_inputs:
                cone.ports[name] = cone_net

    # Copy cells
    for name in visited_cells:
        cell = mod.cells.get(name)
        if cell is None:
            continue

        cone_cell = cone.add_cell(name, cell.op, src=cell.src, **cell.params)

        for port_name, net in cell.inputs.items():
            if net.name in cone.nets:
                cone.connect(cone_cell, port_name, cone.nets[net.name])

        for port_name, net in cell.outputs.items():
            if net.name in cone.nets:
                cone.connect(cone_cell, port_name, cone.nets[net.name], direction="output")

    # Mark the target as an output
    if target_net_name in cone.nets:
        cone.ports[f"_target_{target_net_name}"] = cone.nets[target_net_name]

    return cone
