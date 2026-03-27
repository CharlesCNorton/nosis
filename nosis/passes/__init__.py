"""Nosis optimization passes — split into per-pass modules.

The public API is unchanged: import from ``nosis.passes`` as before.
"""

from __future__ import annotations

# Shared mutable state for memory-fanout protection.
# Populated by run_default_passes, read by folding and identity passes.
_active_mem_protect: set[str] = set()

from nosis.passes.folding import constant_fold  # noqa: E402
from nosis.passes.identity import identity_simplify  # noqa: E402
from nosis.passes.dce import dead_code_eliminate  # noqa: E402
from nosis.passes.constff import remove_const_ffs  # noqa: E402
from nosis.passes.mux import collapse_case_chains, merge_mux_chains, simplify_constant_masks  # noqa: E402
from nosis.passes.equiv import (  # noqa: E402,F401
    _eliminate_dont_care_inputs,
    _eliminate_functional_identities,
    _merge_hit_equivalent,
    _simplify_mux_with_zero,
)
from nosis.passes.misc import annotate_eq_carry  # noqa: E402
from nosis.passes.pipeline import run_default_passes  # noqa: E402

__all__ = [
    "constant_fold",
    "identity_simplify",
    "dead_code_eliminate",
    "run_default_passes",
    "remove_const_ffs",
    "merge_mux_chains",
    "collapse_case_chains",
    "simplify_constant_masks",
    "annotate_eq_carry",
    "_eliminate_dont_care_inputs",
    "_eliminate_functional_identities",
    "_merge_hit_equivalent",
    "_simplify_mux_with_zero",
]
