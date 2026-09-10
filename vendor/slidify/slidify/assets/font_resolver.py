"""CSS-aware font resolver for PPTX font embedding.

Today's slidify embeds Inter into every PPTX. That's necessary but not
sufficient — when the source CSS specifies `'Source Serif Pro', Georgia,
serif` or `'JetBrains Mono', Consolas, monospace`, the rendered PPTX
falls back to Liberation Sans / Calibri because neither family is
embedded. This module closes that gap:

  1. Walk the rendered deck, collect every (family, weight, italic) the
     CSS asks for and the glyphs each one renders.
  2. Resolve each family → a real font file via fontconfig (`fc-match`).
     Drops genericss like "sans-serif" / "monospace" — those are user
     hints, not font names.
  3. Subset each resolved font (fontTools) to only the glyphs the deck
     actually uses. A 600 KB Source Serif Pro becomes 60 KB.
  4. Hand the resulting `FontVariants` list to `embed_fonts_in_pptx`.

Result: every CSS-specified family that exists on the system OR that we
ship a fallback for ends up embedded in the PPTX. Both PowerPoint
(modern) and LibreOffice 7.5+ honor embedded fonts and match by
typeface name — so `<a:latin typeface="Source Serif Pro"/>` finds the
embedded subset and the slide renders with the source's intended type.
"""

from __future__ import annotations

import os
import re
import subprocess
import tempfile
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

import structlog

from slidify.assets.font_embed import FontVariants
from slidify.models import DomElement

log = structlog.get_logger(__name__)


# CSS generic family names — these are author hints, not font files.
_GENERIC_FAMILIES = {
    "serif", "sans-serif", "monospace", "cursive", "fantasy", "system-ui",
    "ui-serif", "ui-sans-serif", "ui-monospace", "ui-rounded",
    "emoji", "math", "fangsong",
    "-apple-system", "blinkmacsystemfont",
}

# Families slidify already bundles or trusts as the runtime fallback —
# no point re-resolving them. Inter ships with the engine.
_BUNDLED_FAMILIES = {"inter", "inter display"}


_KNOWN_WEB_FONTS: dict[str, dict[str, str]] = {
    "playfair display": {
        "regular": (
            "https://raw.githubusercontent.com/google/fonts/main/"
            "ofl/playfairdisplay/PlayfairDisplay%5Bwght%5D.ttf"
        ),
        "italic": (
            "https://raw.githubusercontent.com/google/fonts/main/"
            "ofl/playfairdisplay/PlayfairDisplay-Italic%5Bwght%5D.ttf"
        ),
    }
}


@dataclass
class RequestedFont:
    family: str
    weights_styles: set[tuple[int, bool]] = field(default_factory=set)
    glyphs: set[str] = field(default_factory=set)

    def merge(self, other: RequestedFont) -> None:
        self.weights_styles.update(other.weights_styles)
        self.glyphs.update(other.glyphs)


@dataclass
class ResolvedFont:
    family: str
    files: dict[tuple[int, bool], Path]  # (weight, italic) -> file
    glyphs: set[str]


# ---------------------------------------------------------------------------
# Step 1 — collect requested families from rendered DOM
# ---------------------------------------------------------------------------


_FAMILY_SPLIT_RE = re.compile(r"\s*,\s*")


def _normalize_family(raw: str | None) -> str | None:
    """Pick the FIRST non-generic family from a CSS font-family stack.

    `'Source Serif Pro', Georgia, serif` → "Source Serif Pro".
    `'Inter', sans-serif` → "Inter" (bundled — caller may skip).
    `serif` → None.
    """
    if not raw:
        return None
    for token in _FAMILY_SPLIT_RE.split(raw):
        name = token.strip().strip("'\"")
        if not name:
            continue
        if name.lower() in _GENERIC_FAMILIES:
            continue
        return name
    return None


