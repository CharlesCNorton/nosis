"""Tests for MUX chain merging and EQ deduplication in passes.py."""

from nosis.ir import Module, PrimOp
from nosis.passes import merge_mux_chains, run_default_passes


def _make_eq_mux_pair(mod, sel_net, case_val, case_data_net, default_net, out_name, idx):
    """Build an EQ+MUX pair: MUX(EQ(sel, case_val), default, case_data)."""
    const_net = mod.add_net(f"cv_{idx}", sel_net.width)
    cc = mod.add_cell(f"cv_{idx}", PrimOp.CONST, value=case_val, width=sel_net.width)
    mod.connect(cc, "Y", const_net, direction="output")

    eq_out = mod.add_net(f"eq_{idx}", 1)
    eq = mod.add_cell(f"eq_{idx}", PrimOp.EQ)
    mod.connect(eq, "A", sel_net)
    mod.connect(eq, "B", const_net)
    mod.connect(eq, "Y", eq_out, direction="output")

    mux_out = mod.add_net(out_name, default_net.width)
    mux = mod.add_cell(f"mux_{idx}", PrimOp.MUX)
    mod.connect(mux, "S", eq_out)
    mod.connect(mux, "A", default_net)
    mod.connect(mux, "B", case_data_net)
    mod.connect(mux, "Y", mux_out, direction="output")
    return eq, mux, mux_out


def test_dedup_shared_eq():
    """Two EQ cells comparing the same selector against the same constant should dedup."""
    mod = Module(name="test")
    sel = mod.add_net("sel", 3)
    c0 = mod.add_net("c0", 3)
    cc0 = mod.add_cell("cc0", PrimOp.CONST, value=5, width=3)
    mod.connect(cc0, "Y", c0, direction="output")

    c1 = mod.add_net("c1", 3)
    cc1 = mod.add_cell("cc1", PrimOp.CONST, value=5, width=3)
    mod.connect(cc1, "Y", c1, direction="output")

    eq_out0 = mod.add_net("eq0", 1)
    eq0 = mod.add_cell("eq0", PrimOp.EQ)
    mod.connect(eq0, "A", sel)
    mod.connect(eq0, "B", c0)
    mod.connect(eq0, "Y", eq_out0, direction="output")

    eq_out1 = mod.add_net("eq1", 1)
    eq1 = mod.add_cell("eq1", PrimOp.EQ)
    mod.connect(eq1, "A", sel)
    mod.connect(eq1, "B", c1)
    mod.connect(eq1, "Y", eq_out1, direction="output")

    # Consumer of eq1 should be redirected to eq0's output
    y = mod.add_net("y", 1)
    gc = mod.add_cell("and0", PrimOp.AND)
    mod.connect(gc, "A", eq_out0)
    mod.connect(gc, "B", eq_out1)
    mod.connect(gc, "Y", y, direction="output")

    before = len(mod.cells)
    eliminated = merge_mux_chains(mod)
    assert eliminated == 1
    assert len(mod.cells) == before - 1
    # and0 should now read eq0's output for both inputs
    assert mod.cells["and0"].inputs["A"] is eq_out0
    assert mod.cells["and0"].inputs["B"] is eq_out0


def test_no_dedup_different_constants():
    """EQs comparing against different constants must NOT be deduped."""
    mod = Module(name="test")
    sel = mod.add_net("sel", 3)
    c0 = mod.add_net("c0", 3)
    cc0 = mod.add_cell("cc0", PrimOp.CONST, value=5, width=3)
    mod.connect(cc0, "Y", c0, direction="output")
    c1 = mod.add_net("c1", 3)
    cc1 = mod.add_cell("cc1", PrimOp.CONST, value=7, width=3)
    mod.connect(cc1, "Y", c1, direction="output")

    eq_out0 = mod.add_net("eq0", 1)
    eq0 = mod.add_cell("eq0", PrimOp.EQ)
    mod.connect(eq0, "A", sel)
    mod.connect(eq0, "B", c0)
    mod.connect(eq0, "Y", eq_out0, direction="output")

    eq_out1 = mod.add_net("eq1", 1)
    eq1 = mod.add_cell("eq1", PrimOp.EQ)
    mod.connect(eq1, "A", sel)
    mod.connect(eq1, "B", c1)
    mod.connect(eq1, "Y", eq_out1, direction="output")

    eliminated = merge_mux_chains(mod)
    assert eliminated == 0


