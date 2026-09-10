"""YAML-driven pattern matcher."""

from slidify.patterns.matcher.core import (
    _PREDICATE_HANDLERS,
    Pattern,
    PatternHit,
    PatternStats,
    _h_data_atom_id,
    get_default_patterns,
    match,
)

__all__ = [
    "_PREDICATE_HANDLERS",
    "Pattern",
    "PatternHit",
    "PatternStats",
    "_h_data_atom_id",
    "get_default_patterns",
    "match",
]