def _parse_weight(raw: str | None) -> int:
    if not raw:
        return 400
    raw = raw.strip().lower()
    if raw == "bold":
        return 700
    if raw == "normal":
        return 400
    try:
        return int(raw)
    except ValueError:
        return 400


def collect_requested_families(
    elements: list[DomElement],
) -> dict[str, RequestedFont]:
    """Walk the rendered slides, group every (family, weight, italic) and
    the glyphs each variant actually renders."""
    out: dict[str, RequestedFont] = {}

    def _add(family: str | None, weight: int, italic: bool, text: str) -> None:
        if family is None:
            return
        if family.lower() in _BUNDLED_FAMILIES:
            return  # Inter is already embedded by font_embed.embed_default_fonts.
        key = family.lower()
        req = out.setdefault(key, RequestedFont(family=family))
        req.weights_styles.add((weight, italic))
        if text:
            req.glyphs.update(text)

    for el in elements:
        if el.runs:
            for r in el.runs:
                if r.is_break:
                    continue
                fam = _normalize_family(r.font_family or el.font_family)
                weight = _parse_weight(r.font_weight or el.font_weight)
                _add(fam, weight, r.italic, r.text or "")
        elif el.text:
            fam = _normalize_family(el.font_family)
            weight = _parse_weight(el.font_weight)
            _add(fam, weight, False, el.text)

    return out


# ---------------------------------------------------------------------------
# Step 2 — resolve via fontconfig
# ---------------------------------------------------------------------------