def test_identical_branch_mux_eliminated():
    """MUX(sel, x, x) should be eliminated — output is always x."""
    mod = Module(name="test")
    s = mod.add_net("s", 1)
    x = mod.add_net("x", 8)
    y = mod.add_net("y", 8)

    mux = mod.add_cell("mux0", PrimOp.MUX)
    mod.connect(mux, "S", s)
    mod.connect(mux, "A", x)
    mod.connect(mux, "B", x)  # same as A
    mod.connect(mux, "Y", y, direction="output")

    # Consumer
    z = mod.add_net("z", 8)
    gc = mod.add_cell("not0", PrimOp.NOT)
    mod.connect(gc, "A", y)
    mod.connect(gc, "Y", z, direction="output")

    eliminated = merge_mux_chains(mod)
    assert eliminated >= 1
    # not0 should now read from x directly
    assert mod.cells["not0"].inputs["A"] is x


def test_mux_merge_on_real_design():
    """MUX chain merging on uart_tx should eliminate some cells."""
    import os
    os.environ.setdefault("NOSIS_PYSLANG_PATH", "D:/slang/build/lib")
    from nosis.frontend import parse_files, lower_to_ir
    from tests.conftest import RIME_UART_TX

    r = parse_files([RIME_UART_TX], top="uart_tx")
    d = lower_to_ir(r, top="uart_tx")
    m = d.top_module()
    before = m.stats()["cells"]
    stats = run_default_passes(m)
    after = m.stats()["cells"]
    # Optimization should reduce cell count
    assert after < before


def test_mux_merge_never_increases_cells():
    """merge_mux_chains must never increase the cell count."""
    mod = Module(name="test")
    a = mod.add_net("a", 1)
    b = mod.add_net("b", 1)
    y = mod.add_net("y", 1)
    gc = mod.add_cell("and0", PrimOp.AND)
    mod.connect(gc, "A", a)
    mod.connect(gc, "B", b)
    mod.connect(gc, "Y", y, direction="output")

    before = len(mod.cells)
    merge_mux_chains(mod)
    assert len(mod.cells) <= before


def test_mux_to_and_zero_b():
    """MUX(sel, A, 0) should become AND(NOT(sel), A)."""
    from nosis.passes import _simplify_mux_with_zero
    mod = Module(name="test")
    s = mod.add_net("s", 1)
    a = mod.add_net("a", 8)
    zero = mod.add_net("zero", 8)
    y = mod.add_net("y", 8)
    zc = mod.add_cell("zc", PrimOp.CONST, value=0, width=8)
    mod.connect(zc, "Y", zero, direction="output")
    mux = mod.add_cell("mux0", PrimOp.MUX)
    mod.connect(mux, "S", s)
    mod.connect(mux, "A", a)
    mod.connect(mux, "B", zero)
    mod.connect(mux, "Y", y, direction="output")

    replaced = _simplify_mux_with_zero(mod)
    assert replaced == 1
    assert mod.cells["mux0"].op == PrimOp.AND


def test_mux_to_and_zero_a():
    """MUX(sel, 0, B) should become AND(sel, B)."""
    from nosis.passes import _simplify_mux_with_zero
    mod = Module(name="test")
    s = mod.add_net("s", 1)
    b = mod.add_net("b", 8)
    zero = mod.add_net("zero", 8)
    y = mod.add_net("y", 8)
    zc = mod.add_cell("zc", PrimOp.CONST, value=0, width=8)
    mod.connect(zc, "Y", zero, direction="output")
    mux = mod.add_cell("mux0", PrimOp.MUX)
    mod.connect(mux, "S", s)
    mod.connect(mux, "A", zero)
    mod.connect(mux, "B", b)
    mod.connect(mux, "Y", y, direction="output")

    replaced = _simplify_mux_with_zero(mod)
    assert replaced == 1
    assert mod.cells["mux0"].op == PrimOp.AND
    assert mod.cells["mux0"].inputs["A"] is s
    assert mod.cells["mux0"].inputs["B"] is b


def test_mux_to_and_preserves_nonzero():
    """MUX with non-zero constant should NOT be converted."""
    from nosis.passes import _simplify_mux_with_zero
    mod = Module(name="test")
    s = mod.add_net("s", 1)
    a = mod.add_net("a", 8)
    b = mod.add_net("b", 8)
    y = mod.add_net("y", 8)
    bc = mod.add_cell("bc", PrimOp.CONST, value=42, width=8)
    mod.connect(bc, "Y", b, direction="output")
    mux = mod.add_cell("mux0", PrimOp.MUX)
    mod.connect(mux, "S", s)
    mod.connect(mux, "A", a)
    mod.connect(mux, "B", b)
    mod.connect(mux, "Y", y, direction="output")

    replaced = _simplify_mux_with_zero(mod)
    assert replaced == 0
    assert mod.cells["mux0"].op == PrimOp.MUX


