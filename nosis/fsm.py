"""Nosis FSM extraction — identify state machines and preserve their encoding.

This pass walks the IR and identifies flip-flops that are driven by MUX
trees derived from case/if-else statements over the same register's output.
These are tagged as FSM state registers with their transition logic
preserved exactly as the designer wrote it.

The pass does NOT re-encode state machines. If the designer used one-hot,
the netlist uses one-hot. If binary, binary. If an enum with specific
values, those values. The encoding survives synthesis unchanged.

This is an analysis and annotation pass, not a transformation. It tags
cells and nets with FSM metadata so that downstream passes (tech mapping,
optimization) can make informed decisions without destroying state machine
structure.
"""

from __future__ import annotations

from dataclasses import dataclass

from nosis.ir import Cell, Module, Net, PrimOp

__all__ = [
    "FSMState",
    "FSMInfo",
    "extract_fsms",
    "annotate_fsm_cells",
]


@dataclass(slots=True)
class FSMState:
    """A single state in an identified FSM."""
    name: str | None  # from TransparentMember if available
    value: int
    width: int

    def __repr__(self) -> str:
        label = f" ({self.name})" if self.name else ""
        return f"FSMState({self.value}{label}, w={self.width})"


@dataclass(slots=True)
class FSMInfo:
    """Metadata for an identified finite state machine."""
    state_net: str              # name of the state register net
    state_width: int            # bit width of the state register
    ff_cells: list[str]         # names of the FF cells holding this state
    mux_cells: list[str]        # names of MUX/EQ cells in the transition logic
    states: list[FSMState]      # known state values (from case labels)
    encoding: str               # "binary", "onehot", "sequential", "unknown"
    transition_depth: int       # depth of the MUX tree driving the state FF

    def __repr__(self) -> str:
        return (
            f"FSMInfo(net={self.state_net!r}, w={self.state_width}, "
            f"states={len(self.states)}, encoding={self.encoding})"
        )


def _classify_encoding(states: list[FSMState]) -> str:
    """Classify the state encoding scheme from observed state values."""
    if not states:
        return "unknown"

    values = [s.value for s in states]
    width = states[0].width
    n = len(values)

    # Check sequential: 0, 1, 2, 3, ...
    if values == list(range(n)):
        return "sequential"

    # Check one-hot: each value has exactly one bit set, all different
    if all(bin(v).count("1") == 1 for v in values if v != 0):
        bits_used = set()
        is_onehot = True
        for v in values:
            if v == 0:
                continue
            bit = v.bit_length() - 1
            if bit in bits_used:
                is_onehot = False
                break
            bits_used.add(bit)
        if is_onehot and len(bits_used) >= n - 1:  # allow one zero state
            return "onehot"

    # Check Gray code: consecutive states differ by exactly one bit
    is_gray = True
    for i in range(len(values) - 1):
        diff = values[i] ^ values[i + 1]
        if bin(diff).count("1") != 1:
            is_gray = False
            break
    if is_gray and n > 2:
        return "gray"

    # Check binary: values fit in minimum bits, no gaps
    if width <= 8 and n <= (1 << width):
        return "binary"

    return "unknown"


def _find_ff_feedback_loops(mod: Module) -> list[tuple[Cell, Net, Net]]:
    """Find FFs where the Q output feeds back into the D input's logic cone.

    Returns list of (ff_cell, state_net, d_net) for each feedback FF.
    """
    results: list[tuple[Cell, Net, Net]] = []

    # Build a map from ff_target names to FF cells/Q nets.
    # The lowering creates fresh Q output nets ($ff_q_state) instead of
    # wiring to the original state net, but records the original name in
    # cell.params["ff_target"]. We need to match feedback loops through
    # this indirection.
    target_to_ff: dict[str, tuple[Cell, Net]] = {}
    for cell in mod.cells.values():
        if cell.op != PrimOp.FF:
            continue
        q_net = next(iter(cell.outputs.values()), None)
        if q_net is None:
            continue
        target_name = str(cell.params.get("ff_target", q_net.name))
        target_to_ff[target_name] = (cell, q_net)

    for cell in mod.cells.values():
        if cell.op != PrimOp.FF:
            continue

        d_net = cell.inputs.get("D")
        if d_net is None:
            continue

        q_net = next(iter(cell.outputs.values()), None)
        if q_net is None:
            continue

        # The target net name — the original register this FF drives
        target_name = str(cell.params.get("ff_target", q_net.name))

        # Walk backward from D through the logic cone, looking for
        # the target net (the original state register name, not the
        # fresh $ff_q_ output net).
        visited: set[str] = set()
        worklist: list[Net] = [d_net]
        found_feedback = False

        # Names that indicate feedback to this FF
        feedback_names = {q_net.name, target_name}

        while worklist and not found_feedback:
            net = worklist.pop()
            if net.name in visited:
                continue
            visited.add(net.name)

            # Check direct match
            if net.name in feedback_names:
                found_feedback = True
                break

            if net.driver is not None:
                driver = net.driver
                if driver.op == PrimOp.FF:
                    # Another FF — check if it targets the same register
                    other_target = str(driver.params.get("ff_target", ""))
                    for other_q in driver.outputs.values():
                        if other_q.name in feedback_names or other_target == target_name:
                            found_feedback = True
                            break
                    continue

                # Walk through combinational logic
                for inp_net in driver.inputs.values():
                    if inp_net.name not in visited:
                        worklist.append(inp_net)

        if found_feedback:
            # Use the target name as the canonical state net for the FSM
            state_net = mod.nets.get(target_name, q_net)
            results.append((cell, state_net, d_net))

    return results


