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

# Bundled test designs (always available, no RIME dependency)
_BUNDLED = os.path.join(_NOSIS_DIR, "tests", "designs")
BUNDLED_UART_TX = os.path.join(_BUNDLED, "uart_tx.sv")
BUNDLED_UART_RX = os.path.join(_BUNDLED, "uart_rx.sv")
BUNDLED_SDRAM_BRIDGE = os.path.join(_BUNDLED, "sdram_bridge.sv")
BUNDLED_CRC32 = os.path.join(_BUNDLED, "rime_pcpi_crc32.sv")

# Standard RIME HDL source paths (may not exist in CI)
_rime_uart_tx = os.path.join(RIME_FW, "core/uart/uart_tx.sv")
_rime_uart_rx = os.path.join(RIME_FW, "core/uart/uart_rx.sv")
RIME_UART_TX = _rime_uart_tx if os.path.isfile(_rime_uart_tx) else BUNDLED_UART_TX
RIME_UART_RX = _rime_uart_rx if os.path.isfile(_rime_uart_rx) else BUNDLED_UART_RX
_rime_sdram_bridge = os.path.join(RIME_FW, "core/service/sdram_bridge.sv")
RIME_SDRAM_BRIDGE = _rime_sdram_bridge if os.path.isfile(_rime_sdram_bridge) else BUNDLED_SDRAM_BRIDGE
RIME_SDRAM_CTRL = os.path.join(RIME_FW, "core/service/sdram_controller.sv")
_rime_crc32 = os.path.join(RIME_FW, "core/cpu/rime_pcpi_crc32.sv")
RIME_CRC32 = _rime_crc32 if os.path.isfile(_rime_crc32) else BUNDLED_CRC32
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

RIME_FROST_SOURCES = [
    os.path.join(RIME_FW, "images/frost/top.sv"),
    os.path.join(RIME_FW, "images/frost/frost_controller.sv"),
    os.path.join(RIME_FW, "images/frost/frost_grid.sv"),
    os.path.join(RIME_FW, "images/frost/frost_readback.sv"),
    RIME_UART_RX,
    RIME_UART_TX,
    os.path.join(RIME_FW, "core/service/flash_spi_master.sv"),
    os.path.join(RIME_FW, "core/service/sd_spi_master.sv"),
    os.path.join(RIME_FW, "core/service/sdram_controller.sv"),
    os.path.join(RIME_FW, "core/service/sdram_bridge.sv"),
]

RIME_SLUSH_SOURCES = [
    os.path.join(RIME_FW, "images/slush/top.sv"),
    os.path.join(RIME_FW, "images/slush/slush_controller.sv"),
    os.path.join(RIME_FW, "images/slush/slush_grid.sv"),
    os.path.join(RIME_FW, "images/slush/slush_readback.sv"),
    RIME_UART_RX,
    RIME_UART_TX,
    os.path.join(RIME_FW, "core/service/flash_spi_master.sv"),
    os.path.join(RIME_FW, "core/service/sd_spi_master.sv"),
    os.path.join(RIME_FW, "core/service/sdram_controller.sv"),
    os.path.join(RIME_FW, "core/service/sdram_bridge.sv"),
]

RIME_EMBER_SOURCES = [
    os.path.join(RIME_FW, "images/ember/top.sv"),
    os.path.join(RIME_FW, "images/ember/aes128.sv"),
    RIME_UART_RX,
    RIME_UART_TX,
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


def rime_soc_available() -> bool:
    """Check if the full RIME SoC sources are accessible (not just bundled designs)."""
    return all(os.path.isfile(f) for f in RIME_SOC_SOURCES)


requires_rime = pytest.mark.skipif(
    not rime_available(),
    reason="RIME source not found (set NOSIS_RIME_ROOT)"
)

requires_rime_soc = pytest.mark.skipif(
    not rime_soc_available(),
    reason="Full RIME SoC sources not found (set NOSIS_RIME_ROOT)"
)


# ---------------------------------------------------------------------------
# Cached design fixtures — parse/lower/map once, reuse across tests
# ---------------------------------------------------------------------------

class _DesignCache:
    """Cache parsed/lowered/mapped results for a design to avoid redundant work."""

    def __init__(self, sources, top):
        self.sources = sources
        self.top = top
        self._parsed = None
        self._design = None
        self._mod = None  # unoptimized
        self._opt_mod = None
        self._netlist = None
        self._json = None
        self._json_data = None

    @property
    def parsed(self):
        if self._parsed is None:
            from nosis.frontend import parse_files
            self._parsed = parse_files(self.sources, top=self.top)
        return self._parsed

    @property
    def design(self):
        if self._design is None:
            from nosis.frontend import lower_to_ir
            self._design = lower_to_ir(self.parsed, top=self.top)
        return self._design

    @property
    def mod(self):
        """Unoptimized module (fresh copy each call to avoid mutation issues)."""
        from nosis.frontend import parse_files, lower_to_ir
        r = parse_files(self.sources, top=self.top)
        d = lower_to_ir(r, top=self.top)
        return d.top_module()

    @property
    def netlist(self):
        if self._netlist is None:
            from nosis.techmap import map_to_ecp5
            self._netlist = map_to_ecp5(self.design)
        return self._netlist

    @property
    def json_str(self):
        if self._json is None:
            from nosis.json_backend import emit_json_str
            self._json = emit_json_str(self.netlist)
        return self._json

    @property
    def json_data(self):
        if self._json_data is None:
            import json
            self._json_data = json.loads(self.json_str)
        return self._json_data


# Singleton caches — created on first access, shared across test files
_caches: dict[str, _DesignCache] = {}


def get_design(name: str) -> _DesignCache:
    """Get or create a cached design by name."""
    if name not in _caches:
        if name == "uart_tx":
            _caches[name] = _DesignCache([RIME_UART_TX], "uart_tx")
        elif name == "uart_rx":
            _caches[name] = _DesignCache([RIME_UART_RX], "uart_rx")
        elif name == "sdram_bridge":
            _caches[name] = _DesignCache([RIME_SDRAM_BRIDGE], "sdram_bridge")
        elif name == "crc32":
            _caches[name] = _DesignCache([RIME_CRC32], "rime_pcpi_crc32")
        elif name == "rime_v":
            _caches[name] = _DesignCache([RIME_V], "rime_v")
        elif name == "thaw":
            _caches[name] = _DesignCache(RIME_THAW_SOURCES, "top")
        elif name == "frost":
            _caches[name] = _DesignCache(RIME_FROST_SOURCES, "top")
        elif name == "slush":
            _caches[name] = _DesignCache(RIME_SLUSH_SOURCES, "top")
        elif name == "ember":
            _caches[name] = _DesignCache(RIME_EMBER_SOURCES, "top")
        elif name == "soc":
            _caches[name] = _DesignCache(RIME_SOC_SOURCES, "top")
        elif name == "picorv32":
            _caches[name] = _DesignCache([RIME_PICORV32], "picorv32")
        elif name == "sdram_ctrl":
            _caches[name] = _DesignCache([RIME_SDRAM_CTRL], "sdram_controller")
        else:
            raise ValueError(f"Unknown design: {name}")
    return _caches[name]
