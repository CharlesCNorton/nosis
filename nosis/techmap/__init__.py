"""Nosis ECP5 technology mapping — split into category modules.

The public API is unchanged: import from nosis.techmap as before.
"""

from nosis.techmap.netlist import ECP5Cell, ECP5Net, ECP5Netlist, _compute_lut4_init, _const_bits  # noqa: F401
from nosis.techmap.mapper import map_to_ecp5  # noqa: F401

__all__ = [
    "ECP5Cell",
    "ECP5Net",
    "ECP5Netlist",
    "map_to_ecp5",
]