def test_output_ports_survive_optimization():
    """Output ports with drivers before optimization must retain drivers after.

    Known issue: some SoC top-level output ports (usb_tx, led, sdram_*)
    are undriven even BEFORE optimization due to a hierarchy port wiring
    limitation in _lower_sub_instance. This test only checks ports that
    HAD drivers before optimization.
    """
    import os
    os.environ.setdefault("NOSIS_PYSLANG_PATH", "D:/slang/build/lib")
    from nosis.frontend import parse_files, lower_to_ir
    from nosis.ir import PrimOp
    from tests.conftest import RIME_UART_TX

    # Test on uart_tx which has all ports properly wired
    r = parse_files([RIME_UART_TX], top="uart_tx")
    d = lower_to_ir(r, top="uart_tx")
    m = d.top_module()

    # Record driven output ports before optimization
    driven_before = set()
    for pname, pnet in m.ports.items():
        is_output = any(
            c.op == PrimOp.OUTPUT and any(inp.name == pname for inp in c.inputs.values())
            for c in m.cells.values()
        )
        if is_output and pnet.driver is not None:
            driven_before.add(pname)

    run_default_passes(m)

    # Every port that was driven before must still be driven
    lost = []
    for pname in driven_before:
        pnet = m.ports.get(pname)
        if pnet and pnet.driver is None:
            lost.append(pname)

    assert len(lost) == 0, f"optimization removed drivers for: {lost}"


def test_uart_tx_has_logic_after_optimization():
    """uart_tx must retain combinational logic after optimization — it's a real design."""
    import os
    os.environ.setdefault("NOSIS_PYSLANG_PATH", "D:/slang/build/lib")
    from nosis.frontend import parse_files, lower_to_ir
    from nosis.ir import PrimOp
    from tests.conftest import RIME_UART_TX

    r = parse_files([RIME_UART_TX], top="uart_tx")
    d = lower_to_ir(r, top="uart_tx")
    m = d.top_module()
    run_default_passes(m)

    ffs = sum(1 for c in m.cells.values() if c.op == PrimOp.FF)
    comb = sum(1 for c in m.cells.values()
               if c.op not in (PrimOp.INPUT, PrimOp.OUTPUT, PrimOp.FF, PrimOp.CONST))
    assert ffs >= 3, f"uart_tx must retain at least 3 FFs, got {ffs}"
    assert comb >= 5, f"uart_tx must retain combinational logic, got {comb} comb cells"


def test_soc_ff_count_after_optimization():
    """SoC must retain a substantial number of FFs — it has CPUs, UARTs, SPI, SDRAM."""
    import os
    os.environ.setdefault("NOSIS_PYSLANG_PATH", "D:/slang/build/lib")
    from nosis.frontend import parse_files, lower_to_ir
    from nosis.ir import PrimOp
    from tests.conftest import RIME_SOC_SOURCES

    r = parse_files(RIME_SOC_SOURCES, top="top")
    d = lower_to_ir(r, top="top")
    m = d.top_module()
    run_default_passes(m)

    ffs = sum(1 for c in m.cells.values() if c.op == PrimOp.FF)
    assert ffs >= 500, f"SoC must retain at least 500 FFs, got {ffs}"


def test_soc_lut_count_regression():
    """SoC LUT count after full pipeline must not regress above 5500 slices."""
    import os
    os.environ.setdefault("NOSIS_PYSLANG_PATH", "D:/slang/build/lib")
    from nosis.frontend import parse_files, lower_to_ir
    from nosis.techmap import map_to_ecp5
    from nosis.slicepack import pack_slices
    from tests.conftest import RIME_SOC_SOURCES

    r = parse_files(RIME_SOC_SOURCES, top="top")
    d = lower_to_ir(r, top="top")
    m = d.top_module()
    run_default_passes(m)
    nl = map_to_ecp5(d)
    pack_slices(nl)
    luts = nl.stats().get("TRELLIS_SLICE", 0)
    assert luts < 3100, f"SoC LUT count regressed to {luts}"


def test_uart_tx_lut_count():
    """uart_tx after full pipeline should be under 25 slices."""
    import os
    os.environ.setdefault("NOSIS_PYSLANG_PATH", "D:/slang/build/lib")
    from nosis.frontend import parse_files, lower_to_ir
    from nosis.techmap import map_to_ecp5
    from nosis.slicepack import pack_slices
    from tests.conftest import RIME_UART_TX

    r = parse_files([RIME_UART_TX], top="uart_tx")
    d = lower_to_ir(r, top="uart_tx")
    m = d.top_module()
    run_default_passes(m)
    nl = map_to_ecp5(d)
    pack_slices(nl)
    luts = nl.stats().get("TRELLIS_SLICE", 0)
    assert luts < 12, f"uart_tx LUT count regressed to {luts}"
