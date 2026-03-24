"""Tests for nosis.bram — BRAM inference, DPR16X4 emission, write mode, output register."""

from nosis.ir import Module, PrimOp
from nosis.bram import infer_brams, infer_memory_ports, detect_write_mode, infer_output_register, _fits_dp16kd, _count_brams_needed


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
    net = mod.add_net("mem_out", 2)
    cell = mod.add_cell("mem0", PrimOp.MEMORY, depth=2, width=2)
    mod.connect(cell, "RDATA", net, direction="output")

    tagged = infer_brams(mod)
    assert tagged == 0  # 4 bits total, too small for any RAM


# ---------------------------------------------------------------------------
# DPR16X4 emission through full techmap pipeline
# ---------------------------------------------------------------------------

def test_dpr16x4_inference_and_emission():
    """A 16x4 array must be tagged DPR16X4 and emit TRELLIS_DPR16X4 cells."""
    from nosis.techmap import map_to_ecp5
    from nosis.ir import Design

    mod = Module(name="dpr_test")
    # Create a 16-entry, 4-bit wide memory -> should fit DPR16X4
    raddr = mod.add_net("raddr", 4)
    waddr = mod.add_net("waddr", 4)
    wdata = mod.add_net("wdata", 4)
    rdata = mod.add_net("rdata", 4)
    we = mod.add_net("we", 1)
    clk = mod.add_net("clk", 1)

    mem = mod.add_cell("mem0", PrimOp.MEMORY, depth=16, width=4, mem_name="fifo")
    mod.connect(mem, "RADDR", raddr)
    mod.connect(mem, "WADDR", waddr)
    mod.connect(mem, "WDATA", wdata)
    mod.connect(mem, "WE", we)
    mod.connect(mem, "CLK", clk)
    mod.connect(mem, "RDATA", rdata, direction="output")

    # Input ports
    for name, net in [("raddr", raddr), ("waddr", waddr), ("wdata", wdata),
                      ("we", we), ("clk", clk)]:
        inp = mod.add_cell(f"inp_{name}", PrimOp.INPUT, port_name=name)
        mod.connect(inp, "Y", net, direction="output")
        mod.ports[name] = net
    out = mod.add_cell("out_rdata", PrimOp.OUTPUT, port_name="rdata")
    mod.connect(out, "A", rdata)
    mod.ports["rdata"] = rdata

    # Infer BRAM
    tagged = infer_brams(mod)
    assert tagged == 1
    assert mem.params["bram_config"] == "DPR16X4"

    # Tech map — should produce TRELLIS_DPR16X4 cells
    design = Design()
    design.modules["dpr_test"] = mod
    design.top = "dpr_test"
    nl = map_to_ecp5(design)
    stats = nl.stats()
    assert stats.get("TRELLIS_DPR16X4", 0) >= 1, f"expected DPR16X4, got {stats}"


def test_dpr16x4_tiled_wide():
    """A 16x8 array must tile across 2 DPR16X4 cells."""
    mod = Module(name="dpr_wide")
    rdata = mod.add_net("rdata", 8)
    mem = mod.add_cell("mem0", PrimOp.MEMORY, depth=16, width=8, mem_name="wide_fifo")
    mod.connect(mem, "RDATA", rdata, direction="output")

    tagged = infer_brams(mod)
    assert tagged == 1
    assert mem.params["bram_config"] == "DPR16X4_TILED"
    assert mem.params["bram_count"] == 2


def test_memory_port_inference():
    """Memory port inference must annotate read/write port counts."""
    mod = Module(name="test")
    raddr = mod.add_net("raddr", 10)
    waddr = mod.add_net("waddr", 10)
    rdata = mod.add_net("rdata", 8)

    mem = mod.add_cell("mem0", PrimOp.MEMORY, depth=1024, width=8)
    mod.connect(mem, "RADDR", raddr)
    mod.connect(mem, "WADDR", waddr)
    mod.connect(mem, "RDATA", rdata, direction="output")

    annotated = infer_memory_ports(mod)
    assert annotated == 1
    assert mem.params["mem_read_ports"] == 1
    assert mem.params["mem_write_ports"] == 1
    assert mem.params["mem_dual_port"] is True


