"""Nosis hierarchy support — sub-module instance lowering.

Provides the SubInstanceLowerer class that handles recursive lowering
of sub-module instances with prefixed net and cell names.
"""

from __future__ import annotations

from typing import Any

from nosis.ir import Cell, Module, Net, PrimOp

__all__ = [
    "ECP5_BLACKBOX_NAMES",
    "is_vendor_primitive",
]

# ECP5 vendor primitives that should not be lowered
ECP5_BLACKBOX_NAMES = frozenset({
    "USRMCLK", "GSR", "SGSR", "PUR", "JTAGG", "DTR", "OSCG",
    "EHXPLLL", "EHXPLLJ", "CLKDIVF", "DCCA", "DCC", "SEDGA",
    "EXTREFB", "TSALL", "START", "BCINRD", "DCSC", "DQSCE",
    "ECLKSYNCB", "ECLKBRIDGECS", "PCSCLKDIV",
    "BB", "IB", "OB", "OBZ", "BBPU", "BBPD", "IBPU", "IBPD",
    "IDDRX1F", "IDDRX2F", "ODDRX1F", "ODDRX2F",
    "IDDR71B", "ODDR71B", "OSHX2A", "ISHX2A",
    "TSHX2DQA", "TSHX2DQSA",
    "DELAYF", "DELAYG", "DQSBUFM",
    "IFS1P3BX", "IFS1P3DX", "OFS1P3BX", "OFS1P3DX",
    "DCUA",
})


def is_vendor_primitive(module_name: str) -> bool:
    """Check if a module name is an ECP5 vendor primitive."""
    return module_name in ECP5_BLACKBOX_NAMES