def _fc_match(family: str, weight: int, italic: bool) -> Path | None:
    """Use fc-match to find the actual font file for (family, weight, italic).

    Returns None if fc-match is unavailable, the result is a file we can't
    read, or fontconfig falls back to a clearly-different family (e.g.
    a request for "Source Serif Pro" that resolves to "DejaVu Sans" —
    we want a better-matched bundled font, not a sans for a serif spec).
    """
    style = ":italic" if italic else ""
    spec = f"{family}:weight={weight}{style}"
    try:
        result = subprocess.run(
            ["fc-match", "--format=%{file}\t%{family}", spec],
            capture_output=True, text=True, timeout=4,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    parts = result.stdout.strip().split("\t")
    if len(parts) != 2:
        return None
    file_str, resolved_family = parts
    path = Path(file_str)
    if not path.exists():
        return None
    # If fontconfig resolved to a clearly-different family, reject — we'd
    # rather embed nothing for this family than substitute a wrong genre
    # (the brutalist mono → DejaVu Sans case the consulting agent flagged).
    if not _families_compatible(family, resolved_family):
        return None
    return path


def _font_cache_dir() -> Path:
    override = os.environ.get("SLIDIFY_FONT_CACHE")
    if override:
        return Path(override)
    return Path.home() / ".cache" / "slidify" / "fonts"


def _cached_web_font(family: str, weight: int, italic: bool) -> Path | None:
    if os.environ.get("SLIDIFY_DISABLE_FONT_DOWNLOADS"):
        return None
    entry = _KNOWN_WEB_FONTS.get(family.lower())
    if entry is None:
        return None
    style = "italic" if italic else "regular"
    url = entry.get(style) or entry.get("regular")
    if not url:
        return None
    cache_dir = _font_cache_dir() / re.sub(r"[^a-z0-9]+", "-", family.lower()).strip("-")
    filename = f"{family.replace(' ', '')}-{style}.ttf"
    path = cache_dir / filename
    if path.exists() and path.stat().st_size > 1024:
        return path
    try:
        cache_dir.mkdir(parents=True, exist_ok=True)
        with urllib.request.urlopen(url, timeout=8) as resp:
            data = resp.read()
        if len(data) <= 1024:
            return None
        path.write_bytes(data)
        log.info(
            "font_resolver.web_font_cached",
            family=family,
            style=style,
            file=str(path),
        )
        return path
    except Exception as e:
        log.warning(
            "font_resolver.web_font_failed",
            family=family,
            style=style,
            error=str(e),
        )
        return None


_SERIF_HINTS = ("serif", "times", "georgia", "garamond", "cambria", "tiempos",
                "source serif", "playfair", "merriweather", "lora")
_MONO_HINTS = ("mono", "courier", "console", "code", "menlo", "consolas",
               "jetbrains", "ibm plex mono", "sf mono", "operator")


def _families_compatible(requested: str, resolved: str) -> bool:
    """True iff fc-match's substitution didn't cross a serif/mono boundary."""
    r_lower = requested.lower()
    res_lower = resolved.lower().split(",")[0].strip()
    req_serif = any(h in r_lower for h in _SERIF_HINTS)
    req_mono = any(h in r_lower for h in _MONO_HINTS)
    res_serif = any(h in res_lower for h in _SERIF_HINTS)
    res_mono = any(h in res_lower for h in _MONO_HINTS)
    if req_serif and not res_serif:
        return False
    if req_mono and not res_mono:
        return False
    # Names matching loosely — allow.
    return True


_GENRE_FALLBACK_NAMES: dict[str, list[str]] = {
    "serif": ["DejaVu Serif", "Liberation Serif", "FreeSerif"],
    "mono": ["DejaVu Sans Mono", "Liberation Mono", "FreeMono"],
    "sans": ["Inter", "DejaVu Sans", "Liberation Sans", "FreeSans"],
}


def _genre_for(family: str) -> str:
    f = family.lower()
    if any(h in f for h in _MONO_HINTS):
        return "mono"
    if any(h in f for h in _SERIF_HINTS):
        return "serif"
    return "sans"


def _fc_match_genre_fallback(genre: str, weight: int, italic: bool) -> Path | None:
    """Resolve to the first available system font matching `genre`.

    Used when a strict family resolve fails — better to embed DejaVu
    Serif under the typeface name "Source Serif Pro" than to leave the
    requested family unembedded and let the renderer substitute Sans.
    """
    for candidate in _GENRE_FALLBACK_NAMES.get(genre, []):
        # Direct fc-match without our compatibility check (we already
        # know the genre matches because we picked the candidate by genre).
        try:
            result = subprocess.run(
                ["fc-match", "--format=%{file}", f"{candidate}:weight={weight}{':italic' if italic else ''}"],
                capture_output=True, text=True, timeout=4,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return None
        if result.returncode != 0:
            continue
        path = Path(result.stdout.strip())
        if path.exists():
            return path
    return None


def resolve_to_files(
    requested: dict[str, RequestedFont],
) -> list[ResolvedFont]:
    """Resolve every requested family. Strict fc-match first; if that
    fails (or produces a cross-genre substitution), fall back to a
    genre-matched system font. The font's BYTES are the fallback's, but
    we embed under the REQUESTED typeface name so renderers pick the
    embedded data when seeing `<a:latin typeface="Source Serif Pro"/>`.
    """
    out: list[ResolvedFont] = []
    for req in requested.values():
        files: dict[tuple[int, bool], Path] = {}
        genre = _genre_for(req.family)
        for weight, italic in req.weights_styles:
            f = _fc_match(req.family, weight, italic)
            if f is None:
                f = _cached_web_font(req.family, weight, italic)
            if f is None:
                f = _fc_match_genre_fallback(genre, weight, italic)
                if f is not None:
                    log.info(
                        "font_resolver.genre_fallback",
                        family=req.family, genre=genre, file=str(f),
                    )
            if f is not None:
                files[(weight, italic)] = f
        if not files:
            log.info("font_resolver.unresolved", family=req.family)
            continue
        out.append(ResolvedFont(family=req.family, files=files, glyphs=req.glyphs))
    return out


# ---------------------------------------------------------------------------
# Step 3 — subset each resolved font to the deck's glyphs
# ---------------------------------------------------------------------------


def _subset_font_to_temp(font_path: Path, glyphs: set[str]) -> Path | None:
    """Return a temp-file path holding the font subsetted to `glyphs`.

    Adds basic punctuation / digits / latin even if the deck didn't use
    them, so a renderer that needs an em-dash or a missing-glyph
    placeholder still renders something. Falls back to None on subset
    failure (caller embeds the original full font instead).
    """
    try:
        from fontTools.subset import Options, Subsetter
        from fontTools.ttLib import TTFont
    except Exception:
        return None

    extra = (
        "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
        "abcdefghijklmnopqrstuvwxyz"
        "0123456789"
        " .,;:!?'\"-—–·•(){}[]<>/\\@#$%&*+=_"
    )
    text = "".join(sorted(glyphs)) + extra

    try:
        ft = TTFont(str(font_path))
        options = Options()
        options.layout_features = ["*"]
        options.notdef_outline = True
        options.recommended_glyphs = True
        subsetter = Subsetter(options=options)
        subsetter.populate(text=text)
        subsetter.subset(ft)
        out_path = Path(tempfile.mktemp(suffix=font_path.suffix or ".otf"))
        ft.save(str(out_path))
        return out_path
    except Exception as e:
        log.warning("font_resolver.subset_failed", font=str(font_path), error=str(e))
        return None


def subset_fonts(resolved: list[ResolvedFont]) -> list[FontVariants]:
    """Subset every resolved font to deck glyphs and return as
    FontVariants ready for `embed_fonts_in_pptx`."""
    out: list[FontVariants] = []
    for r in resolved:
        # Pick the closest match to each PPTX standard variant.
        regular_src = (
            r.files.get((400, False))
            or r.files.get((300, False))
            or r.files.get((500, False))
            or next(iter(p for (w, it), p in r.files.items() if not it), None)
            or next(iter(r.files.values()), None)
        )
        bold_src = (
            r.files.get((700, False))
            or r.files.get((600, False))
            or r.files.get((800, False))
        )
        italic_src = (
            r.files.get((400, True))
            or r.files.get((300, True))
            or r.files.get((500, True))
        )
        bold_italic_src = (
            r.files.get((700, True))
            or r.files.get((600, True))
        )

        if regular_src is None:
            continue

        regular_subset = _subset_font_to_temp(regular_src, r.glyphs) or regular_src
        variants = FontVariants(
            typeface=r.family,
            regular=regular_subset,
            bold=(_subset_font_to_temp(bold_src, r.glyphs) or bold_src) if bold_src else None,
            italic=(_subset_font_to_temp(italic_src, r.glyphs) or italic_src) if italic_src else None,
            bold_italic=(_subset_font_to_temp(bold_italic_src, r.glyphs) or bold_italic_src) if bold_italic_src else None,
            panose=_panose_for_family(r.family),
        )
        out.append(variants)
    return out


def _panose_for_family(family: str) -> str:
    """Best-guess PANOSE classification (10 hex pairs). Used by PPTX
    `<p:embeddedFont>` for fallback selection; rough genre matching is
    enough — getting it perfect is not."""
    f = family.lower()
    if any(h in f for h in _MONO_HINTS):
        return "020B0609040504020204"  # generic monospace
    if any(h in f for h in _SERIF_HINTS):
        return "02040603050405020304"  # generic serif
    return "020B0604020202020204"      # generic sans-serif


# ---------------------------------------------------------------------------
# Step 4 — public top-level entrypoint
# ---------------------------------------------------------------------------


def resolve_and_subset_for_deck(
    elements: list[DomElement],
) -> list[FontVariants]:
    """End-to-end: collect requested families → resolve → subset →
    return ready-to-embed FontVariants.

    Returns an empty list if nothing extra to embed (deck only used
    Inter / generic families)."""
    requested = collect_requested_families(elements)
    if not requested:
        return []
    resolved = resolve_to_files(requested)
    if not resolved:
        return []
    return subset_fonts(resolved)


__all__ = [
    "RequestedFont",
    "ResolvedFont",
    "collect_requested_families",
    "resolve_and_subset_for_deck",
    "resolve_to_files",
    "subset_fonts",
]
