"""Compatibility shim for ``slidify.assets.font_resolver``."""

from slidify.assets.font_resolver import (
    RequestedFont,
    ResolvedFont,
    _families_compatible,
    _genre_for,
    _normalize_family,
    _parse_weight,
    collect_requested_families,
    resolve_and_subset_for_deck,
    resolve_to_files,
    subset_fonts,
)

__all__ = [
    "RequestedFont",
    "ResolvedFont",
    "_families_compatible",
    "_genre_for",
    "_normalize_family",
    "_parse_weight",
    "collect_requested_families",
    "resolve_and_subset_for_deck",
    "resolve_to_files",
    "subset_fonts",
]
