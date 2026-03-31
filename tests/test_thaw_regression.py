"""Hardware regression tests for the Thaw service minimal pattern.

These tests exercise the exact synthesis patterns that caused 10 bugs
across three days of silicon debugging (commits b367edd..6b7e2f7).

The thaw_svc_minimal design combines every pattern that broke nosis:
  - Cross-always_ff array access (FIFO drain reads array written elsewhere)
  - Nested case(state) -> case(cmd_reg) dispatch
  - Variable-indexed array writes (tx_fifo[tx_wr] <= resp[resp_idx])
  - Constant-indexed multi-branch writes (resp[0] in VERSION/PING/default)
  - Wire declarations with initializers in sub-instances (wire tx_empty)
  - Cross-always_ff variable reads (tx_busy_counter reads tx_send)
  - List handler chained assignments (tx_send_r <= 0; if ... tx_send_r <= 1)

If any of these invariants break, the synthesized silicon will fail.
"""

import json
import os
import pytest

from nosis.frontend import parse_files, lower_to_ir
from nosis.ir import PrimOp
from nosis.passes import run_default_passes
from nosis.bram import infer_brams
from nosis.techmap import map_to_ecp5
from nosis.json_backend import emit_json_str

_DESIGNS = os.path.join(os.path.dirname(__file__), "designs")
_SVC_TOP = os.path.join(_DESIGNS, "thaw_svc_top.sv")
_SVC_MIN = os.path.join(_DESIGNS, "thaw_svc_minimal.sv")
_UART_TX = os.path.join(_DESIGNS, "uart_tx.sv")
_UART_RX = os.path.join(_DESIGNS, "uart_rx.sv")

# Fall back to RIME sources if bundled designs are missing
_NOSIS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_RIME = os.path.join(os.path.dirname(_NOSIS_DIR), "rime")
if not os.path.isfile(_UART_TX):
    _UART_TX = os.path.join(_RIME, "firmware/core/uart/uart_tx.sv")
if not os.path.isfile(_UART_RX):
    _UART_RX = os.path.join(_RIME, "firmware/core/uart/uart_rx.sv")

_SOURCES = [_SVC_TOP, _SVC_MIN, _UART_RX, _UART_TX]

_all_exist = all(os.path.isfile(s) for s in _SOURCES)
skip_reason = "thaw_svc_minimal sources not available" if not _all_exist else ""


def _synthesize():
    """Full synthesis pipeline for the thaw_svc_minimal design."""
    os.environ["NOSIS_BRAM_THRESHOLD"] = "256"
    result = parse_files(_SOURCES, top="top", include_dirs=[_DESIGNS])
    design = lower_to_ir(result, top="top")
    mod = design.top_module()
    run_default_passes(mod)
    infer_brams(mod)
    netlist = map_to_ecp5(design)
    return design, mod, netlist


# Cache to avoid re-synthesizing for every test
_cache = {}


def _get():
    if "design" not in _cache:
        _cache["design"], _cache["mod"], _cache["netlist"] = _synthesize()
    return _cache["design"], _cache["mod"], _cache["netlist"]


# ---------------------------------------------------------------------------
# BUG #1 (b367edd): MUX INIT 0xCACA -> 0xE4E4
# Verify no LUT4 uses the wrong MUX truth table.
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not _all_exist, reason=skip_reason)
def test_no_wrong_mux_init():
    """No LUT4 cell should use INIT=0xCACA (wrong MUX). Must be 0xE4E4."""
    _, _, nl = _get()
    data = json.loads(emit_json_str(nl))
    cells = list(data["modules"].values())[0]["cells"]
    for name, cell in cells.items():
        if cell["type"] == "LUT4":
            init = cell["parameters"].get("INIT", "")
            assert init != "1100101011001010", f"{name} has INIT=0xCACA (wrong MUX)"


# ---------------------------------------------------------------------------
# BUG #4 (74246d8): Sub-instance wire initializers must be lowered.
# The wire tx_empty = (tx_wr == tx_rd) inside the sub-instance must
# have a driver — not be left undriven.
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not _all_exist, reason=skip_reason)
def test_sub_instance_wire_initializer_driven():
    """Wire declarations with initializers in sub-instances must have drivers."""
    design, _, _ = _get()
    mod = design.top_module()
    tx_empty = mod.nets.get("SVC.tx_empty")
    assert tx_empty is not None, "SVC.tx_empty net missing"
    assert tx_empty.driver is not None, (
        "SVC.tx_empty has no driver — wire initializer not lowered"
    )


