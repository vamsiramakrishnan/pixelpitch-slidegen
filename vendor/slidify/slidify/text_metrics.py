"""Pre-emit font metrics so we can bake fontScale into OOXML rather than
relying on the renderer's TEXT_TO_FIT_SHAPE heuristic.

The dominant visual issue across our decks is font substitution: CSS bboxes
are sized for Inter, but LibreOffice / unembedded PowerPoint render with
Liberation Sans / Calibri (both wider). Rather than letting the runtime
shrink, we measure both fonts here and emit an explicit ``<a:normAutofit
fontScale="..."/>`` so the deck looks the same regardless of renderer.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from fontTools.ttLib import TTFont

from slidify.models import BoundingBox

__all__ = [
    "FontScaleResult",
    "TextMetrics",
    "compute_font_scale_for_textbox",
    "get_fallback_font_path",
    "get_inter_font_path",
    "measure_text",
    "measure_text_width_px",
    "shrink_pill_bbox_to_fit_text",
]


# 1pt = 1/72in, 1px = 1/96in, so 1pt = 96/72 = 1.333... px
_PX_PER_PT = 96.0 / 72.0


@dataclass
class TextMetrics:
    """Width/height info for a text run rendered in a specific font."""

    width_px: float
    ascent_px: float
    descent_px: float
    line_gap_px: float

    @property
    def line_height_px(self) -> float:
        return self.ascent_px + self.descent_px + self.line_gap_px


@dataclass
class FontScaleResult:
    font_scale_pct: int
    line_space_reduction_pct: int
    measured_native_width_px: float
    measured_fallback_width_px: float
    needs_autofit: bool


# ----- font loading + caching ---------------------------------------------


@lru_cache(maxsize=64)
def _load_font(font_path: str) -> TTFont:
    """Cache TTFont objects by absolute path. Parsing a font is slow (10ms+),
    so a long-running emit pass would otherwise re-parse Inter once per run."""
    return TTFont(font_path, lazy=True)


@lru_cache(maxsize=64)
def _font_unitsperem(font_path: str) -> int:
    return int(_load_font(font_path)["head"].unitsPerEm)


@lru_cache(maxsize=64)
def _font_cmap(font_path: str) -> dict[int, str]:
    return _load_font(font_path).getBestCmap() or {}


@lru_cache(maxsize=64)
def _font_metrics_units(font_path: str) -> tuple[int, int, int]:
    """Return (ascent, descent, line_gap) in font design units. Prefers
    OS/2 Typo metrics (per OFF spec they're the cross-platform "use these"
    values); falls back to hhea if absent."""
    f = _load_font(font_path)
    if "OS/2" in f:
        os2 = f["OS/2"]
        return int(os2.sTypoAscender), int(abs(os2.sTypoDescender)), int(os2.sTypoLineGap)
    hhea = f["hhea"]
    return int(hhea.ascent), int(abs(hhea.descent)), int(hhea.lineGap)


def _advance_units(font_path: str, text: str) -> int:
    if not text:
        return 0
    cmap = _font_cmap(font_path)
    f = _load_font(font_path)
    hmtx = f["hmtx"].metrics
    total = 0
    fallback_glyph = cmap.get(ord(" "))
    for ch in text:
        g = cmap.get(ord(ch)) or fallback_glyph
        if g is None:
            continue
        adv = hmtx.get(g, (0, 0))[0]
        total += adv
    return total


# ----- public measurement API ---------------------------------------------


def measure_text(
    text: str, font_path: str | Path, size_pt: float
) -> TextMetrics:
    """Measure a text string in the given font at the given pt size."""
    fp = str(Path(font_path))
    upem = _font_unitsperem(fp)
    px_per_unit = (size_pt / upem) * _PX_PER_PT
    width = _advance_units(fp, text) * px_per_unit
    a_u, d_u, g_u = _font_metrics_units(fp)
    return TextMetrics(
        width_px=width,
        ascent_px=a_u * px_per_unit,
        descent_px=d_u * px_per_unit,
        line_gap_px=g_u * px_per_unit,
    )


def measure_text_width_px(
    text: str, font_path: str | Path, size_pt: float
) -> float:
    """Convenience — just the advance width in px (1pt = 1.333px)."""
    fp = str(Path(font_path))
    upem = _font_unitsperem(fp)
    return _advance_units(fp, text) * (size_pt / upem) * _PX_PER_PT


# ----- font discovery -----------------------------------------------------


_INTER_CANDIDATES: tuple[str, ...] = (
    "/usr/share/fonts/opentype/inter/Inter-Regular.otf",
    "/usr/share/fonts/opentype/Inter/Inter-Regular.otf",
    "/usr/share/fonts/truetype/inter/Inter-Regular.ttf",
    "/usr/local/share/fonts/Inter-Regular.otf",
    "/usr/local/share/fonts/inter/Inter-Regular.otf",
)


# Fallback font: the wide-substitute renderers pick when Inter isn't embedded.
# Linux paths only — macOS/Windows would add e.g.
#   /System/Library/Fonts/Helvetica.ttc, C:\\Windows\\Fonts\\calibri.ttf
_FALLBACK_CANDIDATES: tuple[str, ...] = (
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/TTF/LiberationSans-Regular.ttf",
    "/usr/share/fonts/TTF/DejaVuSans.ttf",
)


def _user_font_dir() -> Path:
    return Path(os.path.expanduser("~/.local/share/fonts"))


def _find_in_user_dir(name_substr: str, ext: str) -> Path | None:
    d = _user_font_dir()
    if not d.is_dir():
        return None
    for p in d.rglob(f"*{ext}"):
        if name_substr.lower() in p.name.lower():
            return p
    return None


def get_inter_font_path() -> Path | None:
    """Locate the bundled / system Inter Regular .otf.

    Prefers ``slidify.font_embed.discover_inter()`` if present (project
    convention), then falls back to common system locations.
    """
    try:
        from slidify import font_embed  # type: ignore[attr-defined]

        variants = font_embed.discover_inter()
        reg = getattr(variants, "regular", None)
        if reg and Path(reg).is_file():
            return Path(reg)
    except Exception:
        pass
    for c in _INTER_CANDIDATES:
        p = Path(c)
        if p.is_file():
            return p
    user_hit = _find_in_user_dir("Inter-Regular", ".otf") or _find_in_user_dir(
        "Inter-Regular", ".ttf"
    )
    if user_hit is not None:
        return user_hit
    return None


def get_fallback_font_path() -> Path | None:
    """Locate a Liberation Sans / DejaVu Sans-class font on the system —
    these are what LibreOffice substitutes for Inter at render time."""
    for c in _FALLBACK_CANDIDATES:
        p = Path(c)
        if p.is_file():
            return p
    for substr in ("LiberationSans-Regular", "DejaVuSans"):
        hit = _find_in_user_dir(substr, ".ttf")
        if hit is not None:
            return hit
    return None


# ----- shrink-to-fit logic ------------------------------------------------


_MIN_FONT_SCALE_PCT = 50_000  # 50% — anything tighter is unreadable
_MAX_FONT_SCALE_PCT = 100_000


def _split_lines(text: str) -> list[str]:
    """Treat literal '<br>' / '<br/>' / '\\n' as line breaks. We don't simulate
    word-wrap — caller passes one logical line at a time."""
    parts = text.replace("<br/>", "\n").replace("<br>", "\n").split("\n")
    return [p for p in parts if p]


def compute_font_scale_for_textbox(
    runs: list[tuple[str, float]],
    bbox_w_px: float,
    bbox_h_px: float | None = None,
    primary_font: Path | None = None,
    fallback_font: Path | None = None,
    safety_margin: float = 1.10,
    genre: str | None = None,
) -> FontScaleResult:
    """Determine the minimum fontScale that lets ``runs`` fit ``bbox_w_px``
    when rendered with ``fallback_font`` (the substituted font on the
    target renderer).

    ``genre`` (``"serif"`` / ``"mono"`` / ``"sans"``) selects a genre-
    matched fallback font when ``fallback_font`` isn't passed explicitly.
    Critical for serif/mono text — measuring those with a sans fallback
    under-counts the substituted width by ~15%, letting overflow cases
    slip through.
    """
    primary = primary_font or get_inter_font_path()
    if fallback_font is None and genre:
        fallback = get_genre_fallback_font_path(genre)
    else:
        fallback = fallback_font or get_fallback_font_path()

    if primary is None or fallback is None or bbox_w_px <= 0:
        return FontScaleResult(
            font_scale_pct=_MAX_FONT_SCALE_PCT,
            line_space_reduction_pct=0,
            measured_native_width_px=0.0,
            measured_fallback_width_px=0.0,
            needs_autofit=False,
        )

    # Sum widths across each line (line breaks split the run text).
    native_per_line: list[float] = [0.0]
    fallback_per_line: list[float] = [0.0]
    for text, pt_size in runs:
        if not text:
            continue
        lines = _split_lines(text) or [""]
        for i, line in enumerate(lines):
            if i > 0:
                native_per_line.append(0.0)
                fallback_per_line.append(0.0)
            native_per_line[-1] += measure_text_width_px(line, primary, pt_size)
            fallback_per_line[-1] += measure_text_width_px(line, fallback, pt_size)

    native_width = max(native_per_line) if native_per_line else 0.0
    fallback_width = max(fallback_per_line) if fallback_per_line else 0.0

    ratio = (fallback_width * safety_margin) / bbox_w_px if bbox_w_px > 0 else 0.0
    if ratio <= 1.0:
        return FontScaleResult(
            font_scale_pct=_MAX_FONT_SCALE_PCT,
            line_space_reduction_pct=0,
            measured_native_width_px=native_width,
            measured_fallback_width_px=fallback_width,
            needs_autofit=False,
        )

    raw_scale = int(round(_MAX_FONT_SCALE_PCT / ratio))
    scale = max(_MIN_FONT_SCALE_PCT, min(_MAX_FONT_SCALE_PCT, raw_scale))
    return FontScaleResult(
        font_scale_pct=scale,
        line_space_reduction_pct=0,
        measured_native_width_px=native_width,
        measured_fallback_width_px=fallback_width,
        needs_autofit=True,
    )


_GENRE_FALLBACK_PATHS: dict[str, tuple[str, ...]] = {
    "serif": (
        "/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSerif-Regular.ttf",
        "/usr/share/fonts/truetype/freefont/FreeSerif.ttf",
    ),
    "mono": (
        "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationMono-Regular.ttf",
        "/usr/share/fonts/truetype/freefont/FreeMono.ttf",
    ),
    "sans": (
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ),
}


def get_genre_fallback_font_path(genre: str) -> Path | None:
    """Return a fallback font path matching ``genre`` (``serif`` / ``mono`` /
    ``sans``). Used when measuring the *substitute* glyph widths: a serif
    primary like Source Serif Pro won't fall back to Liberation Sans on
    LibreOffice — it picks DejaVu Serif (~15% wider). Measuring with the
    sans fallback under-estimates the rendered width and causes
    ``wrap=none`` to fire on a textbox whose text actually overflows.
    """
    for c in _GENRE_FALLBACK_PATHS.get(genre, ()):
        p = Path(c)
        if p.is_file():
            return p
    if genre != "sans":
        return get_genre_fallback_font_path("sans")
    return get_fallback_font_path()


_SERIF_HINTS = (
    "serif", "times", "georgia", "garamond", "cambria", "tiempos",
    "source serif", "playfair", "merriweather", "lora",
)
_MONO_HINTS = (
    "mono", "courier", "consolas", "menlo", "jetbrains", "ibm plex mono",
    "sf mono", "monaco", "fira code", "cascadia",
)


def genre_for_family(family: str | None) -> str:
    f = (family or "").lower()
    if any(h in f for h in _MONO_HINTS):
        return "mono"
    # ``sans-serif`` contains ``serif`` as a substring — strip generic
    # genre keywords first so a sans-serif spec doesn't match the
    # serif hints. Also normalize away whitespace/quotes around generic
    # keywords used as the last item in the CSS ``font-family`` list.
    if "sans-serif" in f or "sans serif" in f:
        # Strip the generic keyword to look at the named families.
        cleaned = f.replace("sans-serif", "").replace("sans serif", "")
        if any(h in cleaned for h in _SERIF_HINTS):
            return "serif"
        return "sans"
    if any(h in f for h in _SERIF_HINTS):
        return "serif"
    return "sans"


def estimate_wrapped_lines(
    text: str,
    bbox_w_px: float,
    font_size_pt: float,
    fallback_font: Path | None = None,
    genre: str | None = None,
    safety_margin: float = 1.05,
) -> int:
    """Estimate how many wrapped lines ``text`` will occupy at ``bbox_w_px``
    when rendered with the genre-appropriate fallback font.

    Used to grow a single-line bbox vertically before emit when the
    source HTML's bbox was sized for a *narrower* font than what the
    target renderer will pick. Conservative: it counts whole lines (so
    a one-line that just barely fits returns 1, not 1.05).
    """
    if not text or bbox_w_px <= 0:
        return 1
    if fallback_font is None and genre:
        fallback = get_genre_fallback_font_path(genre)
    else:
        fallback = fallback_font or get_fallback_font_path()
    if fallback is None:
        return 1
    try:
        full_width = measure_text_width_px(text, fallback, font_size_pt)
    except Exception:
        return 1
    if full_width <= bbox_w_px:
        return 1
    # safety_margin shaves off the soft right edge browsers honor on
    # word breaks (1-char ragging).
    effective_w = bbox_w_px / safety_margin
    if effective_w <= 0:
        return 1
    # Round up to whole lines.
    import math

    return max(1, math.ceil(full_width / effective_w))


def shrink_pill_bbox_to_fit_text(
    text: str,
    bbox: BoundingBox,
    font_size_pt: float,
    horizontal_padding_px: float = 12.0,
    fallback_font: Path | None = None,
) -> BoundingBox:
    """Tighten a pill/chip bbox to its actual text width to kill the
    "green tail" — the empty colored rectangle past the substituted text."""
    fallback = fallback_font or get_fallback_font_path()
    if fallback is None or not text:
        return bbox
    try:
        text_width = measure_text_width_px(text, fallback, font_size_pt)
    except Exception:
        return bbox
    new_w = text_width + 2.0 * horizontal_padding_px
    if new_w >= bbox.w or new_w <= 0:
        return bbox
    return BoundingBox(x=bbox.x, y=bbox.y, w=new_w, h=bbox.h)
