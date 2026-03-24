"""Tests for nosis.clocks — clock domain analysis."""

from nosis.ir import Module, PrimOp
from nosis.clocks import analyze_clock_domains, insert_synchronizers


def test_single_domain():
    mod = Module(name="test")
    clk = mod.add_net("clk", 1)
    cc = mod.add_cell("clk_p", PrimOp.INPUT, port_name="clk")
    mod.connect(cc, "Y", clk, direction="output")
    mod.ports["clk"] = clk
    d = mod.add_net("d", 1)
    q = mod.add_net("q", 1)
    ff = mod.add_cell("ff0", PrimOp.FF)
    mod.connect(ff, "CLK", clk)
    mod.connect(ff, "D", d)
    mod.connect(ff, "Q", q, direction="output")
    domains, crossings = analyze_clock_domains(mod)
    assert len(domains) == 1
    assert domains[0].clock_net == "clk"
    assert "ff0" in domains[0].ff_cells
    assert len(crossings) == 0


def test_two_domains_no_crossing():
    mod = Module(name="test")
    clk_a = mod.add_net("clk_a", 1)
    clk_b = mod.add_net("clk_b", 1)
    d1 = mod.add_net("d1", 1)
    d2 = mod.add_net("d2", 1)
    q1 = mod.add_net("q1", 1)
    q2 = mod.add_net("q2", 1)
    ff1 = mod.add_cell("ff1", PrimOp.FF)
    mod.connect(ff1, "CLK", clk_a)
    mod.connect(ff1, "D", d1)
    mod.connect(ff1, "Q", q1, direction="output")
    ff2 = mod.add_cell("ff2", PrimOp.FF)
    mod.connect(ff2, "CLK", clk_b)
    mod.connect(ff2, "D", d2)
    mod.connect(ff2, "Q", q2, direction="output")
    domains, crossings = analyze_clock_domains(mod)
    assert len(domains) == 2
    assert len(crossings) == 0


def test_crossing_detected():
    mod = Module(name="test")
    clk_a = mod.add_net("clk_a", 1)
    clk_b = mod.add_net("clk_b", 1)
    d1 = mod.add_net("d1", 1)
    q1 = mod.add_net("q1", 1)
    q2 = mod.add_net("q2", 1)
    ff1 = mod.add_cell("ff1", PrimOp.FF)
    mod.connect(ff1, "CLK", clk_a)
    mod.connect(ff1, "D", d1)
    mod.connect(ff1, "Q", q1, direction="output")
    ff2 = mod.add_cell("ff2", PrimOp.FF)
    mod.connect(ff2, "CLK", clk_b)
    mod.connect(ff2, "D", q1)
    mod.connect(ff2, "Q", q2, direction="output")
    domains, crossings = analyze_clock_domains(mod)
    assert len(domains) == 2
    assert len(crossings) == 1
    assert crossings[0].source_domain == "clk_a"
    assert crossings[0].dest_domain == "clk_b"
    assert crossings[0].source_ff == "ff1"
    assert crossings[0].dest_ff == "ff2"


def test_crossing_through_logic():
    mod = Module(name="test")
    clk_a = mod.add_net("clk_a", 1)
    clk_b = mod.add_net("clk_b", 1)
    d1 = mod.add_net("d1", 1)
    q1 = mod.add_net("q1", 1)
    mid = mod.add_net("mid", 1)
    q2 = mod.add_net("q2", 1)
    const1 = mod.add_net("c1", 1)
    c1_cell = mod.add_cell("c1", PrimOp.CONST, value=1, width=1)
    mod.connect(c1_cell, "Y", const1, direction="output")
    ff1 = mod.add_cell("ff1", PrimOp.FF)
    mod.connect(ff1, "CLK", clk_a)
    mod.connect(ff1, "D", d1)
    mod.connect(ff1, "Q", q1, direction="output")
    and_cell = mod.add_cell("and0", PrimOp.AND)
    mod.connect(and_cell, "A", q1)
    mod.connect(and_cell, "B", const1)
    mod.connect(and_cell, "Y", mid, direction="output")
    ff2 = mod.add_cell("ff2", PrimOp.FF)
    mod.connect(ff2, "CLK", clk_b)
    mod.connect(ff2, "D", mid)
    mod.connect(ff2, "Q", q2, direction="output")
    domains, crossings = analyze_clock_domains(mod)
    assert len(crossings) == 1
    assert crossings[0].source_domain == "clk_a"
    assert crossings[0].dest_domain == "clk_b"


