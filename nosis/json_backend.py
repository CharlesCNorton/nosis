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


def _cell_to_json(cell: ECP5Cell) -> dict[str, Any]:
    """Convert an ECP5Cell to a nextpnr JSON cell dict."""
    # Determine port directions from cell type conventions
    port_directions: dict[str, str] = {}
    connections: dict[str, list[int | str]] = {}

    for port_name, bits in cell.ports.items():
        # Classify port direction by name convention
        if port_name in ("Q", "F0", "F1", "OFX0", "OFX1", "FCO", "CO", "COUT", "S0", "S1") or port_name.startswith("P") or port_name.startswith("DO"):
            port_directions[port_name] = "output"
        else:
            port_directions[port_name] = "input"

        # Convert bits: strings stay as-is for constants, ints are signal indices
        json_bits: list[int | str] = []
        for bit in bits:
            if isinstance(bit, str):
                if bit == "0":
                    json_bits.append(0)  # constant 0
                elif bit == "1":
                    json_bits.append(1)  # constant 1
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
        "parameters": {k: str(v) for k, v in cell.parameters.items()},
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

    return {
        "hide_name": 1 if net.name.startswith("$") else 0,
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
