"""Cell-level connectivity tests — verify structural invariants on every cell and net.

These tests walk the entire IR and ECP5 netlist after synthesis and
check invariants that must hold for every design. A single violation
in any cell or net is a synthesis bug.
"""

from nosis.frontend import parse_files, lower_to_ir
from nosis.passes import run_default_passes
from nosis.techmap import map_to_ecp5
from nosis.json_backend import emit_json_str
from nosis.ir import PrimOp, Module
from tests.conftest import (
    RIME_UART_TX, RIME_UART_RX, RIME_SDRAM_BRIDGE, RIME_CRC32, RIME_V,
    RIME_THAW_SOURCES, RIME_SOC_SOURCES, requires_rime_soc,
)

import json


def _lower_design(sources, top):
    result = parse_files(sources if isinstance(sources, list) else [sources], top=top)
    return lower_to_ir(result, top=top)


# ---------------------------------------------------------------------------
# IR invariants — must hold on every design after lowering
# ---------------------------------------------------------------------------

_SINGLE_FILE_DESIGNS = [
    (RIME_UART_TX, "uart_tx"),
    (RIME_UART_RX, "uart_rx"),
    (RIME_SDRAM_BRIDGE, "sdram_bridge"),
    (RIME_CRC32, "rime_pcpi_crc32"),
    (RIME_V, "rime_v"),
]


class TestIRInvariants:
    """Structural invariants on the IR that must hold for every design."""

    def _check_module(self, mod: Module) -> None:
        errors: list[str] = []

        # 1. Every net has width > 0
        for net in mod.nets.values():
            if net.width <= 0:
                errors.append(f"net {net.name} has width {net.width}")

        # 2. Every net has at most one driver (constants/parameters may
        #    have multiple drivers from hierarchical lowering — allowed)
        driver_counts: dict[str, list[str]] = {}
        for cell in mod.cells.values():
            for port_name, net in cell.outputs.items():
                if net.name not in driver_counts:
                    driver_counts[net.name] = []
                driver_counts[net.name].append(cell.name)
        for net_name, drivers in driver_counts.items():
            if len(drivers) > 1:
                # Allow multi-driver if all drivers are CONST (parameter sharing)
                all_const = all(
                    mod.cells[d].op == PrimOp.CONST
                    for d in drivers if d in mod.cells
                )
                if not all_const:
                    errors.append(f"net {net_name} has {len(drivers)} non-const drivers")

        # 3. Every FF has CLK and D inputs
        for cell in mod.cells.values():
            if cell.op == PrimOp.FF:
                if "CLK" not in cell.inputs:
                    errors.append(f"FF {cell.name} missing CLK")
                if "D" not in cell.inputs:
                    errors.append(f"FF {cell.name} missing D")

        # 4. Every FF has at least one output
        for cell in mod.cells.values():
            if cell.op == PrimOp.FF:
                if not cell.outputs:
                    errors.append(f"FF {cell.name} has no outputs")

        # 5. Every INPUT cell has exactly one output
        for cell in mod.cells.values():
            if cell.op == PrimOp.INPUT:
                if len(cell.outputs) != 1:
                    errors.append(f"INPUT {cell.name} has {len(cell.outputs)} outputs (expected 1)")

        # 6. Every OUTPUT cell has at least one input
        for cell in mod.cells.values():
            if cell.op == PrimOp.OUTPUT:
                if not cell.inputs:
                    errors.append(f"OUTPUT {cell.name} has no inputs")

        # 7. CONST cells have no inputs
        for cell in mod.cells.values():
            if cell.op == PrimOp.CONST:
                if cell.inputs:
                    errors.append(f"CONST {cell.name} has inputs: {list(cell.inputs)}")

        # 8. CONST cells have value and width params
        for cell in mod.cells.values():
            if cell.op == PrimOp.CONST:
                if "value" not in cell.params:
                    errors.append(f"CONST {cell.name} missing 'value' param")
                if "width" not in cell.params:
                    errors.append(f"CONST {cell.name} missing 'width' param")

        # 9. Binary ops have A and B inputs
        binary_ops = {
            PrimOp.AND, PrimOp.OR, PrimOp.XOR, PrimOp.ADD, PrimOp.SUB,
            PrimOp.MUL, PrimOp.DIV, PrimOp.MOD, PrimOp.SHL, PrimOp.SHR,
            PrimOp.SSHR, PrimOp.EQ, PrimOp.NE, PrimOp.LT, PrimOp.LE,
            PrimOp.GT, PrimOp.GE,
        }
        for cell in mod.cells.values():
            if cell.op in binary_ops:
                if "A" not in cell.inputs:
                    errors.append(f"{cell.op.name} {cell.name} missing A input")
                if "B" not in cell.inputs:
                    errors.append(f"{cell.op.name} {cell.name} missing B input")

        # 10. MUX has S, A, B inputs
        for cell in mod.cells.values():
            if cell.op == PrimOp.MUX:
                for port in ("S", "A", "B"):
                    if port not in cell.inputs:
                        errors.append(f"MUX {cell.name} missing {port} input")

        # 11. No cell references a net not in mod.nets
        all_net_names = set(mod.nets.keys())
        for cell in mod.cells.values():
            for port, net in cell.inputs.items():
                if net.name not in all_net_names:
                    errors.append(f"cell {cell.name} input {port} references unknown net {net.name}")
            for port, net in cell.outputs.items():
                if net.name not in all_net_names:
                    errors.append(f"cell {cell.name} output {port} references unknown net {net.name}")

        # 12. Every port net exists in mod.nets
        for name in mod.ports:
            if name not in mod.nets:
                errors.append(f"port {name} not in nets")

        assert not errors, f"{len(errors)} connectivity violations:\n" + "\n".join(errors[:20])

    def test_uart_tx(self):
        design = _lower_design(RIME_UART_TX, "uart_tx")
        self._check_module(design.top_module())

    def test_uart_rx(self):
        design = _lower_design(RIME_UART_RX, "uart_rx")
        self._check_module(design.top_module())

    def test_sdram_bridge(self):
        design = _lower_design(RIME_SDRAM_BRIDGE, "sdram_bridge")
        self._check_module(design.top_module())

    def test_crc32(self):
        design = _lower_design(RIME_CRC32, "rime_pcpi_crc32")
        self._check_module(design.top_module())

    @requires_rime_soc
    def test_rime_v(self):
        design = _lower_design(RIME_V, "rime_v")
        self._check_module(design.top_module())

    @requires_rime_soc
    def test_thaw(self):
        design = _lower_design(RIME_THAW_SOURCES, "top")
        self._check_module(design.top_module())

    @requires_rime_soc
    def test_soc(self):
        design = _lower_design(RIME_SOC_SOURCES, "top")
        self._check_module(design.top_module())

    @requires_rime_soc
    def test_after_optimization(self):
        """Invariants must hold after optimization too."""
        design = _lower_design(RIME_V, "rime_v")
        mod = design.top_module()
        run_default_passes(mod)
        self._check_module(mod)


