"""Nosis netlist diff — compare two synthesis runs.

Compares two ECP5 netlists and reports differences in cell counts,
port changes, and structural changes. Useful for verifying that a
code change does not alter the synthesis output unexpectedly.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from nosis.techmap import ECP5Netlist

__all__ = [
    "NetlistDiff",
    "diff_netlists",
]


@dataclass(slots=True)
class NetlistDiff:
    """Differences between two ECP5 netlists."""
    cells_added: list[str] = field(default_factory=list)
    cells_removed: list[str] = field(default_factory=list)
    cell_type_changes: dict[str, tuple[int, int]] = field(default_factory=dict)  # type -> (before, after)
    ports_added: list[str] = field(default_factory=list)
    ports_removed: list[str] = field(default_factory=list)
    net_count_before: int = 0
    net_count_after: int = 0

    @property
    def identical(self) -> bool:
        return (
            not self.cells_added
            and not self.cells_removed
            and not self.cell_type_changes
            and not self.ports_added
            and not self.ports_removed
            and self.net_count_before == self.net_count_after
        )

    def summary_lines(self) -> list[str]:
        if self.identical:
            return ["Netlists are identical."]
        lines = ["--- Netlist Diff ---"]
        if self.cells_added:
            lines.append(f"Cells added: {len(self.cells_added)}")
        if self.cells_removed:
            lines.append(f"Cells removed: {len(self.cells_removed)}")
        for cell_type, (before, after) in sorted(self.cell_type_changes.items()):
            delta = after - before
            sign = "+" if delta > 0 else ""
            lines.append(f"  {cell_type}: {before} -> {after} ({sign}{delta})")
        if self.ports_added:
            lines.append(f"Ports added: {', '.join(self.ports_added)}")
        if self.ports_removed:
            lines.append(f"Ports removed: {', '.join(self.ports_removed)}")
        if self.net_count_before != self.net_count_after:
            lines.append(f"Nets: {self.net_count_before} -> {self.net_count_after}")
        return lines


def diff_netlists(before: ECP5Netlist, after: ECP5Netlist) -> NetlistDiff:
    """Compare two ECP5 netlists and return the differences."""
    before_names = set(before.cells.keys())
    after_names = set(after.cells.keys())

    # Cell type counts
    from collections import Counter
    before_types = Counter(c.cell_type for c in before.cells.values())
    after_types = Counter(c.cell_type for c in after.cells.values())
    all_types = set(before_types) | set(after_types)
    type_changes: dict[str, tuple[int, int]] = {}
    for t in all_types:
        b, a = before_types.get(t, 0), after_types.get(t, 0)
        if b != a:
            type_changes[t] = (b, a)

    return NetlistDiff(
        cells_added=sorted(after_names - before_names)[:50],
        cells_removed=sorted(before_names - after_names)[:50],
        cell_type_changes=type_changes,
        ports_added=sorted(set(after.ports) - set(before.ports)),
        ports_removed=sorted(set(before.ports) - set(after.ports)),
        net_count_before=len(before.nets),
        net_count_after=len(after.nets),
    )
