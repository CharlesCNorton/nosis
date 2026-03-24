"""Nosis power analysis — static power estimation from cell counts and toggle rates.

Estimates power consumption based on:
  - Static (leakage) power per cell type from ECP5 characterization
  - Dynamic power = cell_count * toggle_rate * capacitance * Vdd^2 * frequency
  - Clock tree power from FF count and clock frequency

Without switching activity simulation, toggle rates are assumed at 12.5%
(typical for synchronous logic). True power requires VCD-based analysis.

ECP5 power data (typical, 1.1V core, -6 speed grade):
  TRELLIS_SLICE: 8.5 µW static, 12.0 µW/MHz dynamic at 12.5% toggle
  TRELLIS_FF:    2.0 µW static,  3.5 µW/MHz dynamic
  CCU2C:         9.0 µW static, 13.0 µW/MHz dynamic
  DP16KD:       50.0 µW static, 85.0 µW/MHz dynamic
  MULT18X18D:  120.0 µW static,200.0 µW/MHz dynamic
"""

from __future__ import annotations

from dataclasses import dataclass

from nosis.techmap import ECP5Netlist

__all__ = [
    "PowerReport",
    "estimate_power",
]

# Power model: (static_uw, dynamic_uw_per_mhz)
_CELL_POWER: dict[str, tuple[float, float]] = {
    "TRELLIS_SLICE": (8.5, 12.0),
    "TRELLIS_FF": (2.0, 3.5),
    "CCU2C": (9.0, 13.0),
    "DP16KD": (50.0, 85.0),
    "MULT18X18D": (120.0, 200.0),
}


@dataclass(slots=True)
class PowerReport:
    frequency_mhz: float
    static_power_mw: float
    dynamic_power_mw: float
    total_power_mw: float
    breakdown: dict[str, tuple[float, float]]  # cell_type -> (static_mw, dynamic_mw)

    def summary_lines(self) -> list[str]:
        lines = [
            "--- Power Analysis (estimated, 12.5% toggle, 1.1V) ---",
            f"Frequency:     {self.frequency_mhz:.1f} MHz",
            f"Static power:  {self.static_power_mw:.2f} mW",
            f"Dynamic power: {self.dynamic_power_mw:.2f} mW",
            f"Total power:   {self.total_power_mw:.2f} mW",
        ]
        for cell_type in sorted(self.breakdown):
            s, d = self.breakdown[cell_type]
            lines.append(f"  {cell_type}: static={s:.2f} mW, dynamic={d:.2f} mW")
        return lines


def estimate_power(netlist: ECP5Netlist, frequency_mhz: float = 25.0) -> PowerReport:
    """Estimate power consumption from cell counts and assumed toggle rates."""
    stats = netlist.stats()
    total_static = 0.0
    total_dynamic = 0.0
    breakdown: dict[str, tuple[float, float]] = {}

    for cell_type, (static_uw, dynamic_uw_per_mhz) in _CELL_POWER.items():
        count = stats.get(cell_type, 0)
        if count == 0:
            continue
        static_mw = count * static_uw / 1000.0
        dynamic_mw = count * dynamic_uw_per_mhz * frequency_mhz / 1000.0
        total_static += static_mw
        total_dynamic += dynamic_mw
        breakdown[cell_type] = (static_mw, dynamic_mw)

    return PowerReport(
        frequency_mhz=frequency_mhz,
        static_power_mw=total_static,
        dynamic_power_mw=total_dynamic,
        total_power_mw=total_static + total_dynamic,
        breakdown=breakdown,
    )
