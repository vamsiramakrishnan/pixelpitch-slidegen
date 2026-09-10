"""Pattern database for slidify.

The pattern DB recognizes high-confidence compositions (Tailwind cards, hero
slides, stat blocks, kickers, pills, badges) and emits them directly to PPTX
without going through the heuristic classifier. Hits land at microseconds and
are reused across decks via the structural cache.

Two layers:
    * tailwind.py — the closed-vocabulary class catalog (colors / radii / shadows
      / typography / spacing / gradients) shipped as JSON. Adding more classes
      is a pure data extension.
    * recipes.py  — recipe matchers. Each recipe matches a unit's class
      signature + computed-CSS shape and returns a high-confidence Decision.
"""

from slidify.patterns.matcher import Pattern, PatternHit, PatternStats, match
from slidify.patterns.recipes import RECIPES, classify_tier0
from slidify.patterns.tailwind import TailwindCatalog, get_default_catalog

__all__ = [
    "RECIPES",
    "Pattern",
    "PatternHit",
    "PatternStats",
    "TailwindCatalog",
    "classify_tier0",
    "get_default_catalog",
    "match",
]
