"""Tests for the Phase 2a extended predicate vocabulary in the matcher.

These exercise the handlers registered in `slidify.patterns.matcher` directly
via the `_PREDICATE_HANDLERS` table so the assertions are unaffected by the
shipped YAML rule deck.
"""

from __future__ import annotations

from slidify.models import BoundingBox, DomElement, TextRun, VisualUnit
from slidify.patterns import get_default_catalog
from slidify.patterns.matcher import _PREDICATE_HANDLERS

# ---------------------------------------------------------------------------
# Helpers
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


def _call(name: str, unit: VisualUnit, value) -> bool:
    handler = _PREDICATE_HANDLERS[name]
    anchor = unit.elements[0]
    return handler(unit, anchor, _CATALOG, value)


# ---------------------------------------------------------------------------
# Border predicates
# ---------------------------------------------------------------------------


def test_border_per_side_top_only_match_and_miss():
    el = _el(
        1,
        border_top="4px solid rgb(99, 102, 241)",
        border_right="0px none rgb(0, 0, 0)",
        border_bottom="0px none rgb(0, 0, 0)",
        border_left="0px none rgb(0, 0, 0)",
    )
    u = _unit([el])
    assert _call("anchor.border_per_side", u, {"top": "solid", "right": "none"})
    # Wrong style on top → miss.
    assert not _call("anchor.border_per_side", u, {"top": "dashed"})


def test_border_per_side_any_keyword():
    el = _el(1, border_left="2px dashed rgb(0,0,0)")
    u = _unit([el])
    assert _call("anchor.border_per_side", u, {"left": "any"})
    el2 = _el(2, border_left="0px none rgb(0,0,0)")
    assert not _call("anchor.border_per_side", _unit([el2]), {"left": "any"})


def test_border_top_width_min():
    el = _el(1, border_top="4px solid rgb(0,0,0)")
    assert _call("anchor.border_top_width_min", _unit([el]), 3)
    el2 = _el(2, border_top="1px solid rgb(0,0,0)")
    assert not _call("anchor.border_top_width_min", _unit([el2]), 3)


def test_border_left_width_min():
    el = _el(1, border_left="6px solid rgb(0,0,0)")
    assert _call("anchor.border_left_width_min", _unit([el]), 4)
    assert not _call("anchor.border_left_width_min", _unit([_el(2)]), 4)


def test_has_asymmetric_border_true_and_false():
    asym = _el(
        1,
        border_top="4px solid rgb(0,0,0)",
        border_right="0px none rgb(0,0,0)",
        border_bottom="0px none rgb(0,0,0)",
        border_left="0px none rgb(0,0,0)",
    )
    assert _call("anchor.has_asymmetric_border", _unit([asym]), True)
    sym = _el(
        2,
        border_top="2px solid rgb(0,0,0)",
        border_right="2px solid rgb(0,0,0)",
        border_bottom="2px solid rgb(0,0,0)",
        border_left="2px solid rgb(0,0,0)",
    )
    assert not _call("anchor.has_asymmetric_border", _unit([sym]), True)
    assert _call("anchor.has_asymmetric_border", _unit([sym]), False)


# ---------------------------------------------------------------------------
# Shadow predicates
# ---------------------------------------------------------------------------


def test_shadow_layers_min():
    multi = _el(
        1,
        box_shadow=(
            "rgba(0,0,0,0.3) 0px 4px 6px -1px, "
            "rgba(0,0,0,0.06) 0px 2px 4px -1px"
        ),
    )
    assert _call("anchor.shadow_layers_min", _unit([multi]), 2)
    single = _el(2, box_shadow="rgba(0,0,0,0.3) 0px 4px 6px -1px")
    assert not _call("anchor.shadow_layers_min", _unit([single]), 2)
    assert not _call("anchor.shadow_layers_min", _unit([_el(3)]), 1)


# ---------------------------------------------------------------------------
# Radius predicate
# ---------------------------------------------------------------------------


def test_radius_px_range():
    el = _el(1, border_radius="12px")
    assert _call("anchor.radius_px_range", _unit([el]), [8, 16])
    assert not _call("anchor.radius_px_range", _unit([el]), [16, 32])
    # None bound is unconstrained.
    assert _call("anchor.radius_px_range", _unit([el]), [None, 16])
    assert _call("anchor.radius_px_range", _unit([el]), [8, None])


# ---------------------------------------------------------------------------
# Typography family register
# ---------------------------------------------------------------------------


