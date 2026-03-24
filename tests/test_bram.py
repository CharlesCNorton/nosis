"""Tests for nosis.bram — BRAM inference."""

from nosis.ir import Module, PrimOp
from nosis.bram import infer_brams, _fits_dp16kd, _count_brams_needed


def test_fits_small():
    assert _fits_dp16kd(256, 8) is not None  # 2Kx9 fits easily


def test_fits_exact():
    assert _fits_dp16kd(1024, 16) is not None  # 1Kx18


def test_fits_512x32():
    assert _fits_dp16kd(512, 32) is not None  # 512x36


def test_too_wide_single():
    assert _fits_dp16kd(512, 64) is None  # needs tiling


def test_count_brams_tiled():
    # 1024x64 needs multiple BRAMs
    count = _count_brams_needed(1024, 64)
    assert count >= 2


def test_infer_tags_memory():
    mod = Module(name="test")
    net = mod.add_net("mem_out", 8)
    cell = mod.add_cell("mem0", PrimOp.MEMORY, depth=1024, width=8)
    mod.connect(cell, "RDATA", net, direction="output")
    out_cell = mod.add_cell("out", PrimOp.OUTPUT, port_name="out")
    mod.connect(out_cell, "A", net)
    mod.ports["out"] = net

    tagged = infer_brams(mod)
    assert tagged == 1
    assert mod.cells["mem0"].params["bram_config"] == "DP16KD"


def test_skip_tiny_array():
    mod = Module(name="test")
    net = mod.add_net("mem_out", 4)
    cell = mod.add_cell("mem0", PrimOp.MEMORY, depth=4, width=4)
    mod.connect(cell, "RDATA", net, direction="output")

    tagged = infer_brams(mod)
    assert tagged == 0  # 16 bits total, too small for BRAM
