"""Tests for Phase 2c recipes added to patterns.yaml and atoms.yaml.

Each test builds a synthetic ``DomElement`` + ``VisualUnit`` and asserts the
expected ``recipe`` metadata pops out of ``classify_tier0`` (or, for
structural-tag recipes, that the matcher records a structural hit).
"""

from __future__ import annotations

from slidify.models import BoundingBox, DomElement, VisualUnit
from slidify.patterns import classify_tier0, get_default_catalog
from slidify.patterns.matcher import (
    PatternStats,
    get_default_patterns,
    match,
)


# ---------------------------------------------------------------------------
# Helpers (mirror tests/unit/test_pattern_db.py)
# ---------------------------------------------------------------------------


def _el(
    eid: int,
    tag: str = "DIV",
    cls: str = "",
    text: str | None = None,
    *,
    bbox: tuple[float, float, float, float] = (0, 0, 200, 32),
    **kwargs,
) -> DomElement:
    x, y, w, h = bbox
    base: dict = dict(
        id=eid,
        parent_id=None,
        depth=0,
        tag=tag,
        cls=cls,
        bbox=BoundingBox(x=x, y=y, w=w, h=h),
        text=text,
        stable_selector=f"#e{eid}",
    )
    base.update(kwargs)
    return DomElement(**base)


def _unit(
    elements: list[DomElement],
    children: list[VisualUnit] | None = None,
) -> VisualUnit:
    bb = elements[0].bbox if elements else BoundingBox(x=0, y=0, w=10, h=10)
    return VisualUnit(
        id=f"u_{elements[0].id}" if elements else "u_x",
        bbox=bb,
        elements=elements,
        children=children or [],
        anchor_element_id=elements[0].id if elements else None,
    )


_CATALOG = get_default_catalog()


def _structural_hit_ids(unit: VisualUnit) -> set[str]:
    """Return the set of structural pattern ids that recorded a hit on the
    matcher for ``unit``. Decision-tag hits are *not* included."""
    stats = PatternStats()
    match(unit, _CATALOG, get_default_patterns(), stats)
    return set(stats.structural_hits_by_id.keys())


# ---------------------------------------------------------------------------
# patterns.yaml: must-raster gates
# ---------------------------------------------------------------------------


def test_mask_image_raster_fires():
    el = _el(1, mask_image="linear-gradient(black, transparent)")
    d = classify_tier0(_unit([el]), _CATALOG)
    assert d is not None
    assert d.kind.value == "raster"
    assert d.metadata.get("recipe") == "mask_image_raster"


def test_background_blend_mode_raster_fires():
    el = _el(1, background_blend_mode="multiply")
    d = classify_tier0(_unit([el]), _CATALOG)
    assert d is not None
    assert d.kind.value == "raster"
    assert d.metadata.get("recipe") == "background_blend_mode_raster"


# ---------------------------------------------------------------------------
# patterns.yaml: typography / register cues
# ---------------------------------------------------------------------------


def test_vertical_rail_text_fires():
    # Font 22px is above short_label's 18px cap so the vertical-rail
    # recipe (priority 28) actually wins instead of short_label (20).
    el = _el(
        1,
        "DIV",
        text="SECTION 02",
        bbox=(0, 100, 60, 240),
        writing_mode="vertical-rl",
        font_size="22px",
    )
    d = classify_tier0(_unit([el]), _CATALOG)
    assert d is not None
    assert d.metadata.get("recipe") == "vertical_rail"
    assert d.metadata.get("role") == "rail"


def test_display_headline_tight_fires():
    el = _el(
        1,
        "DIV",
        text="Make it loud.",
        bbox=(0, 0, 800, 96),
        font_family="Bebas Neue, Druk, display",
        font_size="72px",
        letter_spacing="-1.2px",
    )
    d = classify_tier0(_unit([el]), _CATALOG)
    assert d is not None
    assert d.metadata.get("recipe") == "display_headline"
    assert d.metadata.get("register") == "display-tight"


def test_editorial_serif_headline_fires():
    el = _el(
        1,
        "DIV",
        text="An Editorial Headline",
        bbox=(0, 0, 800, 64),
        font_family="Playfair Display, Georgia, serif",
        font_size="48px",
    )
    d = classify_tier0(_unit([el]), _CATALOG)
    assert d is not None
    assert d.metadata.get("recipe") == "editorial_serif"


def test_mono_tag_text_fires():
    # Use CODE tag so short_label (which restricts to DIV/P/SPAN) doesn't
    # absorb the unit before the mono_tag recipe gets evaluated.
    el = _el(
        1,
        "CODE",
        text="v1.0.3",
        bbox=(0, 0, 80, 18),
        font_family="JetBrains Mono, Menlo, monospace",
        font_size="12px",
    )
    d = classify_tier0(_unit([el]), _CATALOG)
    assert d is not None
    assert d.metadata.get("recipe") == "mono_tag"


