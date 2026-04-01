"""Hardware regression tests for bugs found and fixed during RIME-V bring-up.

Each test targets a specific bug that was confirmed on silicon or through
IR simulation. The tests exercise the synthesis pipeline and check
structural invariants that, if violated, would produce non-functional
hardware.

These tests are INDEPENDENT of the ongoing RIME-V UART debugging.
They cover fixes that are silicon-verified (Thaw) or IR-simulation-verified.
"""

import json
import os
import pytest

from nosis.frontend import parse_files, lower_to_ir
from nosis.ir import PrimOp
from nosis.passes import run_default_passes
from nosis.bram import infer_brams
from nosis.techmap import map_to_ecp5
from nosis.slicepack import pack_slices
from nosis.json_backend import emit_json_str

_DESIGNS = os.path.join(os.path.dirname(__file__), "designs")
# RIME source tree location — set RIME_ROOT env var or skip these tests
_RIME = os.environ.get("RIME_ROOT", "")
_CPU_SV = os.path.join(_RIME, "firmware/core/cpu/rime_v.sv") if _RIME else ""

_cpu_exists = os.path.isfile(_CPU_SV)


# ---------------------------------------------------------------------------
# DP16KD chip select: CSDECODE must match CS pin values.
# Bug: CSDECODE_A="0b000" with CSA0="1" → port permanently deselected,
# reads always return zero. Silicon-confirmed: BRAM read test returned
# all-zero until CS was fixed to match.
# ---------------------------------------------------------------------------

def test_dp16kd_chip_select_matches_decode():
    """DP16KD CSA/CSB pins must match CSDECODE_A/CSDECODE_B values."""
    # Synthesize Thaw (has DPR16X4 but tests the DP16KD mapper path too)
    # Use the CPU standalone to get DP16KD (register file)
    if not _cpu_exists:
        pytest.skip("rime_v.sv not available")
    os.environ["NOSIS_BRAM_THRESHOLD"] = "0"
    result = parse_files([_CPU_SV], top="rime_v")
    design = lower_to_ir(result, top="rime_v")
    mod = design.top_module()
    run_default_passes(mod)
    infer_brams(mod)
    netlist = map_to_ecp5(design)
    pack_slices(netlist)
    data = json.loads(emit_json_str(netlist))
    cells = list(data["modules"].values())[0]["cells"]
    for name, cell in cells.items():
        if cell["type"] != "DP16KD":
            continue
        for port_label in ("A", "B"):
            decode = cell["parameters"].get(f"CSDECODE_{port_label}", "0b000")
            # Parse decode value
            if decode.startswith("0b"):
                decode_val = int(decode[2:], 2)
            else:
                decode_val = int(decode)
            # Read CS pin values
            cs0 = cell["connections"].get(f"CS{port_label}0", ["0"])[0]
            cs1 = cell["connections"].get(f"CS{port_label}1", ["0"])[0]
            cs2 = cell["connections"].get(f"CS{port_label}2", ["0"])[0]
            # All CS must be constant strings matching decode
            cs_val = 0
            if cs0 == "1": cs_val |= 1
            if cs1 == "1": cs_val |= 2
            if cs2 == "1": cs_val |= 4
            # Non-constant CS pins (signal bits) are OK — they're dynamic
            if isinstance(cs0, str) and isinstance(cs1, str) and isinstance(cs2, str):
                assert cs_val == decode_val, (
                    f"{name} port {port_label}: CS={cs_val} != CSDECODE={decode_val} "
                    f"— BRAM port permanently deselected"
                )


# ---------------------------------------------------------------------------
# DP16KD clock: read-only BRAMs must have a clock connected.
# Bug: MEMORY cells used only for combinational reads (assign data = mem[addr])
# never got a CLK from the frontend. DP16KD CLKA was tied to "0" — address
# never latched, reads returned stale/zero data.
# ---------------------------------------------------------------------------

def test_dp16kd_clock_not_zero():
    """DP16KD CLKA and CLKB must not be tied to constant zero."""
    if not _cpu_exists:
        pytest.skip("rime_v.sv not available")
    os.environ["NOSIS_BRAM_THRESHOLD"] = "0"
    result = parse_files([_CPU_SV], top="rime_v")
    design = lower_to_ir(result, top="rime_v")
    mod = design.top_module()
    run_default_passes(mod)
    infer_brams(mod)
    netlist = map_to_ecp5(design)
    pack_slices(netlist)
    data = json.loads(emit_json_str(netlist))
    cells = list(data["modules"].values())[0]["cells"]
    for name, cell in cells.items():
        if cell["type"] != "DP16KD":
            continue
        clka = cell["connections"].get("CLKA", ["0"])[0]
        assert clka != "0" and clka != 0, (
            f"{name}: CLKA tied to constant 0 — BRAM never latches address"
        )


