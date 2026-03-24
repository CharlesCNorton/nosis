"""Tests for nosis.incremental — change detection, snapshots, and incremental remapping."""

import tempfile
from pathlib import Path

from nosis.ir import Design, Module, PrimOp
from nosis.incremental import (
    snapshot_module,
    compute_delta,
    save_snapshot,
    load_snapshot,
    serialize_module,
    save_ir,
    load_ir_data,
    incremental_remap,
)


def test_identical_modules():
    mod = Module(name="test")
    mod.add_net("a", 1)
    mod.add_cell("c0", PrimOp.AND)
    s1 = snapshot_module(mod)
    s2 = snapshot_module(mod)
    delta = compute_delta(s1, s2)
    assert delta.is_empty
    assert delta.changed_count == 0


def test_cell_added():
    mod = Module(name="test")
    mod.add_net("a", 1)
    mod.add_cell("c0", PrimOp.AND)
    s1 = snapshot_module(mod)
    mod.add_cell("c1", PrimOp.OR)
    s2 = snapshot_module(mod)
    delta = compute_delta(s1, s2)
    assert not delta.is_empty
    assert "c1" in delta.cells_added
    assert delta.changed_count >= 1


def test_cell_removed():
    mod = Module(name="test")
    mod.add_cell("c0", PrimOp.AND)
    mod.add_cell("c1", PrimOp.OR)
    s1 = snapshot_module(mod)
    del mod.cells["c1"]
    s2 = snapshot_module(mod)
    delta = compute_delta(s1, s2)
    assert "c1" in delta.cells_removed


def test_cell_modified():
    """Changing a cell's connections should be detected as a modification."""
    mod = Module(name="test")
    a = mod.add_net("a", 1)
    b = mod.add_net("b", 1)
    y = mod.add_net("y", 1)
    cell = mod.add_cell("g0", PrimOp.AND)
    mod.connect(cell, "A", a)
    mod.connect(cell, "B", b)
    mod.connect(cell, "Y", y, direction="output")
    s1 = snapshot_module(mod)

    # Rewire B to a different net
    c = mod.add_net("c", 1)
    cell.inputs["B"] = c
    s2 = snapshot_module(mod)
    delta = compute_delta(s1, s2)
    assert "g0" in delta.cells_modified


def test_save_load_roundtrip():
    mod = Module(name="test")
    mod.add_net("a", 8)
    mod.add_cell("c0", PrimOp.AND)
    mod.ports["a"] = mod.nets["a"]
    snap = snapshot_module(mod)

    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        path = f.name
    try:
        save_snapshot(snap, path)
        loaded = load_snapshot(path)
        assert loaded.module_name == snap.module_name
        assert loaded.cell_hashes == snap.cell_hashes
        assert loaded.port_names == snap.port_names
        assert loaded.total_cells == snap.total_cells
        assert loaded.total_nets == snap.total_nets
    finally:
        Path(path).unlink()


def test_port_change_detected():
    mod = Module(name="test")
    a = mod.add_net("a", 1)
    mod.ports["a"] = a
    s1 = snapshot_module(mod)
    b = mod.add_net("b", 1)
    mod.ports["b"] = b
    s2 = snapshot_module(mod)
    delta = compute_delta(s1, s2)
    assert delta.ports_changed


def test_serialize_module_roundtrip():
    """serialize_module must capture all cells, nets, and ports."""
    mod = Module(name="test")
    a = mod.add_net("a", 4)
    y = mod.add_net("y", 4)
    cell = mod.add_cell("not0", PrimOp.NOT)
    mod.connect(cell, "A", a)
    mod.connect(cell, "Y", y, direction="output")
    mod.ports["a"] = a
    mod.ports["y"] = y

    data = serialize_module(mod)
    assert data["module"] == "test"
    assert "not0" in data["cells"]
    assert data["cells"]["not0"]["op"] == "NOT"
    assert "a" in data["nets"]
    assert "y" in data["nets"]
    assert "a" in data["ports"]
    assert "y" in data["ports"]


def test_save_load_ir():
    mod = Module(name="test")
    a = mod.add_net("a", 8)
    y = mod.add_net("y", 8)
    cell = mod.add_cell("and0", PrimOp.AND)
    mod.connect(cell, "A", a)
    mod.connect(cell, "Y", y, direction="output")
    mod.ports["a"] = a

    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        path = f.name
    try:
        save_ir(mod, path)
        data = load_ir_data(path)
        assert data["module"] == "test"
        assert "and0" in data["cells"]
    finally:
        Path(path).unlink()


def test_incremental_remap_empty_delta():
    """An empty delta should return the previous netlist unchanged."""
    from nosis.techmap import ECP5Netlist
    design = Design()
    mod = design.add_module("test")
    design.top = "test"

    prev = ECP5Netlist(top="test")
    prev.add_cell("lut0", "TRELLIS_SLICE")

    s1 = snapshot_module(mod)
    delta = compute_delta(s1, s1)
    result = incremental_remap(design, delta, prev)
    # Empty delta returns the previous netlist
    assert result is prev


def test_incremental_remap_large_delta():
    """A large delta should trigger full re-mapping."""
    from nosis.techmap import ECP5Netlist
    design = Design()
    mod = design.add_module("test")
    a = mod.add_net("a", 1)
    y = mod.add_net("y", 1)
    cell = mod.add_cell("not0", PrimOp.NOT)
    mod.connect(cell, "A", a)
    mod.connect(cell, "Y", y, direction="output")
    inp = mod.add_cell("a_p", PrimOp.INPUT, port_name="a")
    mod.connect(inp, "Y", a, direction="output")
    mod.ports["a"] = a
    out = mod.add_cell("y_p", PrimOp.OUTPUT, port_name="y")
    mod.connect(out, "A", y)
    mod.ports["y"] = y
    design.top = "test"

    prev = ECP5Netlist(top="test")

    s1 = snapshot_module(Module(name="test"))  # empty "before"
    s2 = snapshot_module(mod)
    delta = compute_delta(s1, s2)
    assert not delta.is_empty

    result = incremental_remap(design, delta, prev)
    # Should have produced a new netlist (full re-map)
    assert result is not prev
    assert result.stats()["cells"] >= 1


def test_delta_summary_lines():
    mod = Module(name="test")
    mod.add_cell("c0", PrimOp.AND)
    s1 = snapshot_module(mod)
    mod.add_cell("c1", PrimOp.OR)
    s2 = snapshot_module(mod)
    delta = compute_delta(s1, s2)
    lines = delta.summary_lines()
    assert any("added" in l.lower() for l in lines)
