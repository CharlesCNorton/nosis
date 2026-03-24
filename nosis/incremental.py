"""Nosis incremental synthesis — detect changes and re-synthesize only what changed.

Serializes the IR to a compact representation and compares it against
a previous run. Only cells that changed (added, removed, or modified)
are re-mapped. Unchanged cells keep their previous mapping.

The delta is computed at the IR level, not the source level — a source
change that doesn't affect the IR (e.g., a comment change) produces
no re-synthesis.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from typing import TYPE_CHECKING

from nosis.ir import Module, PrimOp

if TYPE_CHECKING:
    from nosis.ir import Design
    from nosis.techmap.netlist import ECP5Netlist

__all__ = [
    "IRSnapshot",
    "IRDelta",
    "snapshot_module",
    "compute_delta",
    "save_snapshot",
    "load_snapshot",
    "serialize_module",
    "save_ir",
    "load_ir_data",
    "incremental_remap",
]


@dataclass(slots=True)
class IRSnapshot:
    """Serialized snapshot of a module's IR for incremental comparison."""
    module_name: str
    cell_hashes: dict[str, str]  # cell_name -> hash of (op, inputs, params)
    net_hashes: dict[str, str]   # net_name -> hash of (width, driver)
    port_names: list[str]
    total_cells: int
    total_nets: int


@dataclass(slots=True)
class IRDelta:
    """Difference between two IR snapshots."""
    cells_added: list[str]
    cells_removed: list[str]
    cells_modified: list[str]
    nets_added: list[str]
    nets_removed: list[str]
    ports_changed: bool
    is_empty: bool

    @property
    def changed_count(self) -> int:
        """Return the number of changed entries."""
        return len(self.cells_added) + len(self.cells_removed) + len(self.cells_modified)

    def summary_lines(self) -> list[str]:
        """Return human-readable summary lines."""
        if self.is_empty:
            return ["No changes detected — synthesis output is unchanged."]
        lines = [
            f"Cells added: {len(self.cells_added)}",
            f"Cells removed: {len(self.cells_removed)}",
            f"Cells modified: {len(self.cells_modified)}",
            f"Nets added: {len(self.nets_added)}",
            f"Nets removed: {len(self.nets_removed)}",
            f"Ports changed: {'yes' if self.ports_changed else 'no'}",
        ]
        return lines


def _hash_cell(cell_name: str, op: PrimOp, input_names: list[str], output_names: list[str], params: dict) -> str:
    """Compute a deterministic hash for a cell's identity including outputs."""
    key = f"{cell_name}:{op.name}:{','.join(sorted(input_names))}:{','.join(sorted(output_names))}:{json.dumps(sorted(params.items()), default=str)}"
    return hashlib.sha256(key.encode()).hexdigest()[:16]


def _hash_net(net_name: str, width: int, driver_name: str | None) -> str:
    key = f"{net_name}:{width}:{driver_name or 'none'}"
    return hashlib.sha256(key.encode()).hexdigest()[:16]


def snapshot_module(mod: Module) -> IRSnapshot:
    """Create a snapshot of the current IR state for incremental comparison."""
    cell_hashes: dict[str, str] = {}
    for name, cell in mod.cells.items():
        input_names = [f"{p}={n.name}" for p, n in sorted(cell.inputs.items())]
        output_names = [f"{p}={n.name}" for p, n in sorted(cell.outputs.items())]
        stable_params = {k: v for k, v in cell.params.items() if not k.startswith("_")}
        cell_hashes[name] = _hash_cell(name, cell.op, input_names, output_names, stable_params)

    net_hashes: dict[str, str] = {}
    for name, net in mod.nets.items():
        driver = net.driver.name if net.driver else None
        net_hashes[name] = _hash_net(name, net.width, driver)

    return IRSnapshot(
        module_name=mod.name,
        cell_hashes=cell_hashes,
        net_hashes=net_hashes,
        port_names=sorted(mod.ports.keys()),
        total_cells=len(mod.cells),
        total_nets=len(mod.nets),
    )