# ---------------------------------------------------------------------------
# DP16KD X36 data wiring: 32-bit memories need both DOA and DOB for read.
# Bug: only DOA[17:0] was wired, leaving bits [31:18] undriven.
# ---------------------------------------------------------------------------

def test_dp16kd_x36_all_bits_driven():
    """DP16KD in X36 mode must wire both DOA and DOB to rdata bits."""
    if not _cpu_exists:
        pytest.skip("rime_v.sv not available")
    os.environ["NOSIS_BRAM_THRESHOLD"] = "0"
    result = parse_files([_CPU_SV], top="rime_v")
    design = lower_to_ir(result, top="rime_v")
    mod = design.top_module()
    run_default_passes(mod)
    infer_brams(mod)
    netlist = map_to_ecp5(design)
    pack_slices(netlist)
    data = json.loads(emit_json_str(netlist))
    cells = list(data["modules"].values())[0]["cells"]
    for name, cell in cells.items():
        if cell["type"] != "DP16KD":
            continue
        dw = cell["parameters"].get("DATA_WIDTH_A", "18")
        if dw != "36":
            continue
        # For X36, DOB should have signal bits (not just alloc'd unused bits)
        # Check that DOB bits are consumed by at least one other cell
        dob_bits = set()
        for i in range(18):
            b = cell["connections"].get(f"DOB{i}", ["0"])[0]
            if isinstance(b, int) and b >= 2:
                dob_bits.add(b)
        assert len(dob_bits) > 0, (
            f"{name}: X36 mode but DOB has no signal bits — upper data undriven"
        )


# ---------------------------------------------------------------------------
# collapse_case_chains: must not collapse chains with partial constant coverage.
# Bug: case arms with non-CONST outputs were dropped from the case map.
# Uncaptured selector values fell through to default_val=0, incorrectly
# declaring the entire chain as constant. The CPU state machine was
# collapsed to constant 0 (S_FETCH), preventing execution.
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not _cpu_exists, reason="rime_v.sv not available")
def test_cpu_state_advances_after_optimization():
    """CPU state machine must advance from S_FETCH after optimization."""
    from nosis.sim import FastSimulator
    os.environ["NOSIS_BRAM_THRESHOLD"] = "0"
    result = parse_files([_CPU_SV], top="rime_v")
    design = lower_to_ir(result, top="rime_v")
    mod = design.top_module()
    run_default_passes(mod)

    # Find state FF
    state_q_name = None
    state_d_name = None
    for c in mod.cells.values():
        if c.op == PrimOp.FF and c.params.get("ff_target", "") == "state":
            q = list(c.outputs.values())[0]
            state_q_name = q.name
            d = c.inputs.get("D")
            state_d_name = d.name if d else None
            break
    assert state_q_name is not None, "state FF not found after optimization"
    assert state_d_name is not None, "state FF has no D input"

    sim = FastSimulator(mod)
    # Reset
    for _ in range(5):
        sim.step({"clk": 1, "rst": 1, "imem_data": 0, "mem_done": 0,
                  "mem_done_rdata": 0, "irq": 0})
    # Run with a valid instruction (lui sp, 0x4000)
    vals = sim.step({"clk": 1, "rst": 0, "imem_data": 0x00004137,
                     "mem_done": 0, "mem_done_rdata": 0, "irq": 0})
    state_d = vals.get(state_d_name, -1)
    # S_FETCH2 = 11. After reset release with a valid instruction,
    # the D input to the state FF must be S_FETCH2 (not stuck at S_FETCH=0).
    assert state_d == 11, (
        f"state D input = {state_d} after reset release, expected 11 (S_FETCH2). "
        f"collapse_case_chains likely corrupted the state machine."
    )


# ---------------------------------------------------------------------------
# Function parameter binding: inlined function args must be wired.
# Bug: expand_compressed(ci) and read_csr(csr_addr) were inlined but the
# formal parameters (ci, csr_addr) were never connected to actual arguments.
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not _cpu_exists, reason="rime_v.sv not available")
def test_function_parameters_driven():
    """Inlined function parameters must have drivers (not left undriven)."""
    os.environ["NOSIS_BRAM_THRESHOLD"] = "0"
    result = parse_files([_CPU_SV], top="rime_v")
    design = lower_to_ir(result, top="rime_v")
    mod = design.top_module()
    # Check BEFORE optimization (optimization may DCE the nets)
    for name in ("ci", "csr_addr"):
        net = mod.nets.get(name)
        if net is None:
            continue  # may be prefixed differently
        assert net.driver is not None, (
            f"Function parameter '{name}' has no driver — "
            f"function inlining did not bind the actual argument"
        )


