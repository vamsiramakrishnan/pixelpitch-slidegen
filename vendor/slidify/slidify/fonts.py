"""Font resolution from CSS font-family stacks to PPTX-safe names."""

from __future__ import annotations

from functools import lru_cache
import subprocess

# Map common web font families → safe PPTX/Office equivalents.
#
# Entries fall into three groups:
#   1. core/system fonts (kept verbatim — every renderer has them)
#   2. mono families that need to land on a mono substitute even if the
#      named face isn't installed (column alignment is the contract)
#   3. modern web/display families that ride on `@font-face` and aren't
#      portable.  Ship a visually-adjacent core substitute so the slide
#      renders consistently across PowerPoint, LibreOffice, Keynote
#      etc., even when the embedded face is ignored.
#
# Round 2 visual-QA audit (2026-05-03) traced four bench-slide
# regressions to families that previously fell through to the
# title-case-and-emit-verbatim branch — Helvetica Neue, Bebas Neue,
# Playfair Display etc. landed in slideN.xml as `<a:latin typeface=
# "Helvetica Neue"/>` and depended on the host renderer having the
# font installed.  Substituting at resolve-time stops the drift.
_FONT_MAP: dict[str, str] = {
    "inter": "Inter",
    "roboto": "Roboto",
    "open sans": "Open Sans",
    "lato": "Lato",
    "montserrat": "Montserrat",
    "poppins": "Poppins",
    "nunito": "Nunito",
    "source sans pro": "Source Sans Pro",
    "system-ui": "Calibri",
    "ui-sans-serif": "Calibri",
    "sans-serif": "Calibri",
    "serif": "Cambria",
    "ui-serif": "Cambria",
    "monospace": "Consolas",
    "ui-monospace": "Consolas",
    "helvetica": "Helvetica",
    "arial": "Arial",
    "georgia": "Georgia",
    "times": "Times New Roman",
    "times new roman": "Times New Roman",
    "courier": "Courier New",
    "courier new": "Courier New",
    "verdana": "Verdana",
    "tahoma": "Tahoma",
    "consolas": "Consolas",
    "cambria": "Cambria",
    "calibri": "Calibri",
    "segoe ui": "Segoe UI",
    "menlo": "Consolas",
    "monaco": "Consolas",
    # ── modern web/display families → core-font substitutes ──────────
    # Sans display
    "helvetica neue":   "Arial",
    "neue haas grotesk":"Arial",
    "akzidenz-grotesk": "Arial",
    "inter tight":      "Calibri",
    "söhne":            "Calibri",
    "söhne breit":      "Calibri",
    "graphik":          "Calibri",
    # Condensed/display
    "bebas neue":       "Impact",
    "anton":            "Impact",
    "oswald":           "Impact",
    "league gothic":    "Impact",
    # Serif display
    # These are resolved/subset/embedded by slidify.assets.font_resolver.
    # Keep the requested face name in OOXML so the embedded subset can bind.
    "playfair display": "Playfair Display",
    "spectral":         "Spectral",
    "tiempos":          "Tiempos",
    "tiempos text":     "Tiempos Text",
    "tiempos headline": "Tiempos Headline",
    "iowan old style":  "Iowan Old Style",
    "reckless":         "Reckless",
    "fraunces":         "Fraunces",
    # Mono
    "ibm plex mono":    "Consolas",
    "jetbrains mono":   "Consolas",
    "fira code":        "Consolas",
    "sf mono":          "Consolas",
    "source code pro":  "Consolas",
}

DEFAULT_FONT = "Calibri"

# Generic CSS family tokens that act as the *fallback* in a stack. When we
# encounter a stack whose head names aren't in our `_FONT_MAP`, we still want
# to honor the generic family the author asked for — emitting a known-installed
# font of the right *kind* (mono/serif/sans) keeps mono columns lined up,
# stops Inter-sized bboxes from overflowing into a wider serif, etc. The
# alternative (returning the unknown name verbatim and letting PowerPoint /
# LibreOffice substitute) is what causes the dominant class of failure on
# brutalist / status-board / spec-sheet layouts: substitution picks Liberation
# Sans (proportional) for `'JetBrains Mono', monospace`, and tabular alignment
# explodes.
_GENERIC_FAMILY_FALLBACK: dict[str, str] = {
    "monospace": "Consolas",
    "ui-monospace": "Consolas",
    "sans-serif": "Calibri",
    "ui-sans-serif": "Calibri",
    "system-ui": "Calibri",
    "serif": "Cambria",
    "ui-serif": "Cambria",
}

# Hints in a CSS family token that the author wanted a monospace face. We
# don't have a runtime font-availability probe in this engine, so we rely on
# the substring + neighbour-token signal: if the stack ends in `monospace`
# or any leading name contains `mono`/`code`/`mosaic`, treat the whole stack
# as a mono request and resolve to a known mono fallback.
_MONO_HINTS = ("mono", " code", "code ", "console", "courier", "consolas")


