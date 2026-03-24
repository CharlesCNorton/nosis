"""Tests for nosis.testvec — automatic test vector generation."""

from nosis.ir import Module, PrimOp
from nosis.testvec import generate_test_vectors


def _gate_module():
    mod = Module(name="test")
    a = mod.add_net("a", 8)
    b = mod.add_net("b", 8)
    y = mod.add_net("y", 8)
    ac = mod.add_cell("a_p", PrimOp.INPUT, port_name="a")
    mod.connect(ac, "Y", a, direction="output")
    mod.ports["a"] = a
    bc = mod.add_cell("b_p", PrimOp.INPUT, port_name="b")
    mod.connect(bc, "Y", b, direction="output")
    mod.ports["b"] = b
    return mod


def test_generates_vectors():
    mod = _gate_module()
    vecs = generate_test_vectors(mod, num_random=10)
    assert len(vecs) > 10  # corner cases + random


def test_first_is_all_zeros():
    mod = _gate_module()
    vecs = generate_test_vectors(mod)
    assert vecs[0].description == "all_zeros"
    assert all(v == 0 for v in vecs[0].inputs.values())


def test_second_is_all_ones():
    mod = _gate_module()
    vecs = generate_test_vectors(mod)
    assert vecs[1].description == "all_ones"
    assert vecs[1].inputs["a"] == 0xFF
    assert vecs[1].inputs["b"] == 0xFF


def test_deterministic():
    mod = _gate_module()
    v1 = generate_test_vectors(mod, seed=42)
    v2 = generate_test_vectors(mod, seed=42)
    assert len(v1) == len(v2)
    for a, b in zip(v1, v2):
        assert a.inputs == b.inputs


def test_empty_module():
    mod = Module(name="empty")
    vecs = generate_test_vectors(mod)
    assert len(vecs) == 0


def test_single_bit_input():
    mod = Module(name="test")
    a = mod.add_net("a", 1)
    ac = mod.add_cell("a_p", PrimOp.INPUT, port_name="a")
    mod.connect(ac, "Y", a, direction="output")
    mod.ports["a"] = a
    vecs = generate_test_vectors(mod, num_random=5)
    assert len(vecs) > 3  # zeros, ones, onehot, max, random