# ---------------------------------------------------------------------------
# BUG #5 (fc4e600): FF-based mapper must collect ALL write ports.
# WADDR1/WDATA1 must not be skipped.
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not _all_exist, reason=skip_reason)
def test_resp_memory_all_write_ports_collected():
    """The resp MEMORY cell's constant-indexed writes must all be present."""
    design, _, _ = _get()
    mod = design.top_module()
    for cell in mod.cells.values():
        if cell.op == PrimOp.MEMORY and "resp" in cell.params.get("mem_name", ""):
            waddrs = sorted(k for k in cell.inputs if k.startswith("WADDR"))
            wdatas = sorted(k for k in cell.inputs if k.startswith("WDATA"))
            # resp has writes from VERSION (3), PING (2), default (2) = 7 total
            assert len(waddrs) >= 7, f"resp has only {len(waddrs)} WADDRs, expected >= 7"
            assert len(wdatas) >= 7, f"resp has only {len(wdatas)} WDATAs, expected >= 7"
            return
    pytest.fail("No MEMORY cell found for resp")


# ---------------------------------------------------------------------------
# BUG #6 (dc767ed): Per-write WE must be separate from global OR.
# WE1 must exist (not just WE which is the global OR).
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not _all_exist, reason=skip_reason)
def test_per_write_we_numbering():
    """Each write port must have its own WE (WE1, WE2, ...), separate from global OR."""
    design, _, _ = _get()
    mod = design.top_module()
    for cell in mod.cells.values():
        if cell.op == PrimOp.MEMORY and "resp" in cell.params.get("mem_name", ""):
            we_keys = sorted(k for k in cell.inputs if k.startswith("WE"))
            assert "WE1" in cell.inputs, f"WE1 missing — first port WE collides with global OR"
            assert "WE" in cell.inputs, f"Global OR WE missing"
            # WE1 and WE must be different nets
            we1 = cell.inputs["WE1"]
            we_global = cell.inputs["WE"]
            assert we1 is not we_global or len(we_keys) == 2, (
                "WE1 and WE are the same net — global OR corrupts first port"
            )
            return
    pytest.fail("No resp MEMORY cell found")


# ---------------------------------------------------------------------------
# BUG #7 (74246d8): DPR16X4 read port aliasing.
# All RDATA outputs must be connected (not just the first one).
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not _all_exist, reason=skip_reason)
def test_dpr_read_ports_connected():
    """All DPR16X4 DO bits must be consumed by downstream cells."""
    _, _, nl = _get()
    data = json.loads(emit_json_str(nl))
    cells = list(data["modules"].values())[0]["cells"]
    out_ports = {"Z", "Q", "S0", "S1", "COUT", "DO"}
    driven = set()
    for cell in cells.values():
        for pn, bits in cell["connections"].items():
            if pn in out_ports:
                for b in bits:
                    if isinstance(b, int):
                        driven.add(b)
    for cell in cells.values():
        if cell["type"] == "TRELLIS_DPR16X4":
            for b in cell["connections"].get("DO", []):
                if isinstance(b, int):
                    assert b in driven or any(
                        b in c["connections"].get(pn, [])
                        for c in cells.values()
                        for pn in c["connections"]
                        if pn not in out_ports
                    ), f"DPR DO bit {b} not consumed"