def test_no_ffs_no_domains():
    mod = Module(name="test")
    mod.add_net("a", 1)
    mod.add_net("y", 1)
    domains, crossings = analyze_clock_domains(mod)
    assert len(domains) == 0
    assert len(crossings) == 0


def test_ff_without_clk_ignored():
    """An FF missing a CLK input should not crash the analysis."""
    mod = Module(name="test")
    d = mod.add_net("d", 1)
    q = mod.add_net("q", 1)
    ff = mod.add_cell("ff0", PrimOp.FF)
    mod.connect(ff, "D", d)
    mod.connect(ff, "Q", q, direction="output")
    # No CLK connection
    domains, crossings = analyze_clock_domains(mod)
    assert len(domains) == 0  # FF without CLK is not assigned to any domain


def test_multiple_crossings():
    """Three domains with two crossings."""
    mod = Module(name="test")
    clk_a = mod.add_net("clk_a", 1)
    clk_b = mod.add_net("clk_b", 1)
    clk_c = mod.add_net("clk_c", 1)
    d = mod.add_net("d", 1)
    q1 = mod.add_net("q1", 1)
    q2 = mod.add_net("q2", 1)
    q3 = mod.add_net("q3", 1)

    ff1 = mod.add_cell("ff1", PrimOp.FF)
    mod.connect(ff1, "CLK", clk_a)
    mod.connect(ff1, "D", d)
    mod.connect(ff1, "Q", q1, direction="output")

    ff2 = mod.add_cell("ff2", PrimOp.FF)
    mod.connect(ff2, "CLK", clk_b)
    mod.connect(ff2, "D", q1)  # A -> B crossing
    mod.connect(ff2, "Q", q2, direction="output")

    ff3 = mod.add_cell("ff3", PrimOp.FF)
    mod.connect(ff3, "CLK", clk_c)
    mod.connect(ff3, "D", q2)  # B -> C crossing
    mod.connect(ff3, "Q", q3, direction="output")

    domains, crossings = analyze_clock_domains(mod)
    assert len(domains) == 3
    assert len(crossings) == 2


def test_domain_output_nets_tracked():
    """Each domain must track its FF output nets."""
    mod = Module(name="test")
    clk = mod.add_net("clk", 1)
    d = mod.add_net("d", 1)
    q = mod.add_net("q", 1)
    ff = mod.add_cell("ff0", PrimOp.FF)
    mod.connect(ff, "CLK", clk)
    mod.connect(ff, "D", d)
    mod.connect(ff, "Q", q, direction="output")
    domains, _ = analyze_clock_domains(mod)
    assert len(domains) == 1
    assert "q" in domains[0].output_nets


def test_insert_synchronizers():
    """Synchronizer insertion must add 2 FFs per crossing."""
    mod = Module(name="test")
    clk_a = mod.add_net("clk_a", 1)
    clk_b = mod.add_net("clk_b", 1)
    d = mod.add_net("d", 1)
    q1 = mod.add_net("q1", 1)
    q2 = mod.add_net("q2", 1)
    ff1 = mod.add_cell("ff1", PrimOp.FF)
    mod.connect(ff1, "CLK", clk_a)
    mod.connect(ff1, "D", d)
    mod.connect(ff1, "Q", q1, direction="output")
    ff2 = mod.add_cell("ff2", PrimOp.FF)
    mod.connect(ff2, "CLK", clk_b)
    mod.connect(ff2, "D", q1)
    mod.connect(ff2, "Q", q2, direction="output")

    _, crossings = analyze_clock_domains(mod)
    assert len(crossings) == 1

    cells_before = len(mod.cells)
    inserted = insert_synchronizers(mod, crossings)
    assert inserted == 1
    assert len(mod.cells) == cells_before + 2  # 2 sync FFs added

    # Sync FFs should be tagged
    sync_cells = [c for c in mod.cells.values() if c.attributes.get("cdc_sync")]
    assert len(sync_cells) == 2
    stages = {c.attributes["cdc_sync"] for c in sync_cells}
    assert stages == {"stage1", "stage2"}
