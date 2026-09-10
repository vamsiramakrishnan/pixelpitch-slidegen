"""Tier-0 recipe entry point.

The matcher is now data-driven: patterns live in `data/patterns.yaml` and are
interpreted by `slidify.patterns.matcher`. This module is the thin glue that
the rest of the pipeline imports.

We keep `RECIPES` as an empty list for back-compat — the legacy Python-coded
recipes have been retired in favor of YAML entries. Adding a new recipe is now
a manifest edit, not a code change.
"""

from __future__ import annotations

from collections.abc import Callable

from slidify.models import Decision, VisualUnit
from slidify.patterns.matcher import (
    PatternStats,
    get_default_patterns,
    match,
)
from slidify.patterns.tailwind import TailwindCatalog, get_default_catalog

RecipeFn = Callable[[VisualUnit, TailwindCatalog], "Decision | None"]


# Retained for callers that still reference RECIPES; the YAML matcher is the
# canonical source of truth now.
RECIPES: list[RecipeFn] = []


def classify_tier0(
    unit: VisualUnit,
    catalog: TailwindCatalog | None = None,
    *,
    stats: PatternStats | None = None,
) -> Decision | None:
    """Run the YAML-driven pattern matcher; return the first hit's decision."""
    cat = catalog or get_default_catalog()
    hit = match(unit, cat, get_default_patterns(), stats)
    return hit.decision if hit is not None else None