def compute_delta(before: IRSnapshot, after: IRSnapshot) -> IRDelta:
    """Compute the difference between two snapshots."""
    before_cells = set(before.cell_hashes.keys())
    after_cells = set(after.cell_hashes.keys())

    added = sorted(after_cells - before_cells)
    removed = sorted(before_cells - after_cells)
    common = before_cells & after_cells
    modified = sorted(name for name in common if before.cell_hashes[name] != after.cell_hashes[name])

    before_nets = set(before.net_hashes.keys())
    after_nets = set(after.net_hashes.keys())

    ports_changed = before.port_names != after.port_names
    is_empty = not added and not removed and not modified and not ports_changed

    return IRDelta(
        cells_added=added,
        cells_removed=removed,
        cells_modified=modified,
        nets_added=sorted(after_nets - before_nets),
        nets_removed=sorted(before_nets - after_nets),
        ports_changed=ports_changed,
        is_empty=is_empty,
    )


def save_snapshot(snapshot: IRSnapshot, path: str | Path) -> None:
    """Save a snapshot to a JSON file for comparison across runs."""
    data = {
        "module": snapshot.module_name,
        "cells": snapshot.cell_hashes,
        "nets": snapshot.net_hashes,
        "ports": snapshot.port_names,
        "total_cells": snapshot.total_cells,
        "total_nets": snapshot.total_nets,
    }
    Path(path).write_text(json.dumps(data, indent=2), encoding="utf-8")


def load_snapshot(path: str | Path) -> IRSnapshot:
    """Load a snapshot from a JSON file."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return IRSnapshot(
        module_name=data["module"],
        cell_hashes=data["cells"],
        net_hashes=data["nets"],
        port_names=data["ports"],
        total_cells=data["total_cells"],
        total_nets=data["total_nets"],
    )


# ---------------------------------------------------------------------------
# Full IR serialization (not just hashes)
# ---------------------------------------------------------------------------

def serialize_module(mod: "Module") -> dict:
    """Serialize a full Module to a JSON-compatible dict."""
    cells = {}
    for name, cell in mod.cells.items():
        cells[name] = {
            "op": cell.op.name,
            "inputs": {p: n.name for p, n in cell.inputs.items()},
            "outputs": {p: n.name for p, n in cell.outputs.items()},
            "params": {k: str(v) for k, v in cell.params.items() if not k.startswith("_")},
            "src": cell.src,
        }
    nets = {}
    for name, net in mod.nets.items():
        nets[name] = {
            "width": net.width,
            "driver": net.driver.name if net.driver else None,
        }
    return {
        "module": mod.name,
        "cells": cells,
        "nets": nets,
        "ports": sorted(mod.ports.keys()),
    }


def save_ir(mod: "Module", path: str | Path) -> None:
    """Save a full Module IR to JSON."""
    Path(path).write_text(json.dumps(serialize_module(mod), indent=2), encoding="utf-8")


def load_ir_data(path: str | Path) -> dict:
    """Load a serialized Module IR from JSON."""
    return json.loads(Path(path).read_text(encoding="utf-8"))


def incremental_remap(
    design: "Design",
    delta: IRDelta,
    prev_netlist: "ECP5Netlist",
) -> "ECP5Netlist":
    """Re-map after an IR delta, carrying attributes across stable cells.

    An empty delta returns the previous netlist unchanged. Otherwise the
    design is re-mapped in full, and cells that are structurally identical
    to their previous counterparts (same name, type, and parameters) keep
    the previous run's attributes (placement hints, PnR annotations).
    """
    from nosis.techmap import map_to_ecp5

    if delta.is_empty:
        return prev_netlist

    new_netlist = map_to_ecp5(design)

    for name, prev_cell in prev_netlist.cells.items():
        if name in new_netlist.cells:
            new_cell = new_netlist.cells[name]
            # If the new cell has the same type, check if it's unchanged
            if new_cell.cell_type == prev_cell.cell_type:
                if new_cell.parameters == prev_cell.parameters:
                    # Cell is structurally identical — restore previous
                    # attributes (placement hints, PnR annotations)
                    for attr_key, attr_val in prev_cell.attributes.items():
                        new_cell.attributes[attr_key] = attr_val

    return new_netlist
