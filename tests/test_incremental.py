"""Tests for nosis.incremental — change detection and snapshots."""

import tempfile
from pathlib import Path

from nosis.ir import Module, PrimOp
from nosis.incremental import snapshot_module, compute_delta, save_snapshot, load_snapshot


def test_identical_modules():
    mod = Module(name="test")
    mod.add_net("a", 1)
    mod.add_cell("c0", PrimOp.AND)
    s1 = snapshot_module(mod)
    s2 = snapshot_module(mod)
    delta = compute_delta(s1, s2)
    assert delta.is_empty


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


def test_cell_removed():
    mod = Module(name="test")
    mod.add_cell("c0", PrimOp.AND)
    mod.add_cell("c1", PrimOp.OR)
    s1 = snapshot_module(mod)
    del mod.cells["c1"]
    s2 = snapshot_module(mod)
    delta = compute_delta(s1, s2)
    assert "c1" in delta.cells_removed


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