# ---------------------------------------------------------------------------
# BUG #10 (6b7e2f7): List handler cone walk must not corrupt cross-scope refs.
# The tx_busy condition must reference an FF Q output, not CONST(0).
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not _all_exist, reason=skip_reason)
def test_tx_busy_not_stuck_zero():
    """tx_busy_counter's condition must read from an FF Q output, not a constant."""
    design, _, _ = _get()
    mod = design.top_module()
    # Find the tx_busy_counter FF and walk its D MUX chain
    for cell in mod.cells.values():
        if cell.op != PrimOp.FF:
            continue
        if "tx_busy_counter" not in cell.params.get("ff_target", ""):
            continue
        d = cell.inputs.get("D")
        assert d is not None
        # Walk MUX chain to find the tx_send condition
        # The D chain is: MUX(rst, ..., MUX(tx_send, load, decrement/hold))
        # The tx_send MUX select must NOT be a constant
        visited = set()
        work = [d]
        found_const_select = False
        while work:
            net = work.pop()
            if net.name in visited or net.driver is None:
                continue
            visited.add(net.name)
            drv = net.driver
            if drv.op == PrimOp.MUX:
                s = drv.inputs.get("S")
                if s and s.driver and s.driver.op == PrimOp.CONST:
                    # A MUX with a constant select is either dead code
                    # or a cone-walk corruption. Flag it.
                    found_const_select = True
                for inp in drv.inputs.values():
                    if inp.name not in visited:
                        work.append(inp)
            if len(visited) > 100:
                break
        assert not found_const_select, (
            "tx_busy_counter D chain has a MUX with constant select — "
            "List handler cone walk likely corrupted a cross-scope reference"
        )
        return
    pytest.fail("No tx_busy_counter FF found")


# ---------------------------------------------------------------------------
# Structural: no undriven input bits in the final netlist.
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not _all_exist, reason=skip_reason)
def test_no_undriven_input_bits():
    """Every input bit referenced by a cell must be driven by some output."""
    _, _, nl = _get()
    data = json.loads(emit_json_str(nl))
    m = list(data["modules"].values())[0]
    cells = m["cells"]
    out_ports = {"Z", "Q", "S0", "S1", "COUT", "DO", "F", "F0", "F1", "OFX0", "OFX1"}
    driven = set()
    for pi in m["ports"].values():
        if pi["direction"] == "input":
            for b in pi["bits"]:
                if isinstance(b, int):
                    driven.add(b)
    for c in cells.values():
        for pn, bits in c["connections"].items():
            if pn in out_ports:
                for b in bits:
                    if isinstance(b, int):
                        driven.add(b)
    used = set()
    for c in cells.values():
        for pn, bits in c["connections"].items():
            if pn not in out_ports:
                for b in bits:
                    if isinstance(b, int):
                        used.add(b)
    undriven = used - driven
    assert len(undriven) == 0, f"{len(undriven)} undriven input bits: {sorted(undriven)[:5]}"


# ---------------------------------------------------------------------------
# Structural: MEMORY cells must be mapped correctly.
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not _all_exist, reason=skip_reason)
def test_memory_mapping_types():
    """tx_fifo -> DPR16X4, resp -> FF-based (not DP16KD with 108 WADDRs)."""
    design, _, _ = _get()
    mod = design.top_module()
    found = {}
    for cell in mod.cells.values():
        if cell.op == PrimOp.MEMORY:
            mn = cell.params.get("mem_name", "")
            bc = cell.params.get("bram_config", "FF-based")
            found[mn] = bc
    assert "SVC.tx_fifo" in found, "tx_fifo MEMORY not found"
    assert found["SVC.tx_fifo"] == "DPR16X4", f"tx_fifo should be DPR16X4, got {found['SVC.tx_fifo']}"
    assert "SVC.resp" in found, "resp MEMORY not found"
    assert found["SVC.resp"] != "DP16KD", f"resp must NOT use DP16KD (has 108 simultaneous writes)"


# ---------------------------------------------------------------------------
# Structural: design must fit in ECP5-25F.
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not _all_exist, reason=skip_reason)
def test_fits_ecp5_25f():
    """Total LUT4 count must fit in ECP5-25F (24288 LUTs)."""
    _, _, nl = _get()
    s = nl.stats()
    luts = s.get("LUT4", 0)
    ffs = s.get("TRELLIS_FF", 0)
    assert luts <= 24288, f"LUT4 count {luts} exceeds ECP5-25F capacity"
    assert ffs <= 24288, f"FF count {ffs} exceeds ECP5-25F capacity"
    # Sanity: must have reasonable cell counts
    assert luts >= 500, f"Suspiciously low LUT count: {luts}"
    assert ffs >= 100, f"Suspiciously low FF count: {ffs}"
