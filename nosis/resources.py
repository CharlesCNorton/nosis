"""Nosis resource utilization and area calculation.

Computes exact resource consumption from the mapped netlist and
translates cell counts into physical tile occupancy on ECP5.

ECP5 slice architecture:
  - Each slice contains 2 LUT4, 2 FF, 1 CCU2C carry chain
  - LUTs and FFs share slices: a slice is occupied if it uses
    any of its LUT, FF, or carry resources
  - CCU2C cells are part of the slice carry chain, one per slice
  - DP16KD block RAMs occupy dedicated BRAM tiles (1:1)
  - MULT18X18D multipliers share DSP tiles (2 per tile)
  - PLLs occupy dedicated PLL tiles (1:1)

The slice count is the exact minimum packing given the cell counts.
Nextpnr may use more slices due to routing constraints, but the
cell-derived number is the physical floor.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from nosis.ir import Module

from nosis.techmap import ECP5Netlist

__all__ = [
    "ECP5Device",
    "AreaCalculation",
    "ResourceReport",
    "AreaIndependent",
    "ECP5_DEVICES",
    "calculate_area",
    "estimate_area_independent",
    "report_utilization",
]


@dataclass(frozen=True, slots=True)
class ECP5Device:
    """ECP5 device variant with exact resource counts."""
    name: str
    luts: int       # total LUT4 count
    ffs: int        # total FF count (same as LUTs on ECP5)
    slices: int     # total slices (LUTs / 2)
    brams: int      # DP16KD count
    dsp_tiles: int  # DSP tiles (each has 2 MULT18X18D)
    dsps: int       # MULT18X18D count (dsp_tiles * 2)
    plls: int       # PLL count


# Lattice ECP5 device variants — from Lattice ECP5 Family Data Sheet
# LUT count = slice count * 2. FF count = slice count * 2.
ECP5_DEVICES = {
    "12k": ECP5Device("LFE5U-12F", luts=12288, ffs=12288, slices=6144,  brams=32,  dsp_tiles=8,   dsps=16,  plls=2),
    "25k": ECP5Device("LFE5U-25F", luts=24288, ffs=24288, slices=12144, brams=56,  dsp_tiles=14,  dsps=28,  plls=2),
    "45k": ECP5Device("LFE5U-45F", luts=43848, ffs=43848, slices=21924, brams=108, dsp_tiles=36,  dsps=72,  plls=4),
    "85k": ECP5Device("LFE5U-85F", luts=83640, ffs=83640, slices=41820, brams=208, dsp_tiles=78,  dsps=156, plls=4),
}


@dataclass(slots=True)
class AreaCalculation:
    """Exact physical area breakdown for an ECP5 design.

    All numbers are derived from actual cell counts — not estimated.
    """
    # Raw cell counts from the mapped netlist
    lut_cells: int
    ff_cells: int
    ccu2c_cells: int
    bram_cells: int
    dsp_cells: int

    # Slice packing: each slice holds 2 LUTs, 2 FFs, 1 CCU2C
    slices_for_luts: int     # ceil(lut_cells / 2)
    slices_for_ffs: int      # ceil(ff_cells / 2)
    slices_for_carry: int    # ccu2c_cells (1 per slice)
    slices_total: int        # max of the three — the binding constraint

    # Dedicated tile counts
    bram_tiles: int          # bram_cells (1:1)
    dsp_tiles: int           # ceil(dsp_cells / 2)

    # Total tile area
    total_tiles: int         # slices_total + bram_tiles + dsp_tiles

    # Slice packing efficiency
    lut_packing: float       # lut_cells / (slices_total * 2) — how full are LUT slots
    ff_packing: float        # ff_cells / (slices_total * 2) — how full are FF slots

    # The binding resource — which resource type determines the slice count
    binding_resource: str    # "lut", "ff", "carry", or "none"

    def summary_lines(self) -> list[str]:
        """Return human-readable summary lines."""
        lines = [
            "--- Area Calculation ---",
            f"LUT cells:     {self.lut_cells:>6}  ({self.slices_for_luts} slices)",
            f"FF cells:      {self.ff_cells:>6}  ({self.slices_for_ffs} slices)",
            f"CCU2C cells:   {self.ccu2c_cells:>6}  ({self.slices_for_carry} slices)",
            f"Slices total:  {self.slices_total:>6}  (bound by {self.binding_resource})",
            f"BRAM tiles:    {self.bram_tiles:>6}",
            f"DSP tiles:     {self.dsp_tiles:>6}",
            f"Total tiles:   {self.total_tiles:>6}",
            f"LUT packing:   {self.lut_packing:>5.1f}%",
            f"FF packing:    {self.ff_packing:>5.1f}%",
        ]
        return lines


@dataclass(slots=True)
class ResourceReport:
    """Full resource utilization report against a target device."""
    device: ECP5Device
    area: AreaCalculation
    warnings: list[str]

    @property
    def slice_pct(self) -> float:
        """Return slice utilization as a percentage."""
        return 100.0 * self.area.slices_total / self.device.slices if self.device.slices else 0

    @property
    def lut_pct(self) -> float:
        """Return LUT utilization as a percentage."""
        return 100.0 * self.area.lut_cells / self.device.luts if self.device.luts else 0

    @property
    def ff_pct(self) -> float:
        """Return FF utilization as a percentage."""
        return 100.0 * self.area.ff_cells / self.device.ffs if self.device.ffs else 0

    @property
    def bram_pct(self) -> float:
        """Return BRAM utilization as a percentage."""
        return 100.0 * self.area.bram_tiles / self.device.brams if self.device.brams else 0

    @property
    def dsp_pct(self) -> float:
        """Return DSP utilization as a percentage."""
        return 100.0 * self.area.dsp_tiles / self.device.dsp_tiles if self.device.dsp_tiles else 0

    # Keep backward-compatible properties
    @property
    def luts_used(self) -> int:
        """Return the number of LUTs used."""
        return self.area.lut_cells

    @property
    def ffs_used(self) -> int:
        """Return the number of FFs used."""
        return self.area.ff_cells

    @property
    def brams_used(self) -> int:
        """Return the number of BRAMs used."""
        return self.area.bram_cells

    @property
    def dsps_used(self) -> int:
        """Return the number of DSPs used."""
        return self.area.dsp_cells

    @property
    def carry_used(self) -> int:
        """Return the number of carry chains used."""
        return self.area.ccu2c_cells

    def summary_lines(self) -> list[str]:
        """Return human-readable summary lines."""
        lines = [
            f"Device: {self.device.name}",
            f"Slices: {self.area.slices_total:>6} / {self.device.slices:<6} ({self.slice_pct:5.1f}%)",
            f"LUTs:   {self.area.lut_cells:>6} / {self.device.luts:<6} ({self.lut_pct:5.1f}%)",
            f"FFs:    {self.area.ff_cells:>6} / {self.device.ffs:<6} ({self.ff_pct:5.1f}%)",
            f"BRAMs:  {self.area.bram_tiles:>6} / {self.device.brams:<6} ({self.bram_pct:5.1f}%)",
            f"DSPs:   {self.area.dsp_tiles:>6} / {self.device.dsp_tiles:<6} ({self.dsp_pct:5.1f}%)",
            f"CCU2C:  {self.area.ccu2c_cells:>6}",
            f"Tiles:  {self.area.total_tiles:>6}",
            f"Bound:  {self.area.binding_resource}",
        ]
        for w in self.warnings:
            lines.append(f"WARNING: {w}")
        return lines


def calculate_area(netlist: ECP5Netlist) -> AreaCalculation:
    """Calculate exact physical area from a mapped ECP5 netlist.

    Returns an AreaCalculation with exact cell counts, slice packing,
    and tile breakdown. No estimation — every number is derived from
    the actual cells in the netlist.
    """
    stats = netlist.stats()
    lut_cells = stats.get("LUT4", 0)
    ff_cells = stats.get("TRELLIS_FF", 0)
    ccu2c_cells = stats.get("CCU2C", 0)
    bram_cells = stats.get("DP16KD", 0)
    dsp_cells = stats.get("MULT18X18D", 0)

    # Slice packing: each slice has 2 LUT slots, 2 FF slots, 1 carry slot
    slices_for_luts = math.ceil(lut_cells / 2)
    slices_for_ffs = math.ceil(ff_cells / 2)
    slices_for_carry = ccu2c_cells  # 1 CCU2C per slice

    # The binding constraint: whichever resource needs the most slices
    slices_total = max(slices_for_luts, slices_for_ffs, slices_for_carry, 0)

    if slices_total == 0:
        binding = "none"
    elif slices_total == slices_for_carry:
        binding = "carry"
    elif slices_total == slices_for_luts:
        binding = "lut"
    else:
        binding = "ff"

    # Dedicated tiles
    bram_tiles = bram_cells
    dsp_tiles = math.ceil(dsp_cells / 2)

    total_tiles = slices_total + bram_tiles + dsp_tiles

    # Packing efficiency
    max_lut_slots = slices_total * 2 if slices_total > 0 else 1
    max_ff_slots = slices_total * 2 if slices_total > 0 else 1
    lut_packing = 100.0 * lut_cells / max_lut_slots
    ff_packing = 100.0 * ff_cells / max_ff_slots

    return AreaCalculation(
        lut_cells=lut_cells,
        ff_cells=ff_cells,
        ccu2c_cells=ccu2c_cells,
        bram_cells=bram_cells,
        dsp_cells=dsp_cells,
        slices_for_luts=slices_for_luts,
        slices_for_ffs=slices_for_ffs,
        slices_for_carry=slices_for_carry,
        slices_total=slices_total,
        bram_tiles=bram_tiles,
        dsp_tiles=dsp_tiles,
        total_tiles=total_tiles,
        lut_packing=lut_packing,
        ff_packing=ff_packing,
        binding_resource=binding,
    )


def report_utilization(netlist: ECP5Netlist, device_size: str = "25k") -> ResourceReport:
    """Generate a full resource utilization report for the given netlist and device."""
    device = ECP5_DEVICES.get(device_size)
    if device is None:
        raise ValueError(f"unknown ECP5 device size: {device_size} (known: {', '.join(ECP5_DEVICES)})")

    area = calculate_area(netlist)

    warnings: list[str] = []
    if area.slices_total > device.slices:
        warnings.append(f"slice overutilized: {area.slices_total} needed, {device.slices} available")
    if area.lut_cells > device.luts:
        warnings.append(f"LUT overutilized: {area.lut_cells} used, {device.luts} available")
    if area.ff_cells > device.ffs:
        warnings.append(f"FF overutilized: {area.ff_cells} used, {device.ffs} available")
    if area.bram_tiles > device.brams:
        warnings.append(f"BRAM overutilized: {area.bram_tiles} used, {device.brams} available")
    if area.dsp_tiles > device.dsp_tiles:
        warnings.append(f"DSP overutilized: {area.dsp_tiles} used, {device.dsp_tiles} available")

    return ResourceReport(device=device, area=area, warnings=warnings)


@dataclass(frozen=True, slots=True)
class AreaIndependent:
    """Technology-independent area metric for architecture-neutral optimization.

    Counts IR cells by category without mapping to any specific FPGA. This
    allows optimization passes to reason about area reduction before
    technology mapping is performed.
    """
    logic_cells: int
    ff_cells: int
    memory_bits: int
    multiplier_cells: int
    total_cells: int
    area_units: float  # weighted sum: logic=1, ff=0.5, mem_bit=0.01, mul=10


def estimate_area_independent(mod: "Module") -> AreaIndependent:
    """Compute a technology-independent area estimate from the IR module."""
    from nosis.ir import PrimOp

    logic = 0
    ffs = 0
    mem_bits = 0
    muls = 0

    logic_ops = {
        PrimOp.AND, PrimOp.OR, PrimOp.XOR, PrimOp.NOT,
        PrimOp.MUX, PrimOp.PMUX,
        PrimOp.EQ, PrimOp.NE, PrimOp.LT, PrimOp.LE, PrimOp.GT, PrimOp.GE,
        PrimOp.REDUCE_AND, PrimOp.REDUCE_OR, PrimOp.REDUCE_XOR,
        PrimOp.ADD, PrimOp.SUB, PrimOp.SHL, PrimOp.SHR, PrimOp.SSHR,
        PrimOp.DIV, PrimOp.MOD,
    }

    for cell in mod.cells.values():
        if cell.op in logic_ops:
            logic += 1
        elif cell.op == PrimOp.FF:
            ffs += 1
        elif cell.op == PrimOp.MEMORY:
            depth = cell.params.get("depth", 0)
            width = cell.params.get("width", 0)
            mem_bits += depth * width
        elif cell.op == PrimOp.MUL:
            muls += 1

    total = logic + ffs + muls
    area = logic * 1.0 + ffs * 0.5 + mem_bits * 0.01 + muls * 10.0

    return AreaIndependent(
        logic_cells=logic,
        ff_cells=ffs,
        memory_bits=mem_bits,
        multiplier_cells=muls,
        total_cells=total,
        area_units=area,
    )
