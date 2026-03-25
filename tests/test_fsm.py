"""Tests for nosis.fsm — FSM extraction and annotation."""

from nosis.ir import Module, PrimOp
from nosis.fsm import FSMState, extract_fsms, annotate_fsm_cells, _classify_encoding


def test_classify_sequential():
    states = [FSMState(None, i, 3) for i in range(5)]
    assert _classify_encoding(states) == "sequential"


def test_classify_onehot():
    states = [
        FSMState("IDLE", 1, 4),
        FSMState("RUN", 2, 4),
        FSMState("DONE", 4, 4),
        FSMState("ERR", 8, 4),
    ]
    assert _classify_encoding(states) == "onehot"


def test_classify_binary():
    states = [
        FSMState(None, 0, 3),
        FSMState(None, 2, 3),
        FSMState(None, 5, 3),
        FSMState(None, 7, 3),
    ]
    assert _classify_encoding(states) == "binary"


def test_classify_empty():
    assert _classify_encoding([]) == "unknown"


def _build_fsm_module():
    """Build a minimal FSM in IR: state register with MUX-driven transitions."""
    mod = Module(name="fsm_test")

    # Ports
    clk = mod.add_net("clk", 1)
    clk_cell = mod.add_cell("clk_p", PrimOp.INPUT, port_name="clk")
    mod.connect(clk_cell, "Y", clk, direction="output")
    mod.ports["clk"] = clk

    rst = mod.add_net("rst", 1)
    rst_cell = mod.add_cell("rst_p", PrimOp.INPUT, port_name="rst")
    mod.connect(rst_cell, "Y", rst, direction="output")
    mod.ports["rst"] = rst

    out = mod.add_net("out", 2)
    out_cell = mod.add_cell("out_p", PrimOp.OUTPUT, port_name="out")
    mod.connect(out_cell, "A", out)
    mod.ports["out"] = out

    # State register
    state = mod.add_net("state", 2)
    state_next = mod.add_net("state_next", 2)

    # Constants for state values
    s0_net = mod.add_net("s0", 2)
    s0_cell = mod.add_cell("s0_const", PrimOp.CONST, value=0, width=2)
    mod.connect(s0_cell, "Y", s0_net, direction="output")

    s1_net = mod.add_net("s1", 2)
    s1_cell = mod.add_cell("s1_const", PrimOp.CONST, value=1, width=2)
    mod.connect(s1_cell, "Y", s1_net, direction="output")

    s2_net = mod.add_net("s2", 2)
    s2_cell = mod.add_cell("s2_const", PrimOp.CONST, value=2, width=2)
    mod.connect(s2_cell, "Y", s2_net, direction="output")

    # EQ comparisons: state == 0, state == 1
    eq0_out = mod.add_net("eq0", 1)
    eq0 = mod.add_cell("eq0", PrimOp.EQ)
    mod.connect(eq0, "A", state)
    mod.connect(eq0, "B", s0_net)
    mod.connect(eq0, "Y", eq0_out, direction="output")

    eq1_out = mod.add_net("eq1", 1)
    eq1 = mod.add_cell("eq1", PrimOp.EQ)
    mod.connect(eq1, "A", state)
    mod.connect(eq1, "B", s1_net)
    mod.connect(eq1, "Y", eq1_out, direction="output")

    # MUX tree: if state==0 -> 1, elif state==1 -> 2, else -> 0
    mux1_out = mod.add_net("mux1", 2)
    mux1 = mod.add_cell("mux1", PrimOp.MUX)
    mod.connect(mux1, "S", eq1_out)
    mod.connect(mux1, "A", s0_net)   # else: 0
    mod.connect(mux1, "B", s2_net)   # state==1: 2
    mod.connect(mux1, "Y", mux1_out, direction="output")

    mux0 = mod.add_cell("mux0", PrimOp.MUX)
    mod.connect(mux0, "S", eq0_out)
    mod.connect(mux0, "A", mux1_out)  # else: inner mux result
    mod.connect(mux0, "B", s1_net)    # state==0: 1
    mod.connect(mux0, "Y", state_next, direction="output")

    # FF: state_next -> state
    ff = mod.add_cell("state_ff", PrimOp.FF)
    mod.connect(ff, "CLK", clk)
    mod.connect(ff, "D", state_next)
    mod.connect(ff, "Q", state, direction="output")

    return mod


def test_extract_fsm():
    mod = _build_fsm_module()
    fsms = extract_fsms(mod)
    assert len(fsms) >= 1
    fsm = fsms[0]
    assert fsm.state_net == "state"
    assert fsm.state_width == 2
    assert len(fsm.states) >= 2
    assert fsm.transition_depth >= 1
    assert fsm.encoding in ("sequential", "binary", "unknown")


def test_annotate_fsm():
    mod = _build_fsm_module()
    fsms = extract_fsms(mod)
    count = annotate_fsm_cells(mod, fsms)
    assert count >= 1
    # The state FF should be annotated
    ff = mod.cells["state_ff"]
    assert "fsm_state" in ff.params
    assert ff.params["fsm_state"] == "state"


def test_fsm_preserves_encoding():
    """Verify that FSM extraction does not modify any cell or net."""
    mod = _build_fsm_module()
    cells_before = {name: (c.op, dict(c.params)) for name, c in mod.cells.items()}
    nets_before = set(mod.nets.keys())

    extract_fsms(mod)

    # No cells or nets should have been added or removed
    assert set(mod.cells.keys()) == set(cells_before.keys())
    assert set(mod.nets.keys()) == nets_before

    # Cell ops should not have changed
    for name, (op, _) in cells_before.items():
        assert mod.cells[name].op == op, f"cell {name} op changed"
