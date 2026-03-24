"""Nosis parse module — thin re-export of frontend parsing functions.

Module boundary for the parse stage. The actual implementation
lives in frontend.py; this module provides the clean import path:

    from nosis.parse import parse_files, ParseResult, FrontendError
"""

from nosis.frontend import FrontendError, ParseResult, parse_files

__all__ = [
    "FrontendError",
    "ParseResult",
    "parse_files",
]
