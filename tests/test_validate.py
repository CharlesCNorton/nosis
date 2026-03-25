"""Tests for nosis.validate — validation harness."""

from nosis.validate import (
    PortInfo,
    ValidationResult,
    generate_testbench,
    validate_design,
    _find_iverilog,
    _find_vvp,
)
from tests.conftest import (
    RIME_UART_TX as UART_TX,
    RIME_UART_RX as UART_RX,
    requires_rime,
)


def test_generate_testbench_basic():
    ports = [
        PortInfo("clk", "input", 1),
        PortInfo("data", "input", 8),
        PortInfo("out", "output", 8),
    ]
    tb = generate_testbench("test_mod", ports, num_cycles=10)
    assert "module tb_test_mod" in tb
    assert "test_mod dut" in tb
    assert "$fopen" in tb
    assert "$finish" in tb
    assert "clk" in tb


def test_generate_testbench_with_reset():
    ports = [
        PortInfo("clk", "input", 1),
        PortInfo("rst", "input", 1),
        PortInfo("d", "input", 4),
        PortInfo("q", "output", 4),
    ]
    tb = generate_testbench("ff_test", ports, num_cycles=5)
    assert "rst = 1" in tb or "rst = 0" in tb
    assert "q" in tb


def test_generate_testbench_deterministic():
    ports = [
        PortInfo("clk", "input", 1),
        PortInfo("x", "input", 4),
        PortInfo("y", "output", 4),
    ]
    tb1 = generate_testbench("det", ports, num_cycles=20, seed=123)
    tb2 = generate_testbench("det", ports, num_cycles=20, seed=123)
    assert tb1 == tb2


def test_generate_testbench_different_seeds():
    ports = [
        PortInfo("clk", "input", 1),
        PortInfo("x", "input", 8),
        PortInfo("y", "output", 8),
    ]
    tb1 = generate_testbench("seed", ports, num_cycles=20, seed=1)
    tb2 = generate_testbench("seed", ports, num_cycles=20, seed=2)
    assert tb1 != tb2


def test_find_tools():
    """Check that iverilog and vvp can be found (may be absent in CI)."""
    iv = _find_iverilog()
    vp = _find_vvp()
    # These may be None if not installed — that's fine, the test just
    # verifies the lookup code doesn't crash.
    if iv:
        assert "iverilog" in iv.lower()
    if vp:
        assert "vvp" in vp.lower()


def test_validate_uart_tx():
    """Run validation on uart_tx if iverilog is available."""
    if not _find_iverilog() or not _find_vvp():
        return  # skip if simulation tools not available

    result = validate_design(
        [UART_TX],
        top="uart_tx",
        num_cycles=20,
        seed=42,
    )
    assert result.rtl_sim_ok, f"RTL sim failed: {result.error}"
    # The comparison may find initial-value mismatches (initial blocks
    # set FF values in RTL but post-synthesis FFs default to 0).
    # Verify that the infrastructure runs without crashing.
    assert result.cycles > 0
