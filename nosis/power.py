"""Nosis power analysis — static power estimation from cell counts and toggle rates.

Example::

    from nosis.techmap import ECP5Netlist
    from nosis.power import estimate_power

    # After tech mapping:
    report = estimate_power(netlist, frequency_mhz=25.0)
    print(f"Total power: {report.total_power_mw:.2f} mW")

Estimates power consumption based on:
  - Static (leakage) power per cell type from ECP5 characterization
  - Dynamic power = cell_count * toggle_rate * capacitance * Vdd^2 * frequency
  - Clock tree power from FF count and clock frequency

Without switching activity simulation, toggle rates are assumed at 12.5%
(typical for synchronous logic). True power requires VCD-based analysis.

ECP5 power data (typical, 1.1V core, -6 speed grade):
  LUT4:          4.25 µW static, 6.0 µW/MHz dynamic at 12.5% toggle
  TRELLIS_FF:    2.0 µW static,  3.5 µW/MHz dynamic
  CCU2C:         9.0 µW static, 13.0 µW/MHz dynamic
  DP16KD:       50.0 µW static, 85.0 µW/MHz dynamic
  MULT18X18D:  120.0 µW static,200.0 µW/MHz dynamic
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from nosis.techmap import ECP5Netlist

if TYPE_CHECKING:
    from nosis.ir import Module

__all__ = [
    "PowerReport",
    "estimate_power",
    "estimate_clock_tree_power",
    "estimate_toggle_rates",
]

# Power model: (static_uw, dynamic_uw_per_mhz)
_CELL_POWER: dict[str, tuple[float, float]] = {
    "LUT4": (4.25, 6.0),  # half of TRELLIS_SLICE (which contains 2 LUT4s)
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


def estimate_power(
    netlist: ECP5Netlist,
    frequency_mhz: float = 25.0,
    toggle_rate: float = 0.125,
) -> PowerReport:
    """Estimate power consumption from cell counts and toggle rates.

    The default toggle rate of 12.5% is a blanket assumption. Pass a
    measured rate from ``estimate_toggle_rates`` for more accuracy.
    Dynamic power scales linearly with toggle rate.
    """
    stats = netlist.stats()
    total_static = 0.0
    total_dynamic = 0.0
    breakdown: dict[str, tuple[float, float]] = {}

    # The _CELL_POWER dynamic values assume 12.5% toggle rate.
    # Scale by the actual rate.
    rate_scale = toggle_rate / 0.125

    for cell_type, (static_uw, dynamic_uw_per_mhz) in _CELL_POWER.items():
        count = stats.get(cell_type, 0)
        if count == 0:
            continue
        static_mw = count * static_uw / 1000.0
        dynamic_mw = count * dynamic_uw_per_mhz * frequency_mhz * rate_scale / 1000.0
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


def estimate_clock_tree_power(
    netlist: ECP5Netlist,
    frequency_mhz: float = 25.0,
) -> float:
    """Estimate clock tree power separately from FF dynamic power.

    The clock tree drives every FF in the design. On ECP5, clock routing
    uses dedicated clock resources (DCC/DCCA) which have lower capacitance
    than general routing, but the clock toggles at full frequency.

    Model: each FF has ~3.5 fF clock pin capacitance.
    Clock tree power = N_ff * C_pin * V^2 * f * activity
    With V=1.1V, activity=1.0 (clock always toggles), C_pin=3.5fF:
    P_clk_per_ff = 3.5e-15 * 1.1^2 * f * 1.0 = 4.235e-15 * f

    Returns clock tree power in milliwatts.
    """
    stats = netlist.stats()
    n_ff = stats.get("TRELLIS_FF", 0)
    # ECP5 clock pin capacitance: ~3.5 fF per FF, 1.1V core
    # P = N * C * V^2 * f (with f in Hz, C in F)
    c_pin = 3.5e-15  # farads
    vdd = 1.1  # volts
    f_hz = frequency_mhz * 1e6
    power_w = n_ff * c_pin * vdd * vdd * f_hz
    return power_w * 1000.0  # convert to mW


def estimate_toggle_rates(
    mod: "Module",
    *,
    num_vectors: int = 1000,
    seed: int = 42,
) -> dict[str, float]:
    """Per-net activity estimation from simulation.

    Simulates the combinational logic with random inputs and measures
    the toggle rate (fraction of cycles where the net changes value)
    for each net. Returns ``{net_name: toggle_rate}`` where toggle_rate
    is in [0.0, 1.0].

    Replaces the assumed 12.5% blanket toggle rate with measured values.
    """
    import random
    from nosis.ir import PrimOp
    from nosis.sim import FastSimulator

    rng = random.Random(seed)

    input_ports: dict[str, int] = {}
    for cell in mod.cells.values():
        if cell.op == PrimOp.INPUT:
            for out_net in cell.outputs.values():
                input_ports[out_net.name] = out_net.width

    if not input_ports:
        return {}

    fast_sim = FastSimulator(mod)

    prev_vals: dict[str, int] = {}
    toggle_counts: dict[str, int] = {}
    total_cycles = 0

    # Carry FF state between simulation vectors for sequential designs.
    ff_state: dict[str, int] = {}
    for cell in mod.cells.values():
        if cell.op == PrimOp.FF:
            for out_net in cell.outputs.values():
                ff_state[out_net.name] = 0

    # Pre-collect FF pairs for fast state update
    ff_pairs: list[tuple[str, str]] = []
    for cell in mod.cells.values():
        if cell.op == PrimOp.FF:
            d_net = cell.inputs.get("D")
            if d_net:
                for out in cell.outputs.values():
                    ff_pairs.append((d_net.name, out.name))

    for _ in range(num_vectors):
        inputs: dict[str, int] = {}
        for name, width in input_ports.items():
            inputs[name] = rng.getrandbits(width)

        # Inject FF state into simulation
        inputs.update(ff_state)

        vals = fast_sim.step(inputs)
        total_cycles += 1

        # Update FF state: Q_next = current D value
        for d_name, q_name in ff_pairs:
            if d_name in vals:
                ff_state[q_name] = vals[d_name]

        for net_name, val in vals.items():
            if net_name in prev_vals and prev_vals[net_name] != val:
                toggle_counts[net_name] = toggle_counts.get(net_name, 0) + 1
            elif net_name not in toggle_counts:
                toggle_counts[net_name] = 0
        prev_vals = dict(vals)

    rates: dict[str, float] = {}
    for net_name, count in toggle_counts.items():
        rates[net_name] = count / max(total_cycles - 1, 1)

    return rates