# ---------------------------------------------------------------------------
# Iterative pack_slices: multiple rounds must reduce more than one round.
# Bug: single-pass merge missed feeders that became single-fanout after
# prior merges. The gap between nosis and PnR capacity was 4%.
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not _cpu_exists, reason="rime_v.sv not available")
def test_iterative_merge_reduces_more_than_single_pass():
    """pack_slices iterative merge must reduce beyond a single merge round."""
    from nosis.slicepack_merge import merge_lut_chains_safe
    os.environ["NOSIS_BRAM_THRESHOLD"] = "0"
    result = parse_files([_CPU_SV], top="rime_v")
    design = lower_to_ir(result, top="rime_v")
    mod = design.top_module()
    run_default_passes(mod)
    infer_brams(mod)
    netlist = map_to_ecp5(design)
    # Single pass
    round1 = merge_lut_chains_safe(netlist)
    round2 = merge_lut_chains_safe(netlist)
    # The second round should find additional merges (feeders that became
    # single-fanout after round 1 eliminated their other consumer).
    assert round2 > 0, (
        f"Second merge round found 0 merges (round 1 found {round1}). "
        f"Iterative merging is not working."
    )


# ---------------------------------------------------------------------------
# Tainted LUT elimination: LUTs with undriven inputs get tied to "0".
# Bug: scope-leaked cells had undriven inputs that cascaded through
# LUT chains into FF DI ports, corrupting sequential state.
# ---------------------------------------------------------------------------

def test_no_lut4_with_undriven_signal_inputs():
    """No LUT4 cell in the final netlist should have undriven signal inputs."""
    # Use Thaw (silicon-verified, zero tolerance for undriven)
    _thaw_designs = os.path.join(_DESIGNS, "thaw_svc_top.sv")
    _thaw_min = os.path.join(_DESIGNS, "thaw_svc_minimal.sv")
    _uart_tx = os.path.join(_DESIGNS, "uart_tx.sv")
    _uart_rx = os.path.join(_DESIGNS, "uart_rx.sv")
    if not os.path.isfile(_uart_tx):
        _uart_tx = os.path.join(_RIME, "firmware/core/uart/uart_tx.sv")
    if not os.path.isfile(_uart_rx):
        _uart_rx = os.path.join(_RIME, "firmware/core/uart/uart_rx.sv")
    sources = [_thaw_designs, _thaw_min, _uart_rx, _uart_tx]
    if not all(os.path.isfile(s) for s in sources):
        pytest.skip("thaw sources not available")

    os.environ["NOSIS_BRAM_THRESHOLD"] = "256"
    result = parse_files(sources, top="top", include_dirs=[_DESIGNS])
    design = lower_to_ir(result, top="top")
    mod = design.top_module()
    run_default_passes(mod)
    infer_brams(mod)
    netlist = map_to_ecp5(design)
    pack_slices(netlist)
    data = json.loads(emit_json_str(netlist))
    m = list(data["modules"].values())[0]
    cells = m["cells"]

    OUT_PORTS = {"Z", "Q", "S0", "S1", "COUT", "DO", "F", "F0", "F1",
                 "OFX0", "OFX1", "CLKO", "DCSOUT", "CDIVX", "CO"}
    driven = set()
    for pi in m["ports"].values():
        if pi["direction"] == "input":
            for b in pi["bits"]:
                if isinstance(b, int):
                    driven.add(b)
    for c in cells.values():
        for pn, bits in c["connections"].items():
            if pn in OUT_PORTS or pn.startswith("DO") or pn.startswith("P") or pn.startswith("R"):
                for b in bits:
                    if isinstance(b, int):
                        driven.add(b)

    bad_luts = []
    for name, cell in cells.items():
        if cell["type"] != "LUT4":
            continue
        for pin in ("A", "B", "C", "D"):
            for b in cell["connections"].get(pin, []):
                if isinstance(b, int) and b >= 2 and b not in driven:
                    bad_luts.append(name)
                    break
            else:
                continue
            break

    assert len(bad_luts) == 0, (
        f"{len(bad_luts)} LUT4 cells have undriven signal inputs. "
        f"Tainted LUT elimination is not working."
    )
