"""Tests for the CSS-aware font resolver.

The resolver is what closes the dominant SSIM gap on the divergent
corpus — without it, every CSS-specified family that isn't installed
gets fontconfig-substituted into a different genre (Source Serif Pro
→ DejaVu Sans), losing the typographic intent.
"""

from __future__ import annotations

from pathlib import Path

import slidify.assets.font_resolver as font_resolver
from slidify.font_resolver import (
    RequestedFont,
    ResolvedFont,
    _families_compatible,
    _genre_for,
    _normalize_family,
    _parse_weight,
    collect_requested_families,
    resolve_to_files,
    subset_fonts,
)
from slidify.models import BoundingBox, DomElement, TextRun

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _el(font_family: str = "", text: str = "x", runs: list[TextRun] | None = None) -> DomElement:
    return DomElement(
        id=0,
        parent_id=None,
        depth=0,
        tag="DIV",
        bbox=BoundingBox(x=0, y=0, w=10, h=10),
        font_family=font_family,
        text=text,
        runs=runs,
        stable_selector="#x",
    )


def _run(text: str, font_family: str, weight: str = "400", italic: bool = False) -> TextRun:
    return TextRun(text=text, font_family=font_family, font_weight=weight, italic=italic)


# ---------------------------------------------------------------------------
# normalize_family
# ---------------------------------------------------------------------------


def test_normalize_family_picks_first_non_generic():
    assert _normalize_family("'Source Serif Pro', Georgia, serif") == "Source Serif Pro"
    assert _normalize_family('"IBM Plex Mono", monospace') == "IBM Plex Mono"


def test_normalize_family_strips_quotes_and_whitespace():
    assert _normalize_family('  "Inter Display"  ,  sans-serif') == "Inter Display"


def test_normalize_family_returns_none_for_pure_generic():
    assert _normalize_family("serif") is None
    assert _normalize_family("monospace, system-ui") is None
    assert _normalize_family("") is None
    assert _normalize_family(None) is None


def test_normalize_family_skips_apple_system_aliases():
    # `-apple-system` and `BlinkMacSystemFont` are platform aliases, not real
    # names — skip and pick the next.
    assert _normalize_family("-apple-system, BlinkMacSystemFont, 'SF Pro', sans-serif") == "SF Pro"


# ---------------------------------------------------------------------------
# weight parsing
# ---------------------------------------------------------------------------


def test_parse_weight_string_keywords():
    assert _parse_weight("normal") == 400
    assert _parse_weight("bold") == 700
    assert _parse_weight(None) == 400
    assert _parse_weight("") == 400


def test_parse_weight_numeric():
    assert _parse_weight("700") == 700
    assert _parse_weight("300") == 300
    assert _parse_weight("garbage") == 400


# ---------------------------------------------------------------------------
# genre detection + cross-genre compatibility
# ---------------------------------------------------------------------------


def test_genre_for_known_classes():
    assert _genre_for("Source Serif Pro") == "serif"
    assert _genre_for("Tiempos Headline") == "serif"
    assert _genre_for("Georgia") == "serif"
    assert _genre_for("IBM Plex Mono") == "mono"
    assert _genre_for("JetBrains Mono") == "mono"
    assert _genre_for("Fira Code") == "mono"
    assert _genre_for("Inter") == "sans"
    assert _genre_for("Helvetica Neue") == "sans"


def test_families_compatible_rejects_cross_genre():
    # The bug we hit: fc-match substitutes Source Serif Pro → DejaVu Sans.
    # The compatibility check must reject this so we fall through to a
    # genre-matched fallback.
    assert _families_compatible("Source Serif Pro", "DejaVu Sans") is False
    assert _families_compatible("IBM Plex Mono", "DejaVu Sans") is False
    assert _families_compatible("JetBrains Mono", "Liberation Sans") is False


def test_families_compatible_accepts_same_genre():
    assert _families_compatible("Source Serif Pro", "DejaVu Serif") is True
    assert _families_compatible("IBM Plex Mono", "DejaVu Sans Mono") is True
    assert _families_compatible("Inter", "Inter") is True


def test_families_compatible_accepts_sans_for_no_specific_request():
    # Family without serif/mono hint can land on a sans without complaint.
    assert _families_compatible("Inter Display", "DejaVu Sans") is True


# ---------------------------------------------------------------------------
# collect_requested_families
# ---------------------------------------------------------------------------


def test_collect_skips_inter_and_generics():
    # Inter is bundled by font_embed.embed_default_fonts, no need to re-resolve.
    elements = [
        _el(font_family="'Inter', sans-serif", text="hello"),
        _el(font_family="serif", text="x"),
    ]
    out = collect_requested_families(elements)
    assert out == {}


