"""Shared test configuration — paths, fixtures, and environment setup.

All test paths are configurable via environment variables:
  NOSIS_PYSLANG_PATH  — directory containing pyslang .pyd/.so
  NOSIS_RIME_ROOT     — root of the RIME repository (for HDL test sources)

If NOSIS_RIME_ROOT is not set, tests that require RIME source files
are skipped.
"""

import os
import pytest

# pyslang path — default to sibling directory of nosis repo
_NOSIS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DEFAULT_PYSLANG = os.path.join(os.path.dirname(_NOSIS_DIR), "slang", "build", "lib")
os.environ.setdefault("NOSIS_PYSLANG_PATH", _DEFAULT_PYSLANG)

# RIME repository root — default to sibling directory of nosis repo
_DEFAULT_RIME = os.path.join(os.path.dirname(_NOSIS_DIR), "rime")
RIME_ROOT = os.environ.get("NOSIS_RIME_ROOT", _DEFAULT_RIME)
RIME_FW = os.path.join(RIME_ROOT, "firmware")

# Standard RIME HDL source paths
RIME_UART_TX = os.path.join(RIME_FW, "core/uart/uart_tx.sv")
RIME_UART_RX = os.path.join(RIME_FW, "core/uart/uart_rx.sv")
RIME_SDRAM_BRIDGE = os.path.join(RIME_FW, "core/service/sdram_bridge.sv")
RIME_SDRAM_CTRL = os.path.join(RIME_FW, "core/service/sdram_controller.sv")
RIME_CRC32 = os.path.join(RIME_FW, "core/cpu/rime_pcpi_crc32.sv")
RIME_V = os.path.join(RIME_FW, "core/cpu/rime_v.sv")
RIME_PICORV32 = os.path.join(RIME_FW, "core/cpu/picorv32.v")

RIME_THAW_SOURCES = [
    os.path.join(RIME_FW, "images/thaw/top.sv"),
    os.path.join(RIME_FW, "images/thaw/thaw_service.sv"),
    RIME_UART_RX,
    RIME_UART_TX,
    os.path.join(RIME_FW, "core/service/flash_spi_master.sv"),
    os.path.join(RIME_FW, "core/service/sdram_controller.sv"),
    os.path.join(RIME_FW, "core/service/sdram_bridge.sv"),
]

RIME_SOC_SOURCES = [
    os.path.join(RIME_FW, "images/picorv32/top.sv"),
    RIME_PICORV32,
    os.path.join(RIME_FW, "core/cpu/rime_soc.sv"),
    RIME_CRC32,
    RIME_V,
    os.path.join(RIME_FW, "core/cpu/rime_v_mini.sv"),
    os.path.join(RIME_FW, "core/cpu/rime_v_memif.sv"),
    RIME_UART_RX,
    RIME_UART_TX,
    os.path.join(RIME_FW, "core/service/flash_spi_master.sv"),
    os.path.join(RIME_FW, "core/service/sd_spi_master.sv"),
    os.path.join(RIME_FW, "core/service/sdram_controller.sv"),
    os.path.join(RIME_FW, "core/service/sdram_bridge.sv"),
]


def rime_available() -> bool:
    """Check if RIME source files are accessible."""
    return os.path.isfile(RIME_UART_TX)


requires_rime = pytest.mark.skipif(
    not rime_available(),
    reason="RIME source not found (set NOSIS_RIME_ROOT)"
)
