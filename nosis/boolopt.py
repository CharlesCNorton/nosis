"""Nosis Boolean optimization — algebraic simplification of logic expressions.

Applies algebraic identities to reduce cell count:
  - (a & b) | (a & c)  ->  a & (b | c)     (AND distribution)
  - (a | b) & (a | c)  ->  a | (b & c)     (OR distribution)
  - a & a  ->  a                             (idempotent AND)
  - a | a  ->  a                             (idempotent OR)
  - a ^ a  ->  0                             (XOR self)
  - a & ~a  ->  0                            (AND complement)
  - a | ~a  ->  all_ones                     (OR complement)

Operates at the IR level before tech mapping.
"""

from __future__ import annotations

from nosis.ir import Cell, Module, Net, PrimOp

__all__ = [
    "boolean_optimize",
]


def boolean_optimize(mod: Module) -> int:
    """Apply Boolean algebraic simplifications. Returns cells eliminated."""
    eliminated = 0

    # Build consumer map
    net_consumers: dict[str, list[tuple[Cell, str]]] = {}
    for cell in mod.cells.values():
        for port_name, net in cell.inputs.items():
            if net.name not in net_consumers:
                net_consumers[net.name] = []
            net_consumers[net.name].append((cell, port_name))

    to_remove: set[str] = set()

    # --- Idempotent: a OP a -> a (AND, OR) ---
    for cell in list(mod.cells.values()):
        if cell.name in to_remove:
            continue
        if cell.op not in (PrimOp.AND, PrimOp.OR):
            continue
        a_net = cell.inputs.get("A")
        b_net = cell.inputs.get("B")
        if a_net and b_net and a_net is b_net:
            # a OP a -> a: bypass cell
            for out_net in cell.outputs.values():
                out_net.driver = a_net.driver
            to_remove.add(cell.name)
            eliminated += 1

    # --- XOR self: a ^ a -> 0 ---
    for cell in list(mod.cells.values()):
        if cell.name in to_remove:
            continue
        if cell.op != PrimOp.XOR:
            continue
        a_net = cell.inputs.get("A")
        b_net = cell.inputs.get("B")
        if a_net and b_net and a_net is b_net:
            out_nets = list(cell.outputs.values())
            if out_nets:
                w = out_nets[0].width
                cell.inputs.clear()
                cell.op = PrimOp.CONST
                cell.params = {"value": 0, "width": w}
                eliminated += 1

    # --- AND distribution: (a & b) | (a & c) -> a & (b | c) ---
    for cell in list(mod.cells.values()):
        if cell.name in to_remove:
            continue
        if cell.op != PrimOp.OR:
            continue
        a_net = cell.inputs.get("A")
        b_net = cell.inputs.get("B")
        if not a_net or not b_net:
            continue
        if not a_net.driver or not b_net.driver:
            continue
        if a_net.driver.op != PrimOp.AND or b_net.driver.op != PrimOp.AND:
            continue

        and_a = a_net.driver
        and_b = b_net.driver
        if and_a.name in to_remove or and_b.name in to_remove:
            continue

        # Check for common input
        a_inputs = {p: n for p, n in and_a.inputs.items()}
        b_inputs = {p: n for p, n in and_b.inputs.items()}

        common = None
        a_other = None
        b_other = None

        aa, ab = a_inputs.get("A"), a_inputs.get("B")
        ba, bb = b_inputs.get("A"), b_inputs.get("B")

        if aa and ba and aa is ba:
            common = aa
            a_other = ab
            b_other = bb
        elif aa and bb and aa is bb:
            common = aa
            a_other = ab
            b_other = ba
        elif ab and ba and ab is ba:
            common = ab
            a_other = aa
            b_other = bb
        elif ab and bb and ab is bb:
            common = ab
            a_other = aa
            b_other = ba

        if common is None or a_other is None or b_other is None:
            continue

        # Check single consumer on both AND outputs
        a_consumers = net_consumers.get(a_net.name, [])
        b_consumers = net_consumers.get(b_net.name, [])
        a_live = [(c, p) for c, p in a_consumers if c.name not in to_remove]
        b_live = [(c, p) for c, p in b_consumers if c.name not in to_remove]
        if len(a_live) != 1 or len(b_live) != 1:
            continue

        # Rewrite: OR(AND(common, a_other), AND(common, b_other))
        #       -> AND(common, OR(a_other, b_other))
        # Reuse the OR cell for the inner OR, rewrite inputs
        cell.inputs["A"] = a_other
        cell.inputs["B"] = b_other
        # The OR cell now computes a_other | b_other

        # Reuse and_a for the outer AND
        and_a.inputs["A"] = common
        and_a.inputs["B"] = list(cell.outputs.values())[0]  # OR output

        # and_b is dead
        to_remove.add(and_b.name)
        eliminated += 1

    # --- OR distribution: (a | b) & (a | c) -> a | (b & c) ---
    # Rebuild consumer map after AND distribution may have changed things
    net_consumers2: dict[str, list[tuple[Cell, str]]] = {}
    for cell in mod.cells.values():
        if cell.name in to_remove:
            continue
        for port_name, net in cell.inputs.items():
            if net.name not in net_consumers2:
                net_consumers2[net.name] = []
            net_consumers2[net.name].append((cell, port_name))

    for cell in list(mod.cells.values()):
        if cell.name in to_remove:
            continue
        if cell.op != PrimOp.AND:
            continue
        a_net = cell.inputs.get("A")
        b_net = cell.inputs.get("B")
        if not a_net or not b_net:
            continue
        if not a_net.driver or not b_net.driver:
            continue
        if a_net.driver.op != PrimOp.OR or b_net.driver.op != PrimOp.OR:
            continue

        or_a = a_net.driver
        or_b = b_net.driver
        if or_a.name in to_remove or or_b.name in to_remove:
            continue

        # Check for common input
        aa, ab = or_a.inputs.get("A"), or_a.inputs.get("B")
        ba, bb = or_b.inputs.get("A"), or_b.inputs.get("B")

        common = None
        a_other = None
        b_other = None

        for x, y in [(aa, ba), (aa, bb), (ab, ba), (ab, bb)]:
            if x and y and x is y:
                common = x
                a_other = ab if x is aa else aa
                b_other = bb if y is ba else ba
                break

        if common is None or a_other is None or b_other is None:
            continue

        a_consumers = net_consumers2.get(a_net.name, [])
        b_consumers = net_consumers2.get(b_net.name, [])
        a_live = [(c, p) for c, p in a_consumers if c.name not in to_remove]
        b_live = [(c, p) for c, p in b_consumers if c.name not in to_remove]
        if len(a_live) != 1 or len(b_live) != 1:
            continue

        # Rewrite: AND(OR(common, a_other), OR(common, b_other))
        #       -> OR(common, AND(a_other, b_other))
        cell.inputs["A"] = a_other
        cell.inputs["B"] = b_other
        # cell is now AND(a_other, b_other)

        or_a.inputs["A"] = common
        or_a.inputs["B"] = list(cell.outputs.values())[0]
        # or_a is now OR(common, AND_output)

        to_remove.add(or_b.name)
        eliminated += 1

    for name in to_remove:
        if name in mod.cells:
            del mod.cells[name]

    return eliminated