def test_font_family_family_classification():
    serif = _unit([_el(1, font_family='"Playfair Display", Georgia, serif')])
    sans = _unit([_el(2, font_family='Inter, "Helvetica Neue", sans-serif')])
    mono = _unit([_el(3, font_family='"JetBrains Mono", Menlo, monospace')])
    display = _unit([_el(4, font_family='"Bebas Neue", Impact, sans-serif')])
    assert _call("anchor.font_family_family", serif, "serif")
    assert _call("anchor.font_family_family", sans, ["sans", "mono"])
    assert _call("anchor.font_family_family", mono, "mono")
    assert _call("anchor.font_family_family", display, "display")
    # Negative: serif stack is not classified as sans.
    assert not _call("anchor.font_family_family", serif, "sans")


def test_letter_spacing_px_min_max():
    tight = _unit([_el(1, letter_spacing="0.5px")])
    wide = _unit([_el(2, letter_spacing="2.4px")])
    normal = _unit([_el(3, letter_spacing="normal")])
    assert _call("anchor.letter_spacing_px_min", wide, 2.0)
    assert not _call("anchor.letter_spacing_px_min", tight, 2.0)
    assert _call("anchor.letter_spacing_px_max", tight, 1.0)
    assert not _call("anchor.letter_spacing_px_max", wide, 1.0)
    # "normal" is treated as 0.
    assert _call("anchor.letter_spacing_px_max", normal, 0.0)


# ---------------------------------------------------------------------------
# Text-shadow / writing-mode
# ---------------------------------------------------------------------------


def test_has_text_shadow():
    none = _unit([_el(1, text_shadow="none")])
    has = _unit([_el(2, text_shadow="rgba(0,0,0,0.4) 0px 2px 4px")])
    assert _call("anchor.has_text_shadow", has, True)
    assert not _call("anchor.has_text_shadow", none, True)
    assert _call("anchor.has_text_shadow", none, False)


def test_writing_mode_in():
    vert = _unit([_el(1, writing_mode="vertical-rl")])
    horiz = _unit([_el(2, writing_mode="horizontal-tb")])
    assert _call(
        "anchor.writing_mode_in", vert, ["vertical-rl", "vertical-lr"]
    )
    assert not _call(
        "anchor.writing_mode_in", horiz, ["vertical-rl", "vertical-lr"]
    )


# ---------------------------------------------------------------------------
# Aspect / layout intent
# ---------------------------------------------------------------------------


def test_aspect_ratio_range_explicit():
    sq = _unit([_el(1, aspect_ratio="1 / 1", bbox=(0, 0, 100, 100))])
    wide = _unit([_el(2, aspect_ratio="16 / 9", bbox=(0, 0, 320, 180))])
    assert _call("anchor.aspect_ratio_range", sq, [0.95, 1.05])
    assert not _call("anchor.aspect_ratio_range", sq, [1.5, 2.0])
    assert _call("anchor.aspect_ratio_range", wide, [1.6, 2.0])


def test_aspect_ratio_range_auto_falls_back_to_bbox():
    el = _el(1, aspect_ratio="auto", bbox=(0, 0, 200, 100))
    u = _unit([el])
    assert _call("anchor.aspect_ratio_range", u, [1.9, 2.1])
    assert not _call("anchor.aspect_ratio_range", u, [3.0, 4.0])


def test_is_grid_container():
    grid = _unit([_el(1, grid_template_columns="repeat(3, 1fr)")])
    flex = _unit([_el(2, grid_template_columns="none")])
    assert _call("anchor.is_grid_container", grid, True)
    assert not _call("anchor.is_grid_container", flex, True)
    assert _call("anchor.is_grid_container", flex, False)


def test_grid_columns_count_repeat_and_explicit():
    rep = _unit([_el(1, grid_template_columns="repeat(3, 1fr)")])
    explicit = _unit([_el(2, grid_template_columns="240px 1fr 240px")])
    named = _unit(
        [_el(3, grid_template_columns="[col-start] 1fr [col-end] 2fr")]
    )
    assert _call("anchor.grid_columns_count", rep, 3)
    assert _call("anchor.grid_columns_count", explicit, 3)
    assert _call("anchor.grid_columns_count", named, 2)
    assert not _call("anchor.grid_columns_count", rep, 4)


def test_gap_px_min():
    g = _unit([_el(1, gap="24px 16px")])
    none = _unit([_el(2, gap="normal")])
    assert _call("anchor.gap_px_min", g, 16)
    assert not _call("anchor.gap_px_min", g, 32)
    assert not _call("anchor.gap_px_min", none, 1)


# ---------------------------------------------------------------------------
# Composition predicates
# ---------------------------------------------------------------------------


