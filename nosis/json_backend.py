"""Nosis nextpnr JSON backend — emit a nextpnr-compatible netlist.

The output format follows the nextpnr JSON netlist schema:
  - "creator": tool identification
  - "modules": dict of module name -> module definition
  - Each module has "ports", "cells", "netnames"
  - Each cell has "type", "parameters", "port_directions", "connections"
  - Each port has "direction", "bits"
  - Each netname has "bits", "hide_name"

Bit numbering: 0 = constant 0, 1 = constant 1, >=2 = signal bits.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from nosis import __version__
from nosis.techmap import ECP5Cell, ECP5Net, ECP5Netlist

__all__ = [
    "emit_json",
    "emit_json_str",
]


def _format_param(key: str, value: str) -> str:
    """Format a cell parameter value for nextpnr JSON.

    nextpnr expects parameter values as strings. Numeric values must be
    encoded as 32-bit binary strings (e.g., "00000000000000000000000000000001"
    for 1). Hex INIT values are converted to 16-bit binary.
    String values like "DISABLED", "CLK", "LOGIC" pass through as-is.
    """
    s = str(value)
    # Hex values: convert to binary
    if s.startswith("0x") or s.startswith("0X"):
        try:
            int_val = int(s, 16)
            if "INIT" in key.upper():
                return format(int_val, "016b")
            return format(int_val, "032b")
        except ValueError:
            return s
    # String values (MUX selectors, mode names, etc.) pass through as-is
    return s


def _cell_to_json(cell: ECP5Cell) -> dict[str, Any]:
    """Convert an ECP5Cell to a nextpnr JSON cell dict."""
    # Determine port directions from cell type conventions
    port_directions: dict[str, str] = {}
    connections: dict[str, list[int | str]] = {}

    for port_name, bits in cell.ports.items():
        # Classify port direction by name convention
        if port_name in ("Q", "Z", "F0", "F1", "OFX0", "OFX1", "FCO", "CO", "COUT", "S0", "S1", "CDIVX", "DCSOUT", "CLKO") or port_name.startswith("P") or port_name.startswith("DO"):
            port_directions[port_name] = "output"
        else:
            port_directions[port_name] = "input"

        # Convert bits: constant "0"/"1" become integer 0/1 (reserved GND/VCC
        # bit indices). nextpnr's ECP5 DPR packer requires integer bit indices
        # for constant connections, not string constants.
        json_bits: list[int | str] = []
        for bit in bits:
            if isinstance(bit, str):
                if bit == "0":
                    json_bits.append(0)  # bit index 0 = GND
                elif bit == "1":
                    json_bits.append(1)  # bit index 1 = VCC
                elif bit == "x":
                    json_bits.append("x")
                else:
                    json_bits.append(int(bit))
            else:
                json_bits.append(bit)
        connections[port_name] = json_bits

    result: dict[str, Any] = {
        "hide_name": 1 if cell.name.startswith("$") else 0,
        "type": cell.cell_type,
        "parameters": {k: _format_param(k, v) for k, v in cell.parameters.items()},
        "attributes": cell.attributes,
        "port_directions": port_directions,
        "connections": connections,
    }
    return result


def _netname_to_json(net: ECP5Net) -> dict[str, Any]:
    """Convert an ECP5Net to a nextpnr JSON netname dict."""
    json_bits: list[int | str] = []
    for bit in net.bits:
        if isinstance(bit, str):
            if bit == "0":
                json_bits.append(0)
            elif bit == "1":
                json_bits.append(1)
            else:
                json_bits.append(int(bit))
        else:
            json_bits.append(bit)

    # Hide internal nets: those starting with $ or _ (generated names)
    # and those not appearing in the port list
    is_internal = net.name.startswith("$") or net.name.startswith("_")
    return {
        "hide_name": 1 if is_internal else 0,
        "bits": json_bits,
        "attributes": {},
    }


def _netlist_to_json(netlist: ECP5Netlist) -> dict[str, Any]:
    """Convert a full ECP5Netlist to a nextpnr JSON dict."""
    cells: dict[str, Any] = {}
    for name, cell in netlist.cells.items():
        cells[name] = _cell_to_json(cell)

    ports: dict[str, Any] = {}
    for name, port_info in netlist.ports.items():
        bits = []
        for bit in port_info["bits"]:
            if isinstance(bit, str):
                if bit == "0":
                    bits.append(0)
                elif bit == "1":
                    bits.append(1)
                else:
                    bits.append(int(bit))
            else:
                bits.append(bit)
        ports[name] = {
            "direction": port_info["direction"],
            "bits": bits,
        }

    netnames: dict[str, Any] = {}
    for name, net in netlist.nets.items():
        netnames[name] = _netname_to_json(net)

    # Ensure port netname bits match port declaration bits.
    # Techmap may overwrite net bits to constants (e.g. from _map_const)
    # but the port declaration preserves the original signal bits.
    # nextpnr requires these to agree.
    for port_name, port_info in ports.items():
        if port_name in netnames:
            netnames[port_name]["bits"] = list(port_info["bits"])

    return {
        "creator": f"nosis {__version__}",
        "modules": {
            netlist.top: {
                "attributes": {
                    "top": "00000000000000000000000000000001",
                    "src": "",
                },
                "ports": ports,
                "cells": cells,
                "netnames": netnames,
            }
        },
    }


def emit_json_str(netlist: ECP5Netlist) -> str:
    """Serialize an ECP5Netlist to a nextpnr-compatible JSON string."""
    return json.dumps(_netlist_to_json(netlist), indent=2, sort_keys=False)


def emit_json(netlist: ECP5Netlist, output: str | Path) -> Path:
    """Write an ECP5Netlist to a nextpnr-compatible JSON file."""
    path = Path(output).resolve()
    path.write_text(emit_json_str(netlist), encoding="utf-8")
    return path
