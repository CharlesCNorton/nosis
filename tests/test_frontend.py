"""Tests for nosis.frontend — pyslang parsing and IR lowering."""

import os
import sys

# Ensure pyslang is importable
os.environ.setdefault("NOSIS_PYSLANG_PATH", "D:/slang/build/lib")

from nosis.frontend import FrontendError, parse_files, lower_to_ir
from nosis.ir import PrimOp


RIME_ROOT = "D:/rime/firmware"
UART_TX = f"{RIME_ROOT}/core/uart/uart_tx.sv"
UART_RX = f"{RIME_ROOT}/core/uart/uart_rx.sv"
SDRAM_BRIDGE = f"{RIME_ROOT}/core/service/sdram_bridge.sv"
CRC32 = f"{RIME_ROOT}/core/cpu/rime_pcpi_crc32.sv"


def test_parse_uart_tx():
    result = parse_files([UART_TX], top="uart_tx")
    names = [inst.name for inst in result.top_instances]
    assert "uart_tx" in names


def test_parse_uart_rx():
    result = parse_files([UART_RX], top="uart_rx")
    names = [inst.name for inst in result.top_instances]
    assert "uart_rx" in names


def test_parse_sdram_bridge():
    result = parse_files([SDRAM_BRIDGE], top="sdram_bridge")
    names = [inst.name for inst in result.top_instances]
    assert "sdram_bridge" in names


def test_parse_crc32():
    result = parse_files([CRC32], top="rime_pcpi_crc32")
    names = [inst.name for inst in result.top_instances]
    assert "rime_pcpi_crc32" in names


def test_parse_nonexistent_fails():
    try:
        parse_files(["nonexistent_file.sv"])
        assert False, "should have raised"
    except FrontendError:
        pass


def test_lower_uart_tx():
    result = parse_files([UART_TX], top="uart_tx")
    design = lower_to_ir(result, top="uart_tx")
    mod = design.top_module()
    assert mod.name == "uart_tx"
    # Should have ports
    assert "clk" in mod.ports
    assert "tx" in mod.ports
    assert "send" in mod.ports
    assert "data" in mod.ports
    # Should have cells
    assert len(mod.cells) > 0
    # Should have nets
    assert len(mod.nets) > 0
    # Should have FF cells (always_ff block)
    ff_cells = [c for c in mod.cells.values() if c.op == PrimOp.FF]
    assert len(ff_cells) > 0, "expected FF cells from always_ff block"
    # Should have CONST cells (parameters, literals)
    const_cells = [c for c in mod.cells.values() if c.op == PrimOp.CONST]
    assert len(const_cells) > 0
    stats = mod.stats()
    print(f"uart_tx IR stats: {stats}")


def test_lower_sdram_bridge():
    result = parse_files([SDRAM_BRIDGE], top="sdram_bridge")
    design = lower_to_ir(result, top="sdram_bridge")
    mod = design.top_module()
    assert mod.name == "sdram_bridge"
    assert "clk" in mod.ports
    assert "rst" in mod.ports
    assert "start" in mod.ports
    assert "done" in mod.ports
    assert "busy" in mod.ports
    ff_cells = [c for c in mod.cells.values() if c.op == PrimOp.FF]
    assert len(ff_cells) > 0
    stats = mod.stats()
    print(f"sdram_bridge IR stats: {stats}")


def test_lower_crc32():
    result = parse_files([CRC32], top="rime_pcpi_crc32")
    design = lower_to_ir(result, top="rime_pcpi_crc32")
    mod = design.top_module()
    assert mod.name == "rime_pcpi_crc32"
    assert "clk" in mod.ports
    assert "pcpi_valid" in mod.ports
    assert "pcpi_rd" in mod.ports
    stats = mod.stats()
    print(f"crc32 IR stats: {stats}")


def test_lower_produces_valid_design():
    result = parse_files([UART_TX], top="uart_tx")
    design = lower_to_ir(result, top="uart_tx")
    mod = design.top_module()
    # Every cell output net should have exactly one driver
    for cell in mod.cells.values():
        for port_name, net in cell.outputs.items():
            assert net.driver is not None, f"cell {cell.name} output {port_name} has no driver"


def test_lower_top_filter():
    result = parse_files([UART_TX], top="uart_tx")
    design = lower_to_ir(result, top="uart_tx")
    assert design.top == "uart_tx"
    assert "uart_tx" in design.modules


def test_lower_stats_nonzero():
    """Every lowered design should have nonzero cells, nets, and ports."""
    for path, top in [(UART_TX, "uart_tx"), (UART_RX, "uart_rx"), (SDRAM_BRIDGE, "sdram_bridge"), (CRC32, "rime_pcpi_crc32")]:
        result = parse_files([path], top=top)
        design = lower_to_ir(result, top=top)
        mod = design.top_module()
        stats = mod.stats()
        assert stats["cells"] > 0, f"{path}: no cells"
        assert stats["nets"] > 0, f"{path}: no nets"
        assert stats["ports"] > 0, f"{path}: no ports"