def test_siblings_uniform_aspect():
    # Three children with ~square aspect ratios.
    kids = [
        _unit([_el(10 + i, bbox=(0, 0, 120, 120))]) for i in range(3)
    ]
    parent = _unit([_el(1, bbox=(0, 0, 360, 120))], children=kids)
    assert _call(
        "siblings_uniform_aspect", parent, {"tolerance": 0.1, "n_min": 3}
    )
    # Now mix in an extreme outlier so the spread blows past the tolerance.
    kids_mixed = [
        _unit([_el(20, bbox=(0, 0, 120, 120))]),
        _unit([_el(21, bbox=(0, 0, 120, 120))]),
        _unit([_el(22, bbox=(0, 0, 600, 30))]),
    ]
    parent2 = _unit([_el(2, bbox=(0, 0, 600, 120))], children=kids_mixed)
    assert not _call(
        "siblings_uniform_aspect", parent2, {"tolerance": 0.1, "n_min": 3}
    )
    # Below n_min → False.
    short = _unit([_el(3)], children=kids[:1])
    assert not _call(
        "siblings_uniform_aspect", short, {"tolerance": 0.5, "n_min": 3}
    )


def test_siblings_icon_text_pair():
    icon = _el(11, tag="svg", is_svg=True, bbox=(0, 0, 24, 24))
    text = _el(12, tag="SPAN", text="Label", bbox=(30, 0, 80, 24),
               runs=[TextRun(text="Label")])
    parent = _unit([_el(1, bbox=(0, 0, 120, 24))],
                   children=[_unit([icon]), _unit([text])])
    assert _call("siblings_icon_text_pair", parent, True)
    # Two big SVGs is not an icon-text pair.
    big1 = _el(13, tag="svg", is_svg=True, bbox=(0, 0, 200, 200))
    big2 = _el(14, tag="svg", is_svg=True, bbox=(0, 0, 200, 200))
    bad = _unit([_el(2)], children=[_unit([big1]), _unit([big2])])
    assert not _call("siblings_icon_text_pair", bad, True)


def test_child_is_svg_icon_max_px():
    icon_child = _unit([_el(11, tag="svg", is_svg=True, bbox=(0, 0, 32, 32))])
    big_child = _unit([_el(12, tag="svg", is_svg=True, bbox=(0, 0, 200, 200))])
    assert _call("child.is_svg_icon_max_px",
                 _unit([_el(1)], children=[icon_child]), 48)
    assert not _call("child.is_svg_icon_max_px",
                     _unit([_el(2)], children=[big_child]), 48)


# ---------------------------------------------------------------------------
# Slide-relative position
# ---------------------------------------------------------------------------


def test_slide_y_band_top_center_bottom():
    top = _unit([_el(1, bbox=(0, 20, 200, 40))])
    center = _unit([_el(2, bbox=(0, 320, 200, 40))])
    bottom = _unit([_el(3, bbox=(0, 660, 200, 40))])
    assert _call("bbox.slide_y_band", top, "top")
    assert not _call("bbox.slide_y_band", top, "bottom")
    assert _call("bbox.slide_y_band", center, "center")
    assert not _call("bbox.slide_y_band", center, "top")
    assert _call("bbox.slide_y_band", bottom, "bottom")
    assert not _call("bbox.slide_y_band", bottom, "center")


def test_slide_y_band_thirds():
    upper = _unit([_el(1, bbox=(0, 100, 200, 40))])  # mid = 120
    lower = _unit([_el(2, bbox=(0, 500, 200, 40))])  # mid = 520
    assert _call("bbox.slide_y_band", upper, "upper-third")
    assert _call("bbox.slide_y_band", lower, "lower-third")
    assert not _call("bbox.slide_y_band", upper, "lower-third")


# ---------------------------------------------------------------------------
# Must-raster signals
# ---------------------------------------------------------------------------


def test_mask_image_any():
    masked = _unit(
        [_el(1, mask_image="linear-gradient(black, transparent)")]
    )
    plain = _unit([_el(2, mask_image="none")])
    assert _call("mask_image", masked, "any")
    assert not _call("mask_image", plain, "any")
    assert _call("mask_image", plain, "none")


def test_background_blend_mode_any():
    blended = _unit([_el(1, background_blend_mode="multiply")])
    plain = _unit([_el(2, background_blend_mode="normal")])
    assert _call("background_blend_mode", blended, "any")
    assert not _call("background_blend_mode", plain, "any")
    assert _call("background_blend_mode", plain, "none")


# ---------------------------------------------------------------------------
# Pseudo-element introspection
# ---------------------------------------------------------------------------


def test_pseudo_has_gradient():
    grad = _unit([_el(1, has_before=True, pseudo_before_style={
        "background_image": "linear-gradient(135deg, rgb(99,102,241), rgb(236,72,153))",
    })])
    plain = _unit([_el(2, has_after=True, pseudo_after_style={
        "background_image": "url(noise.png)",
    })])
    none = _unit([_el(3)])
    assert _call("pseudo.has_gradient", grad, True)
    assert not _call("pseudo.has_gradient", plain, True)
    assert not _call("pseudo.has_gradient", none, True)
    assert _call("pseudo.has_gradient", none, False)