def _looks_mono(name: str) -> bool:
    n = name.lower()
    if n in _FONT_MAP:
        # Already-mapped families are mono iff they map to one of the mono
        # canonical fonts.
        return _FONT_MAP[n] in ("Consolas", "Courier New")
    return any(h in n for h in _MONO_HINTS)


@lru_cache(maxsize=1)
def _installed_families() -> dict[str, str]:
    """Exact family/full-face matches only; fc-match silently substitutes."""
    try:
        result = subprocess.run(
            ["fc-list", "--format=%{family}\n%{fullname}\n"],
            capture_output=True, text=True, check=True, timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return {}
    return {name.strip().casefold(): name.strip()
            for line in result.stdout.splitlines() for name in line.split(",") if name.strip()}


def resolve(css_font_family: str) -> str:
    """Resolve a CSS font-family stack to a single PPTX font name.

    Strategy: walk the stack once. The *first known* mapping wins UNLESS the
    stack as a whole signals a generic family (`monospace`, `serif`, etc.)
    that disagrees with the unknown lead. In that case we substitute the
    generic family's safe equivalent — preserving the visual class the
    author asked for (mono → mono, sans → sans) even when the named font
    isn't installed on the target renderer.
    """
    if not css_font_family:
        return DEFAULT_FONT

    tokens: list[str] = []
    for raw in css_font_family.split(","):
        name = raw.strip().strip('"').strip("'").lower()
        if name:
            tokens.append(name)
    if not tokens:
        return DEFAULT_FONT

    # Detect the stack's intended generic family (last generic token wins;
    # CSS spec puts generic families at the end of the cascade).
    generic_fallback: str | None = None
    for tok in tokens:
        if tok in _GENERIC_FAMILY_FALLBACK:
            generic_fallback = _GENERIC_FAMILY_FALLBACK[tok]
            # Don't break — keep walking to preserve last-generic-wins.

    stack_is_mono = (
        generic_fallback in ("Consolas", "Courier New")
        or any(_looks_mono(t) for t in tokens)
    )

    for tok in tokens:
        if tok not in _FONT_MAP and not stack_is_mono:
            installed = _installed_families().get(tok)
            if installed:
                # The embedding pass uses the requested family. Replacing
                # it here leaves embedded Roboto Light unused while every
                # text run points at Calibri instead.
                return installed
        if tok in _FONT_MAP:
            mapped = _FONT_MAP[tok]
            # If the stack is mono but the first hit is proportional (e.g.,
            # `Inter, monospace` — rare but possible), prefer the mono
            # generic. Mono columns rely on equal advances; a proportional
            # font will visibly break them.
            if stack_is_mono and mapped not in ("Consolas", "Courier New"):
                continue
            return mapped
        # Unknown token. If it looks mono and the whole stack is mono, fall
        # through to the generic fallback (Consolas) — emitting "Jetbrains
        # Mono" verbatim is worse than emitting Consolas when the renderer
        # likely lacks JetBrains Mono and would substitute a proportional
        # font instead.
        if stack_is_mono and _looks_mono(tok):
            continue
        # Non-mono unknown name.  Previously this branch title-cased the
        # token and returned it verbatim — landing `<a:latin typeface=
        # "Helvetica Neue"/>` etc. in slide XML and silently substituting
        # on every renderer without the named face installed (round-2
        # visual-QA finding).  Now: walk past it (`continue`) so a later
        # known token in the stack still wins.  When no token in the
        # stack is known, the post-loop logic returns the generic family
        # fallback (or DEFAULT_FONT) — the emit is portable either way.
        if all(part.isalpha() or part.isspace() or part in "-_" for part in tok):
            continue

    if generic_fallback is not None:
        return generic_fallback
    # Stack-wide mono signal but no explicit generic family → still fall back
    # to a known mono face. The author wrote `'JetBrains Mono', 'Fira Code'`
    # without `monospace`; honoring the mono *intent* is more important than
    # honoring DEFAULT_FONT (which is a sans).
    if stack_is_mono:
        return "Consolas"
    return DEFAULT_FONT


def parse_weight(css_weight: str) -> int:
    """Parse CSS font-weight ('400', 'bold', '600') → integer in [100..900]."""
    if not css_weight:
        return 400
    css_weight = css_weight.strip().lower()
    if css_weight == "bold":
        return 700
    if css_weight in ("normal", "regular"):
        return 400
    try:
        v = int(css_weight)
        return max(100, min(900, v))
    except ValueError:
        return 400


def is_bold(css_weight: str) -> bool:
    return parse_weight(css_weight) >= 600
