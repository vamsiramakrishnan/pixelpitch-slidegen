"""Tests for slidify.fonts (CSS font-family stack resolution)."""

from __future__ import annotations

from slidify.fonts import DEFAULT_FONT, is_bold, parse_weight, resolve


def test_resolve_empty_returns_default():
    assert resolve("") == DEFAULT_FONT
    assert resolve(None) == DEFAULT_FONT  # type: ignore[arg-type]


def test_resolve_known_font_first():
    assert resolve("Inter, sans-serif") == "Inter"
    assert resolve("Calibri") == "Calibri"


def test_resolve_unknown_then_known():
    # Round-2 visual-QA contract: unknown families no longer pass through
    # verbatim — that path was emitting `<a:latin typeface="CustomSans"/>`
    # to slide XML and depending on host substitution.  The resolver
    # walks past the unknown lead and lands on the next known token.
    assert resolve("CustomSans, Arial") == "Arial"
    # Unknown mono lead still defers to the mono generic fallback.
    assert resolve("'JetBrains Mono', monospace") == "Consolas"


def test_resolve_unknown_no_generic_falls_back_to_default():
    # No generic family token in the stack and no known names — drop to
    # DEFAULT_FONT instead of emitting an unknown verbatim.
    assert resolve("'NobodysFont'") == DEFAULT_FONT


def test_installed_template_face_is_not_replaced_by_calibri(monkeypatch):
    monkeypatch.setattr("slidify.fonts._installed_families", lambda: {"roboto light": "Roboto Light", "roboto medium": "Roboto Medium"}, raising=False)
    assert resolve("'Roboto Light'") == "Roboto Light"
    assert resolve("'Roboto Medium', Arial") == "Roboto Medium"


def test_resolve_modern_display_families_substitute():
    # Bench audit found these families landing as `<a:latin typeface=
    # "Helvetica Neue"/>` etc. and silently substituting on every renderer
    # without the named face installed.  Each is now in `_FONT_MAP` mapped
    # to a visually-adjacent core font.
    assert resolve("'Helvetica Neue', Arial, sans-serif") == "Arial"
    assert resolve("'Bebas Neue', Impact, sans-serif") == "Impact"
    assert resolve("'Playfair Display', serif") == "Playfair Display"
    assert resolve("'Spectral', Georgia, serif") == "Spectral"
    assert resolve("'Inter Tight', sans-serif") == "Calibri"
    assert resolve("'IBM Plex Mono', monospace") == "Consolas"
    assert resolve("'JetBrains Mono', 'Menlo', monospace") == "Consolas"


def test_resolve_mono_stack_with_unknown_lead():
    """The dominant brutalist/status-board pattern: a stack of unknown mono
    fonts ending in `monospace`. Naively returning the first unknown name
    causes LibreOffice to substitute Liberation Sans (proportional) and
    break tabular alignment. The resolver must recognize the generic-family
    fallback and emit a known mono face instead."""
    assert resolve("'JetBrains Mono', 'IBM Plex Mono', 'SF Mono', Menlo, monospace") == "Consolas"
    assert resolve("'Fira Code', monospace") == "Consolas"
    assert resolve("'Cascadia Mono', monospace") == "Consolas"


def test_resolve_mono_stack_without_generic_keyword():
    """Even without the explicit `monospace` token, a stack composed of
    obvious mono names (containing `mono` / `code` / `console`) should
    resolve to a known mono face."""
    assert resolve("'Source Code Pro', 'Menlo'") == "Consolas"
    assert resolve("'JetBrains Mono', 'Fira Code'") == "Consolas"


def test_resolve_sans_serif_stack_with_unknown_lead():
    # Round-2 contract: the resolver now substitutes deterministically
    # rather than emitting an unknown name and trusting the renderer.
    # An unknown lead in a sans-serif stack lands on the sans-serif
    # generic fallback (Calibri) — same shape as the mono path.
    assert resolve("'Custom Sans', sans-serif") == "Calibri"


def test_resolve_serif_generic_fallback():
    # Same shape as the sans path: unknown serif lead → serif generic.
    assert resolve("'Souvenir', serif") == "Cambria"


def test_resolve_already_known_mono_passes_through():
    assert resolve("Consolas, monospace") == "Consolas"
    assert resolve("Menlo, monospace") == "Consolas"
    assert resolve("'Courier New', monospace") == "Courier New"


def test_resolve_mono_stack_with_proportional_first():
    """If author writes `Inter, monospace` (a contradiction — Inter is
    proportional, the generic says mono), trust the *generic* — mono columns
    rely on equal advances."""
    assert resolve("Inter, monospace") == "Consolas"


def test_parse_weight():
    assert parse_weight("") == 400
    assert parse_weight("normal") == 400
    assert parse_weight("bold") == 700
    assert parse_weight("400") == 400
    assert parse_weight("700") == 700
    assert parse_weight("950") == 900  # clamped


def test_is_bold():
    assert not is_bold("400")
    assert not is_bold("500")
    assert is_bold("600")
    assert is_bold("700")
    assert is_bold("bold")
