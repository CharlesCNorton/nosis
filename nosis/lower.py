"""Nosis lower module — thin re-export of frontend lowering functions.

Module boundary for the IR lowering stage. The actual implementation
lives in frontend.py; this module provides the clean import path:

    from nosis.lower import lower_to_ir, SynthesisWarning
"""

from nosis.frontend import SynthesisWarning, lower_to_ir

__all__ = [
    "SynthesisWarning",
    "lower_to_ir",
]