# Write mode detection (item 17)

def test_write_mode_different_addrs():
    """Different read/write addresses should default to NORMAL."""
    mod = Module(name="test")
    raddr = mod.add_net("raddr", 10)
    waddr = mod.add_net("waddr", 10)
    rdata = mod.add_net("rdata", 8)
    mem = mod.add_cell("mem0", PrimOp.MEMORY, depth=1024, width=8)
    mod.connect(mem, "RADDR", raddr)
    mod.connect(mem, "WADDR", waddr)
    mod.connect(mem, "RDATA", rdata, direction="output")
    detect_write_mode(mod)
    assert mem.params["write_mode"] == "NORMAL"


def test_write_mode_same_addr_no_feedback():
    """Same address, no feedback from rdata to wdata → NORMAL."""
    mod = Module(name="test")
    addr = mod.add_net("addr", 10)
    rdata = mod.add_net("rdata", 8)
    wdata = mod.add_net("wdata", 8)
    mem = mod.add_cell("mem0", PrimOp.MEMORY, depth=1024, width=8)
    mod.connect(mem, "RADDR", addr)
    mod.connect(mem, "WADDR", addr)
    mod.connect(mem, "WDATA", wdata)
    mod.connect(mem, "RDATA", rdata, direction="output")
    detect_write_mode(mod)
    assert mem.params["write_mode"] == "NORMAL"


def test_write_mode_same_addr_feedback():
    """Same address, wdata derived from rdata → WRITETHROUGH."""
    mod = Module(name="test")
    addr = mod.add_net("addr", 10)
    rdata = mod.add_net("rdata", 8)
    inc = mod.add_net("inc", 8)
    mem = mod.add_cell("mem0", PrimOp.MEMORY, depth=1024, width=8)
    mod.connect(mem, "RADDR", addr)
    mod.connect(mem, "WADDR", addr)
    mod.connect(mem, "RDATA", rdata, direction="output")
    # wdata = rdata + 1 (feedback loop)
    one = mod.add_net("one", 8)
    oc = mod.add_cell("one_c", PrimOp.CONST, value=1, width=8)
    mod.connect(oc, "Y", one, direction="output")
    add = mod.add_cell("add0", PrimOp.ADD)
    mod.connect(add, "A", rdata)
    mod.connect(add, "B", one)
    mod.connect(add, "Y", inc, direction="output")
    mod.connect(mem, "WDATA", inc)
    detect_write_mode(mod)
    assert mem.params["write_mode"] == "WRITETHROUGH"


# Output register inference (item 18)

def test_output_register_detected():
    """FF directly on BRAM read data with matching clock → output register."""
    mod = Module(name="test")
    clk = mod.add_net("clk", 1)
    rdata = mod.add_net("rdata", 8)
    q = mod.add_net("q", 8)
    mem = mod.add_cell("mem0", PrimOp.MEMORY, depth=1024, width=8)
    mod.connect(mem, "CLK", clk)
    mod.connect(mem, "RDATA", rdata, direction="output")
    ff = mod.add_cell("ff0", PrimOp.FF)
    mod.connect(ff, "CLK", clk)
    mod.connect(ff, "D", rdata)
    mod.connect(ff, "Q", q, direction="output")
    annotated = infer_output_register(mod)
    assert annotated == 1
    assert mem.params["output_register"] is True
    assert mem.params["output_ff"] == "ff0"


def test_output_register_different_clock():
    """FF with different clock should NOT be absorbed as output register."""
    mod = Module(name="test")
    clk1 = mod.add_net("clk1", 1)
    clk2 = mod.add_net("clk2", 1)
    rdata = mod.add_net("rdata", 8)
    q = mod.add_net("q", 8)
    mem = mod.add_cell("mem0", PrimOp.MEMORY, depth=1024, width=8)
    mod.connect(mem, "CLK", clk1)
    mod.connect(mem, "RDATA", rdata, direction="output")
    ff = mod.add_cell("ff0", PrimOp.FF)
    mod.connect(ff, "CLK", clk2)  # different clock
    mod.connect(ff, "D", rdata)
    mod.connect(ff, "Q", q, direction="output")
    annotated = infer_output_register(mod)
    assert annotated == 0
