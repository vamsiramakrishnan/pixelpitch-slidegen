"""Regression tests anchored to the round-2 visual-QA findings.

Each finding ships with an HTML fixture under
`tests/fixtures/qa/<finding>.html`.  These tests pin the contracted
fix at the call site or at the OOXML level — so a future refactor
that reverts the contract fails loudly here, not in a downstream
visual diff.
"""
from __future__ import annotations

from pathlib import Path

from slidify.fonts import resolve

FIXTURES = Path(__file__).parent.parent / "fixtures" / "qa"


# ---------------------------------------------------------------------------
# Finding 1 — font-unknown-family-fallback
# ---------------------------------------------------------------------------


def test_font_unknown_display_families_substitute_to_core_fonts():
    """Round-2 audit traced 4 bench-slide regressions to web-font
    families landing as `<a:latin typeface="Helvetica Neue"/>` in slide
    XML and depending on host substitution.  Every modern display
    family used in the bench MUST resolve to a core/Office font."""

    # Sans display
    assert resolve("'Helvetica Neue', Helvetica, Arial, sans-serif") == "Arial"
    assert resolve("'Inter Tight', sans-serif") == "Calibri"

    # Condensed display
    assert resolve("'Bebas Neue', Anton, Impact, sans-serif") == "Impact"

    # Serif display families stay named so the font-embedding pass can bind
    # its genre-matched subset to the requested typeface.
    assert resolve("'Playfair Display', 'Spectral', Georgia, serif") == "Playfair Display"
    assert resolve("'Spectral', Georgia, serif") == "Spectral"
    assert resolve("'Tiempos', Georgia, serif") == "Tiempos"

    # Mono
    assert resolve("'JetBrains Mono', 'IBM Plex Mono', monospace") == "Consolas"
    assert resolve("'IBM Plex Mono', 'SF Mono', Menlo, monospace") == "Consolas"


def test_font_unknown_with_known_later_in_stack_walks_past():
    """An unknown lead must not short-circuit the resolver — a later
    known token still wins so authors can layer safety nets."""
    assert resolve("'NobodysFont', Arial") == "Arial"
    assert resolve("'NobodysFont', 'Helvetica Neue', Arial") == "Arial"


def test_font_unknown_alone_falls_back_to_default():
    """No known names anywhere in the stack and no generic-family token
    means every renderer would substitute.  Pre-empt with DEFAULT_FONT."""
    assert resolve("'NobodysFont'") == "Calibri"


def test_font_fixture_html_exists():
    """The QA finding ships with its own self-contained HTML fixture.
    A future refactor that loses the fixture should fail this test."""
    assert (FIXTURES / "font-unknown-display-fallback.html").exists()


# ---------------------------------------------------------------------------
# Finding 2 — SVG url(#) gradient → native <a:gradFill>
# ---------------------------------------------------------------------------


def test_svg_gradient_emit_writes_gradfill_with_stops():
    """The `_apply_svg_gradient_fill` emitter takes a resolved gradient
    dict (the walker turns `fill="url(#duo-bg)"` into the structured
    form) and writes a native `<a:gradFill>` with `<a:gsLst>` stops,
    per-stop `<a:alpha>` for partial opacity, and `<a:lin ang=.../>`
    for linear direction.  Mock just enough of python-pptx's shape
    surface to capture the spPr element."""
    from lxml import etree as et

    from slidify.svg_shapes import _NS_A, _apply_svg_gradient_fill

    NS = {"a": _NS_A}
    sp_pr = et.Element(f"{{{_NS_A}}}spPr")
    et.SubElement(sp_pr, f"{{{_NS_A}}}prstGeom", attrib={"prst": "rect"})

    class Stub:
        class _E:
            pass
    stub = Stub()
    stub._element = Stub._E()
    stub._element.spPr = sp_pr

    gradient = {
        "kind": "linear",
        "id": "duo-bg",
        "stops": [
            {"offset": 0.0,  "color": "#0F1311", "opacity": 1.0},
            {"offset": 0.55, "color": "#1F2924", "opacity": 1.0},
            {"offset": 1.0,  "color": "#8A4530", "opacity": 0.5},
        ],
        "x1": 0, "y1": 0, "x2": 1, "y2": 1,
    }
    _apply_svg_gradient_fill(stub, gradient, fill_opacity=1.0)

    grad = sp_pr.find("a:gradFill", NS)
    assert grad is not None, "no <a:gradFill> emitted"
    stops = grad.findall("a:gsLst/a:gs", NS)
    assert len(stops) == 3
    # Position values are 1/100000 percent
    assert stops[0].get("pos") == "0"
    assert stops[1].get("pos") == "55000"
    assert stops[2].get("pos") == "100000"
    # First stop colour
    s0_clr = stops[0].find("a:srgbClr", NS)
    assert s0_clr.get("val") == "0F1311"
    # Last stop has alpha
    s2_alpha = stops[2].find("a:srgbClr/a:alpha", NS)
    assert s2_alpha is not None and s2_alpha.get("val") == "50000"
    # Linear angle: (0,0)→(1,1) is 45° = 2_700_000 in 60_000ths
    lin = grad.find("a:lin", NS)
    assert lin is not None and lin.get("ang") == "2700000"


def test_svg_gradient_emit_radial_path():
    """Radial gradient lands as `<a:path path="circle">` with a
    `<a:fillToRect>` derived from focal-point coords."""
    from lxml import etree as et

    from slidify.svg_shapes import _NS_A, _apply_svg_gradient_fill

    NS = {"a": _NS_A}
    sp_pr = et.Element(f"{{{_NS_A}}}spPr")
    et.SubElement(sp_pr, f"{{{_NS_A}}}prstGeom", attrib={"prst": "rect"})

    class Stub:
        class _E:
            pass
    stub = Stub()
    stub._element = Stub._E()
    stub._element.spPr = sp_pr

    _apply_svg_gradient_fill(stub, {
        "kind": "radial",
        "id": "duo-glow",
        "stops": [
            {"offset": 0.0, "color": "#FFECC4", "opacity": 0.95},
            {"offset": 1.0, "color": "#FF7A59", "opacity": 0.0},
        ],
        "cx": 0.66, "cy": 0.62, "r": 0.34, "fx": 0.66, "fy": 0.62,
    }, fill_opacity=1.0)

    grad = sp_pr.find("a:gradFill", NS)
    assert grad is not None
    path = grad.find("a:path", NS)
    assert path is not None and path.get("path") == "circle"
    fill_to_rect = path.find("a:fillToRect", NS)
    assert fill_to_rect is not None
    # fx=0.66 → l=66000, r=34000 (1−fx)
    assert fill_to_rect.get("l") == "66000"
    assert fill_to_rect.get("r") == "34000"


def test_svg_gradient_fixture_html_exists():
    assert (FIXTURES / "duotone-svg-gradient-fill.html").exists()
