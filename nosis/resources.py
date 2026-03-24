"""Nosis resource utilization reporting — compare synthesis results against device limits.

Reports how much of the target ECP5 device is consumed by the synthesized
design, and warns when any resource is overutilized.
"""

from __future__ import annotations

from dataclasses import dataclass

from nosis.techmap import ECP5Netlist

__all__ = [
    "ECP5Device",
    "ResourceReport",
    "ECP5_DEVICES",
    "report_utilization",
]


@dataclass(frozen=True, slots=True)
class ECP5Device:
    name: str
    luts: int
    ffs: int
    brams: int   # DP16KD count
    dsps: int    # MULT18X18D count
    plls: int


# Lattice ECP5 device variants
ECP5_DEVICES = {
    "12k": ECP5Device("LFE5U-12F", 12288, 12288, 32, 16, 2),
    "25k": ECP5Device("LFE5U-25F", 24288, 24288, 56, 28, 2),
    "45k": ECP5Device("LFE5U-45F", 43848, 43848, 108, 72, 4),
    "85k": ECP5Device("LFE5U-85F", 83640, 83640, 208, 156, 4),
}


@dataclass(slots=True)
class ResourceReport:
    device: ECP5Device
    luts_used: int
    ffs_used: int
    brams_used: int
    dsps_used: int
    carry_used: int
    warnings: list[str]

    @property
    def lut_pct(self) -> float:
        return 100.0 * self.luts_used / self.device.luts if self.device.luts else 0

    @property
    def ff_pct(self) -> float:
        return 100.0 * self.ffs_used / self.device.ffs if self.device.ffs else 0

    @property
    def bram_pct(self) -> float:
        return 100.0 * self.brams_used / self.device.brams if self.device.brams else 0

    @property
    def dsp_pct(self) -> float:
        return 100.0 * self.dsps_used / self.device.dsps if self.device.dsps else 0

    def summary_lines(self) -> list[str]:
        lines = [
            f"Device: {self.device.name}",
            f"LUTs:  {self.luts_used:>6} / {self.device.luts:<6} ({self.lut_pct:5.1f}%)",
            f"FFs:   {self.ffs_used:>6} / {self.device.ffs:<6} ({self.ff_pct:5.1f}%)",
            f"BRAMs: {self.brams_used:>6} / {self.device.brams:<6} ({self.bram_pct:5.1f}%)",
            f"DSPs:  {self.dsps_used:>6} / {self.device.dsps:<6} ({self.dsp_pct:5.1f}%)",
            f"CCU2C: {self.carry_used:>6}",
        ]
        for w in self.warnings:
            lines.append(f"WARNING: {w}")
        return lines


def report_utilization(netlist: ECP5Netlist, device_size: str = "25k") -> ResourceReport:
    """Generate a resource utilization report for the given netlist and device."""
    device = ECP5_DEVICES.get(device_size)
    if device is None:
        raise ValueError(f"unknown ECP5 device size: {device_size} (known: {', '.join(ECP5_DEVICES)})")

    stats = netlist.stats()
    luts = stats.get("TRELLIS_SLICE", 0)
    ffs = stats.get("TRELLIS_FF", 0)
    brams = stats.get("DP16KD", 0)
    dsps = stats.get("MULT18X18D", 0)
    carry = stats.get("CCU2C", 0)

    warnings: list[str] = []
    if luts > device.luts:
        warnings.append(f"LUT overutilized: {luts} used, {device.luts} available")
    if ffs > device.ffs:
        warnings.append(f"FF overutilized: {ffs} used, {device.ffs} available")
    if brams > device.brams:
        warnings.append(f"BRAM overutilized: {brams} used, {device.brams} available")
    if dsps > device.dsps:
        warnings.append(f"DSP overutilized: {dsps} used, {device.dsps} available")

    return ResourceReport(
        device=device,
        luts_used=luts,
        ffs_used=ffs,
        brams_used=brams,
        dsps_used=dsps,
        carry_used=carry,
        warnings=warnings,
    )