def test_text_shadow_text_fires():
    # font 24px sits above short_label's 18px cap and below body_paragraph's
    # 22px cap so text_with_shadow (priority 39) is the first recipe to match.
    el = _el(
        1,
        "DIV",
        text="Glow on me",
        bbox=(0, 0, 280, 32),
        font_size="24px",
        text_shadow="0 2px 8px rgb(0,0,0)",
    )
    d = classify_tier0(_unit([el]), _CATALOG)
    assert d is not None
    assert d.metadata.get("recipe") == "text_with_shadow"
    assert d.confidence >= 0.85


def test_pill_radius_only_fires():
    # radius 90px is inside pill-radius-only's [80, 9999] range but below
    # pill-from-css's 100px threshold so pill_by_radius wins. font 20px is
    # above short_label's 18px cap so that recipe doesn't intercept first.
    el = _el(
        1,
        "DIV",
        text="Live",
        bbox=(0, 0, 80, 28),
        border_radius="90px",
        font_size="20px",
    )
    d = classify_tier0(_unit([el]), _CATALOG)
    assert d is not None
    assert d.metadata.get("recipe") == "pill_by_radius"


# ---------------------------------------------------------------------------
# patterns.yaml: brutalist / magazine rules
# ---------------------------------------------------------------------------


def test_brutalist_top_rule_fires():
    # Anchor with thick top border, no other sides, plus child unit.
    parent = _el(
        1,
        "SECTION",
        bbox=(0, 0, 600, 200),
        border_top="6px solid rgb(0,0,0)",
        border_right="0px none rgb(0,0,0)",
        border_bottom="0px none rgb(0,0,0)",
        border_left="0px none rgb(0,0,0)",
    )
    child = _el(2, "P", text="Body copy", bbox=(0, 20, 600, 40))
    u = _unit([parent], children=[_unit([child])])
    d = classify_tier0(u, _CATALOG)
    assert d is not None
    assert d.metadata.get("recipe") == "brutalist_top_rule"
    assert d.metadata.get("decorate") == "top-rule"


def test_magazine_left_rule_fires():
    # No text on the anchor so body_paragraph / short_label can't preempt it.
    parent = _el(
        1,
        "DIV",
        bbox=(0, 0, 400, 96),
        border_top="0px none rgb(0,0,0)",
        border_right="0px none rgb(0,0,0)",
        border_bottom="0px none rgb(0,0,0)",
        border_left="6px solid rgb(0,0,0)",
    )
    d = classify_tier0(_unit([parent]), _CATALOG)
    assert d is not None
    assert d.metadata.get("recipe") == "magazine_left_rule"


# ---------------------------------------------------------------------------
# patterns.yaml: structural-tag recipes (elevated-card, bento, etc.)
# ---------------------------------------------------------------------------


def test_elevated_card_structural_hit():
    parent = _el(
        1,
        "DIV",
        bbox=(0, 0, 400, 240),
        box_shadow=(
            "0 1px 2px rgba(0,0,0,0.1), "
            "0 4px 8px rgba(0,0,0,0.08), "
            "0 12px 24px rgba(0,0,0,0.06)"
        ),
    )
    child = _el(2, "P", text="Card body", bbox=(0, 20, 400, 24))
    u = _unit([parent], children=[_unit([child])])
    assert "elevated-card" in _structural_hit_ids(u)


def test_bento_grid_3up_structural_hit():
    parent = _el(
        1,
        "DIV",
        bbox=(0, 0, 900, 300),
        grid_template_columns="repeat(3, 1fr)",
        gap="24px",
    )
    cells = []
    for i, x in enumerate((0, 312, 624)):
        cells.append(_unit([_el(10 + i, "DIV", bbox=(x, 0, 264, 240))]))
    u = _unit([parent], children=cells)
    assert "bento-grid-3up" in _structural_hit_ids(u)


def test_bento_grid_12col_structural_hit():
    parent = _el(
        1,
        "DIV",
        bbox=(0, 0, 1200, 600),
        grid_template_columns="repeat(12, 1fr)",
        gap="16px",
    )
    cells = [
        _unit([_el(20 + i, "DIV", bbox=(i * 100, 0, 88, 96))])
        for i in range(4)
    ]
    u = _unit([parent], children=cells)
    assert "bento-grid-12col" in _structural_hit_ids(u)