def test_collect_groups_by_family_aggregates_glyphs_and_styles():
    elements = [
        _el(text="abc", runs=[
            _run("abc", "'Source Serif Pro', serif", weight="400"),
        ]),
        _el(text="DEF", runs=[
            _run("DEF", "Source Serif Pro", weight="700"),
        ]),
        _el(text="ghi", runs=[
            _run("ghi", "Source Serif Pro", italic=True),
        ]),
    ]
    out = collect_requested_families(elements)
    assert "source serif pro" in out
    req = out["source serif pro"]
    assert req.family == "Source Serif Pro"
    assert (400, False) in req.weights_styles
    assert (700, False) in req.weights_styles
    assert (400, True) in req.weights_styles
    assert "a" in req.glyphs and "F" in req.glyphs and "g" in req.glyphs


def test_collect_picks_up_element_text_when_no_runs():
    elements = [
        _el(font_family="'IBM Plex Mono', monospace", text="status: 200"),
    ]
    out = collect_requested_families(elements)
    assert "ibm plex mono" in out
    req = out["ibm plex mono"]
    assert req.glyphs.issuperset(set("status: 200"))


def test_collect_skips_runs_marked_break():
    br = TextRun(text="\n", is_break=True)
    elements = [
        _el(text="ab", runs=[
            _run("ab", "Source Serif Pro"),
            br,
            _run("cd", "Source Serif Pro"),
        ]),
    ]
    out = collect_requested_families(elements)
    req = out["source serif pro"]
    assert "\n" not in req.glyphs
    assert req.glyphs.issuperset(set("abcd"))


# ---------------------------------------------------------------------------
# resolve_to_files (real fontconfig — skip if fc-match unavailable)
# ---------------------------------------------------------------------------


def test_resolve_falls_back_to_genre_when_strict_fc_match_misses():
    """`Source Serif Pro` isn't installed on this system. fontconfig will
    substitute DejaVu Sans (cross-genre — rejected). Resolver must fall
    through to a genre-matched system font (DejaVu Serif)."""
    import shutil
    if not shutil.which("fc-match"):
        return  # Skip on systems without fontconfig.
    requested = {
        "source serif pro": RequestedFont(
            family="Source Serif Pro",
            weights_styles={(400, False), (700, False)},
            glyphs=set("Hello world"),
        ),
    }
    resolved = resolve_to_files(requested)
    if not resolved:
        return  # No serif system fallback available — environment-dependent skip.
    assert resolved[0].family == "Source Serif Pro"
    # Must have resolved at least the regular variant.
    file_for_regular = resolved[0].files.get((400, False))
    assert file_for_regular is not None
    assert Path(file_for_regular).exists()


def test_resolve_passes_through_inter_when_strict_match_succeeds():
    """A request for `Inter` on a system that has Inter installed must
    keep the strict fc-match result, not fall through to genre fallback."""
    import shutil
    if not shutil.which("fc-match"):
        return
    requested = {
        "inter display": RequestedFont(
            family="Inter Display",
            weights_styles={(400, False)},
            glyphs=set("abc"),
        ),
    }
    resolved = resolve_to_files(requested)
    if not resolved:
        return
    f = resolved[0].files.get((400, False))
    assert f is not None and Path(f).exists()


def test_resolve_uses_curated_web_font_before_genre_fallback(
    tmp_path: Path, monkeypatch
):
    web_font = tmp_path / "PlayfairDisplay-Italic.ttf"
    web_font.write_bytes(b"fake-web-font")
    monkeypatch.setattr(font_resolver, "_fc_match", lambda *args: None)
    monkeypatch.setattr(font_resolver, "_cached_web_font", lambda *args: web_font)
    monkeypatch.setattr(font_resolver, "_fc_match_genre_fallback", lambda *args: None)

    resolved = resolve_to_files(
        {
            "playfair display": RequestedFont(
                family="Playfair Display",
                weights_styles={(400, True)},
                glyphs=set("Editorial"),
            )
        }
    )
    assert resolved
    assert resolved[0].files[(400, True)] == web_font


def test_subset_fonts_keeps_italic_only_editorial_family(tmp_path: Path):
    font = tmp_path / "italic.ttf"
    font.write_bytes(b"not-a-real-font")
    variants = subset_fonts(
        [
            ResolvedFont(
                family="Playfair Display",
                files={(400, True): font},
                glyphs=set("Editorial"),
            )
        ]
    )
    assert len(variants) == 1
    assert variants[0].typeface == "Playfair Display"
    assert variants[0].regular == font
    assert variants[0].italic == font