# ---------------------------------------------------------------------------
# JSON netlist invariants — must hold on every emitted netlist
# ---------------------------------------------------------------------------

class TestJSONInvariants:
    """Structural invariants on the nextpnr JSON output."""

    def _check_json(self, sources, top):
        result = parse_files(sources if isinstance(sources, list) else [sources], top=top)
        design = lower_to_ir(result, top=top)
        nl = map_to_ecp5(design)
        text = emit_json_str(nl)
        data = json.loads(text)

        errors: list[str] = []

        # 1. Valid top-level structure
        assert "creator" in data
        assert "modules" in data
        assert top in data["modules"]
        mod = data["modules"][top]

        # 2. Every cell has required fields
        for name, cell in mod.get("cells", {}).items():
            if "type" not in cell:
                errors.append(f"cell {name} missing type")
            if "connections" not in cell:
                errors.append(f"cell {name} missing connections")
            if "port_directions" not in cell:
                errors.append(f"cell {name} missing port_directions")
            if "parameters" not in cell:
                errors.append(f"cell {name} missing parameters")

            # 3. Every connection bit is an integer (signal) or string constant ("0"/"1"/"x")
            for port, bits in cell.get("connections", {}).items():
                for i, bit in enumerate(bits):
                    if not (isinstance(bit, int) or (isinstance(bit, str) and bit in ("0", "1", "x"))):
                        errors.append(f"cell {name} port {port} bit {i} is invalid: {bit!r}")

            # 4. Every port has a direction
            for port in cell.get("connections", {}):
                if port not in cell.get("port_directions", {}):
                    errors.append(f"cell {name} port {port} has no direction")

            # 5. Cell type is a known ECP5 primitive
            known_types = {"LUT4", "TRELLIS_FF", "CCU2C", "MULT18X18D", "DP16KD", "TRELLIS_DPR16X4", "ALU54B", "BB"}
            if cell.get("type") not in known_types:
                errors.append(f"cell {name} has unknown type {cell.get('type')}")

        # 6. Every port has direction and bits
        for name, port in mod.get("ports", {}).items():
            if "direction" not in port:
                errors.append(f"port {name} missing direction")
            if "bits" not in port:
                errors.append(f"port {name} missing bits")
            if port.get("direction") not in ("input", "output", "inout"):
                errors.append(f"port {name} has invalid direction: {port.get('direction')}")

        # 7. Top attribute is set
        attrs = mod.get("attributes", {})
        if attrs.get("top") != "00000000000000000000000000000001":
            errors.append("module missing top attribute")

        assert not errors, f"{len(errors)} JSON violations:\n" + "\n".join(errors[:20])

    def test_uart_tx(self):
        self._check_json(RIME_UART_TX, "uart_tx")

    def test_uart_rx(self):
        self._check_json(RIME_UART_RX, "uart_rx")

    def test_sdram_bridge(self):
        self._check_json(RIME_SDRAM_BRIDGE, "sdram_bridge")

    @requires_rime_soc
    def test_rime_v(self):
        self._check_json(RIME_V, "rime_v")

    @requires_rime_soc
    def test_thaw(self):
        self._check_json(RIME_THAW_SOURCES, "top")