def test_top_strip_bg_band_fires():
    # Top-strip-bg-band sits at priority 205 — strictly after slide-gradient-bg
    # (200) and gradient-card-no-text (70). To assert the recipe matches at
    # all we filter out the higher-priority body/gradient catchalls and run
    # the matcher against just the top-strip-bg-band pattern.
    parent = _el(
        1,
        "BODY",
        bbox=(0, 0, 1280, 64),
        background_image="linear-gradient(90deg, rgb(99,102,241), rgb(236,72,153))",
    )
    u = _unit([parent])
    only = [
        p for p in get_default_patterns() if p.id == "top-strip-bg-band"
    ]
    assert only, "top-strip-bg-band recipe missing from manifest"
    hit = match(u, _CATALOG, only)
    assert hit is not None
    assert hit.decision is not None
    assert hit.decision.metadata.get("recipe") == "top_band"


# ---------------------------------------------------------------------------
# atoms.yaml: data-atom recipes
# ---------------------------------------------------------------------------


def test_atom_comp_bento_3up_structural_hit():
    el = _el(1, "DIV", data_atom="comp.bento-3up", bbox=(0, 0, 900, 300))
    assert "atom-comp-bento-3up" in _structural_hit_ids(_unit([el]))


def test_atom_comp_brutalist_top_rule_structural_hit():
    el = _el(1, "DIV", data_atom="comp.brutalist-top-rule", bbox=(0, 0, 600, 200))
    assert "atom-comp-brutalist-top-rule" in _structural_hit_ids(_unit([el]))


def test_atom_type_display_tight_decision():
    # Use SECTION tag so the generic short_label / body_paragraph recipes
    # (which restrict to DIV/P/SPAN) don't intercept the atom decision.
    el = _el(
        1,
        "SECTION",
        text="HEADLINE",
        bbox=(0, 0, 600, 96),
        data_atom="type.display-tight",
    )
    d = classify_tier0(_unit([el]), _CATALOG)
    assert d is not None
    assert d.metadata.get("recipe") == "atom_type_display_tight"
    assert d.metadata.get("axis") == "type"


def test_atom_type_mono_tag_decision():
    el = _el(
        1,
        "CODE",
        text="v1.0",
        bbox=(0, 0, 60, 18),
        data_atom="type.mono-tag",
    )
    d = classify_tier0(_unit([el]), _CATALOG)
    assert d is not None
    assert d.metadata.get("recipe") == "atom_type_mono_tag"


def test_atom_surf_elevated_stack_decision():
    el = _el(
        1,
        "DIV",
        bbox=(0, 0, 400, 240),
        data_atom="surf.elevated-stack",
    )
    d = classify_tier0(_unit([el]), _CATALOG)
    assert d is not None
    assert d.metadata.get("recipe") == "atom_surf_elevated_stack"
    assert d.metadata.get("decorate") == "elevation-multi-shadow"


def test_atom_dec_top_rule_decision():
    el = _el(
        1,
        "DIV",
        bbox=(0, 0, 400, 4),
        data_atom="dec.top-rule",
    )
    d = classify_tier0(_unit([el]), _CATALOG)
    assert d is not None
    assert d.metadata.get("recipe") == "atom_dec_top_rule"
    assert d.kind.value == "native_shape"


def test_atom_bg_grid_overlay_decision():
    el = _el(
        1,
        "DIV",
        bbox=(0, 0, 1280, 720),
        data_atom="bg.grid-overlay",
    )
    d = classify_tier0(_unit([el]), _CATALOG)
    assert d is not None
    assert d.metadata.get("recipe") == "atom_bg_grid_overlay"
    assert d.kind.value == "native_svg"


# ---------------------------------------------------------------------------
# Negative: unrelated unit must NOT trigger any of the new recipes.
# ---------------------------------------------------------------------------


_NEW_PATTERNS_RECIPES = {
    "mask_image_raster",
    "background_blend_mode_raster",
    "vertical_rail",
    "display_headline",
    "editorial_serif",
    "mono_tag",
    "brutalist_top_rule",
    "magazine_left_rule",
    "pill_by_radius",
    "text_with_shadow",
    "elevated_card",
    "pseudo_gradient_glyph",
    "feature_row",
    "bento_grid",
    "top_band",
}


def test_plain_paragraph_does_not_trigger_new_recipes():
    """An unstyled body paragraph routes to the existing body-paragraph recipe
    and must NOT fire any Phase 2c rule."""
    el = _el(
        1,
        "P",
        text="The quick brown fox jumps over the lazy dog repeatedly.",
        bbox=(0, 0, 600, 24),
        font_size="16px",
    )
    d = classify_tier0(_unit([el]), _CATALOG)
    assert d is not None
    assert d.metadata.get("recipe") not in _NEW_PATTERNS_RECIPES