def _collect_mux_tree(mod: Module, root_net: Net, state_net_name: str) -> tuple[list[str], list[FSMState], int]:
    """Walk backward from a net through MUX/EQ cells to collect transition logic.

    Returns (mux_cell_names, discovered_states, tree_depth).
    """
    mux_cells: list[str] = []
    states: list[FSMState] = []
    seen_values: set[int] = set()
    max_depth = 0

    def walk(net: Net, depth: int) -> None:
        """Walk the data structure, calling the visitor function."""
        nonlocal max_depth
        max_depth = max(max_depth, depth)

        if net.driver is None:
            return

        cell = net.driver
        if cell.op == PrimOp.MUX:
            mux_cells.append(cell.name)
            # The selector might be an EQ comparison against a state value
            s_net = cell.inputs.get("S")
            if s_net and s_net.driver and s_net.driver.op == PrimOp.EQ:
                eq_cell = s_net.driver
                mux_cells.append(eq_cell.name)
                # Check if one input to EQ is the state net
                for port_name, eq_inp in eq_cell.inputs.items():
                    if eq_inp.name == state_net_name:
                        continue
                    # The other input is a case label — may be a CONST cell
                    # or a named parameter/enum constant
                    val: int | None = None
                    width: int = eq_inp.width
                    label: str | None = None
                    if eq_inp.driver and eq_inp.driver.op == PrimOp.CONST:
                        val = int(eq_inp.driver.params.get("value", 0))
                        width = int(eq_inp.driver.params.get("width", 1))
                    else:
                        # Look for a CONST cell in the module that drives
                        # a net with this name (parameter/enum constants)
                        for other_cell in mod.cells.values():
                            if other_cell.op == PrimOp.CONST:
                                for out_net in other_cell.outputs.values():
                                    if out_net.name == eq_inp.name:
                                        val = int(other_cell.params.get("value", 0))
                                        width = int(other_cell.params.get("width", 1))
                                        label = eq_inp.name
                                        break
                                if val is not None:
                                    break
                    if val is not None and val not in seen_values:
                        seen_values.add(val)
                        states.append(FSMState(name=label, value=val, width=width))

            # Recurse into MUX inputs (the true/false branches)
            for port in ("A", "B"):
                branch = cell.inputs.get(port)
                if branch:
                    walk(branch, depth + 1)

        elif cell.op == PrimOp.EQ:
            mux_cells.append(cell.name)

        elif cell.op == PrimOp.CONST:
            val = int(cell.params.get("value", 0))
            width = int(cell.params.get("width", 1))
            if val not in seen_values:
                seen_values.add(val)
                states.append(FSMState(name=None, value=val, width=width))

    walk(root_net, 0)
    return mux_cells, states, max_depth


def extract_fsms(mod: Module) -> list[FSMInfo]:
    """Identify finite state machines in the module.

    Finds FFs with feedback loops through MUX trees (case statements over
    the state register). Returns metadata for each identified FSM without
    modifying the IR.
    """
    fsms: list[FSMInfo] = []
    seen_state_nets: set[str] = set()

    feedback_ffs = _find_ff_feedback_loops(mod)

    for ff_cell, q_net, d_net in feedback_ffs:
        state_name = q_net.name
        if state_name in seen_state_nets:
            continue

        # Collect the MUX tree and state values
        mux_cells, states, depth = _collect_mux_tree(mod, d_net, state_name)

        # Only classify as FSM if there are multiple states and a MUX tree
        if len(states) < 2 or depth < 1:
            continue

        seen_state_nets.add(state_name)

        # Sort states by value
        states.sort(key=lambda s: s.value)

        # Classify encoding
        encoding = _classify_encoding(states)

        # Collect all FF cells for this state (multi-bit state registers
        # may have one FF per bit, all with the same base name)
        ff_names = [ff_cell.name]
        for other_cell in mod.cells.values():
            if other_cell is ff_cell:
                continue
            if other_cell.op != PrimOp.FF:
                continue
            for other_q in other_cell.outputs.values():
                # Same state register if the name matches the base
                if other_q.name.startswith(state_name.rsplit("_", 1)[0]):
                    ff_names.append(other_cell.name)
                    break

        fsms.append(FSMInfo(
            state_net=state_name,
            state_width=q_net.width,
            ff_cells=ff_names,
            mux_cells=mux_cells,
            states=states,
            encoding=encoding,
            transition_depth=depth,
        ))

    return fsms


def annotate_fsm_cells(mod: Module, fsms: list[FSMInfo]) -> int:
    """Tag IR cells with FSM metadata. Returns the number of cells annotated.

    Adds 'fsm_state' to FF cell params and 'fsm_transition' to MUX/EQ
    cell params so downstream passes know these are part of an FSM and
    should not be re-encoded or aggressively optimized.
    """
    annotated = 0

    for fsm in fsms:
        for ff_name in fsm.ff_cells:
            if ff_name in mod.cells:
                cell = mod.cells[ff_name]
                cell.params["fsm_state"] = fsm.state_net
                cell.params["fsm_encoding"] = fsm.encoding
                cell.params["fsm_num_states"] = len(fsm.states)
                annotated += 1

        for mux_name in fsm.mux_cells:
            if mux_name in mod.cells:
                cell = mod.cells[mux_name]
                cell.params["fsm_transition"] = fsm.state_net
                annotated += 1

    return annotated
