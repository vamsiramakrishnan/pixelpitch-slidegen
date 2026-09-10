"""Tier-1 deterministic rules. Fast, no LLM. Returns Decision or None (defer).

Rules are evaluated in order; first match wins. Each rule is a small function
to make adding/removing easy.
"""

from __future__ import annotations

import re
from collections.abc import Callable

from slidify.dom_walker import SVG_NATIVE_PATH_BUDGET
from slidify.geom import parse_px
from slidify.gradients import parse_gradient
from slidify.models import Decision, DecisionKind, UnitKind, VisualUnit
from slidify.preset_shapes import clip_path_to_preset
from slidify.shadows import is_translatable_shadow
from slidify.svg.shapes import is_translatable_svg


def _has_canvas(unit: VisualUnit) -> bool:
    return any(e.is_canvas for e in unit.all_elements())


def _has_video(unit: VisualUnit) -> bool:
    return any(e.is_video for e in unit.all_elements())


def _has_record_hint(unit: VisualUnit) -> bool:
    return any((e.pptx_record or "").strip() for e in unit.elements)


def _has_css_animation(unit: VisualUnit) -> bool:
    for e in unit.elements:
        name = (e.animation_name or "").strip().lower()
        if name and name != "none" and _duration_ms(e.animation_duration) > 0:
            return True
    return False


def _duration_ms(raw: str | None) -> float:
    """Return the longest CSS time in a comma-separated duration list."""
    if not raw:
        return 0.0
    values: list[float] = []
    for part in str(raw).split(","):
        s = part.strip().lower()
        try:
            if s.endswith("ms"):
                values.append(float(s[:-2]))
            elif s.endswith("s"):
                values.append(float(s[:-1]) * 1000.0)
        except ValueError:
            continue
    return max(values or [0.0])


def _has_complex_svg(unit: VisualUnit) -> bool:
    # Iterate this unit's DIRECT elements only — never descendants. A
    # child unit's SVG should fire its own classifier; bubbling the rule
    # up to the parent caused the slide-level unit to absorb every
    # sibling box's emit (slide 04 went 48 -> 3 shapes). Threshold
    # mirrors the dom_walker capture cap (SVG_NATIVE_PATH_BUDGET) so any
    # SVG that the walker could capture stays on the native path.
    return any(
        e.is_svg and e.svg_path_count > SVG_NATIVE_PATH_BUDGET for e in unit.elements
    )


def _has_simple_svg(unit: VisualUnit) -> bool:
    """True iff the unit IS substantially a simple SVG.

    Direct elements only — same reason as `_has_complex_svg`. Also
    require the SVG element to occupy at least 30% of the unit's area
    so a tiny decorative icon inside a card unit doesn't relabel the
    whole card.
    """
    unit_area = unit.bbox.area
    if unit_area <= 0:
        return False
    for e in unit.elements:
        if e.is_svg and e.svg_path_count > 0:
            if e.bbox.area / unit_area >= 0.30:
                return True
    return False


def _has_caller_rasterize_hint(unit: VisualUnit) -> bool:
    # Caller hint applies if any element in THIS unit (not child units) is hinted.
    return any(e.pptx_rasterize for e in unit.elements)


def _is_skipped(unit: VisualUnit) -> bool:
    return any(e.pptx_skip for e in unit.elements)


def _has_role_title(unit: VisualUnit) -> bool:
    # A unit is a "title" only if an element directly in it carries the role —
    # never a descendant unit (otherwise ancestors falsely inherit the role).
    return any(e.pptx_role == "title" for e in unit.elements)


def _is_single_image(unit: VisualUnit) -> Decision | None:
    """If unit has a single <img> that fills it, return NativePicture."""
    imgs = [e for e in unit.all_elements() if e.is_img and e.img_src]
    if len(imgs) != 1:
        return None
    img = imgs[0]
    # img must roughly fill the unit bbox
    if (
        img.bbox.w >= unit.bbox.w * 0.85
        and img.bbox.h >= unit.bbox.h * 0.85
    ):
        return Decision(
            kind=DecisionKind.NativePicture,
            confidence=1.0,
            reason="single image fills unit",
            metadata={"src": img.img_src, "alt": img.aria_label or ""},
            source_tier="tier1",
        )
    return None


def _has_3d_transform(unit: VisualUnit) -> bool:
    for e in unit.all_elements():
        t = e.transform
        if not t or t == "none":
            continue
        if _is_unsupported_transform(t):
            return True
    return False


def _is_unsupported_transform(transform: str) -> bool:
    """Return true for transforms that cannot be represented as native 2D PPTX."""
    t = (transform or "").strip().lower()
    if not t or t == "none":
        return False
    if "matrix3d" in t or "skew" in t or "perspective" in t:
        return True
    if t.startswith("matrix("):
        return False
    funcs = re.findall(r"([a-z0-9]+)\(", t)
    if not funcs:
        return False
    allowed = {"translate", "translatex", "translatey", "scale", "scalex", "scaley", "rotate"}
    return any(fn not in allowed for fn in funcs)


def _has_filter(unit: VisualUnit) -> bool:
    return any(e.filter and e.filter != "none" for e in unit.all_elements())


def _has_clip_path(unit: VisualUnit) -> bool:
    return any(e.clip_path and e.clip_path != "none" for e in unit.all_elements())


def _has_untranslatable_clip_path(unit: VisualUnit) -> bool:
    """True iff any element has a clip-path that doesn't map to a PPTX preset.

    inset(), circle(), and a handful of polygon() shapes are emit-time
    translatable via `slidify.preset_shapes`; any other expression
    (path(), url(#mask), unrecognized polygon) still forces raster.
    """
    for e in unit.all_elements():
        cp = e.clip_path
        if not cp or cp == "none":
            continue
        if clip_path_to_preset(cp, e.bbox) is None:
            return True
    return False


def _has_mix_blend_mode(unit: VisualUnit) -> bool:
    return any(
        getattr(e, "mix_blend_mode", "normal") not in ("normal", "")
        for e in unit.all_elements()
    )


def _has_backdrop_filter(unit: VisualUnit) -> bool:
    return any(
        getattr(e, "backdrop_filter", "none") not in ("none", "")
        for e in unit.all_elements()
    )


def _has_text_image_mask(unit: VisualUnit) -> bool:
    """The CSS `background-clip: text` + `background-image: url(...)` recipe.

    The text glyphs become a window into a raster image. PPTX's text-fill
    paths can't reproduce this — they support solid, gradient, and pattern
    fills, but not arbitrary raster clipped to glyph silhouettes. Force
    Raster so the visual is preserved, even at the cost of editability.
    """
    for e in unit.all_elements():
        clip = getattr(e, "background_clip", "border-box") or "border-box"
        if clip != "text":
            continue
        bg = e.background_image or "none"
        if bg != "none" and "url(" in bg:
            return True
    return False


def _has_url_background_image(unit: VisualUnit) -> bool:
    """An anchor element painting `background-image: url(...)` — slidify
    can't fetch+embed bg-images natively (only `<img>` tags route through
    `_emit_native_picture`), so the unit must raster."""
    for e in unit.elements:  # direct, not descendants
        bg = e.background_image or "none"
        if bg != "none" and "url(" in bg:
            return True
    return False


def _is_complex_pseudo(content: str | None, style: dict | None) -> bool:
    """A pseudo is *complex* — and forces raster — if it carries a bg-image,
    url() content, or a non-trivial decoration. Text-only pseudos (e.g.,
    `content: "01"`) are decorative and don't disqualify the parent from
    native classification.
    """
    if not content or content in ("none", '""', "''"):
        return False
    if "url(" in content:
        return True
    if style:
        bg_image = (style.get("background_image") or "none")
        if bg_image != "none" and "url(" in bg_image:
            return True
    return False


def _has_pseudo_content(unit: VisualUnit) -> bool:
    """Return True only if the unit has a *complex* pseudo we can't translate."""
    for e in unit.all_elements():
        if e.has_before and _is_complex_pseudo(e.before_content, e.pseudo_before_style):
            return True
        if e.has_after and _is_complex_pseudo(e.after_content, e.pseudo_after_style):
            return True
    return False


def _is_simple_leaf_text(unit: VisualUnit) -> bool:
    """Unit is a self-contained text region: its own elements include text
    and there are no child anchor units (text belonging to descendants
    should be classified as their own units)."""
    if unit.children:
        return False
    own_text = [e for e in unit.elements if e.text and e.text.strip()]
    if not own_text:
        return False
    if len(unit.elements) > 6:
        return False
    anchor = unit.elements[0]
    # Gradient bg is OK if we can translate it natively.
    if anchor.background_image and anchor.background_image != "none":
        if "url(" in anchor.background_image:
            return False
        if not parse_gradient(anchor.background_image):
            return False
    if (
        anchor.transform
        and anchor.transform != "none"
        and _is_unsupported_transform(anchor.transform)
    ):
        return False
    # Shadow is OK if translatable.
    if (
        anchor.box_shadow
        and anchor.box_shadow != "none"
        and not is_translatable_shadow(anchor.box_shadow)
    ):
        return False
    if anchor.filter and anchor.filter != "none":
        return False
    if _has_pseudo_content(unit):
        return False
    return True


def _is_plain_rectangle(unit: VisualUnit) -> bool:
    """Unit is a pure rectangle: bg (or border), no text content of its own."""
    elems = unit.all_elements()
    if any(e.text and e.text.strip() for e in elems):
        return False
    if any(e.is_canvas or e.is_svg or e.is_img or e.is_video for e in elems):
        return False
    if _has_pseudo_content(unit):
        return False
    if any(
        e.transform
        and e.transform != "none"
        and _is_unsupported_transform(e.transform)
        for e in elems
    ):
        return False
    if any(e.filter and e.filter != "none" for e in elems):
        return False
    # url() backgrounds we can't reproduce. Gradients we *can* — let those
    # through.
    for e in elems:
        bg_img = e.background_image
        if not bg_img or bg_img == "none":
            continue
        if "url(" in bg_img:
            return False
        # Unparseable / unrecognized gradient → treat as raster
        if not parse_gradient(bg_img):
            return False
    return True


def _has_nontrivial_shadow(box_shadow: str) -> bool:
    """A shadow we *might* be able to render natively in PPTX."""
    if not box_shadow or box_shadow == "none":
        return False
    # Multiple shadows separated by commas (and not inside rgba)
    # Simple heuristic: count commas outside parens
    depth = 0
    n_shadows = 1
    for ch in box_shadow:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        elif ch == "," and depth == 0:
            n_shadows += 1
    return n_shadows > 1


# ---------------------------------------------------------------------------
# Public rule entry point
# ---------------------------------------------------------------------------

RuleFn = Callable[[VisualUnit], Decision | None]


def rule_skip_hint(unit: VisualUnit) -> Decision | None:
    if _is_skipped(unit):
        return Decision(
            kind=DecisionKind.Skip,
            confidence=1.0,
            reason="data-pptx-skip",
            source_tier="tier1",
        )
    return None


def rule_canvas_always_raster(unit: VisualUnit) -> Decision | None:
    if _has_canvas(unit):
        return Decision(
            kind=DecisionKind.Raster,
            confidence=1.0,
            reason="canvas",
            source_tier="tier1",
        )
    return None


def rule_record_animation_gif(unit: VisualUnit) -> Decision | None:
    if _has_record_hint(unit) or _has_css_animation(unit) or _has_video(unit):
        return Decision(
            kind=DecisionKind.Raster,
            confidence=1.0,
            reason="animated_gif",
            metadata={"animated_gif": True},
            source_tier="tier1",
        )
    return None


def rule_video_always_raster(unit: VisualUnit) -> Decision | None:
    if _has_video(unit):
        return Decision(
            kind=DecisionKind.Raster,
            confidence=1.0,
            reason="video",
            source_tier="tier1",
        )
    return None


def rule_complex_svg_raster(unit: VisualUnit) -> Decision | None:
    if _has_complex_svg(unit):
        return Decision(
            kind=DecisionKind.Raster,
            confidence=1.0,
            reason="complex_svg",
            source_tier="tier1",
        )
    return None


def rule_simple_svg_raster(unit: VisualUnit) -> Decision | None:
    if not _has_simple_svg(unit):
        return None
    svg_el = next(
        (e for e in unit.all_elements() if e.is_svg and e.svg_shapes), None
    )
    if svg_el is not None and is_translatable_svg(svg_el.svg_shapes):
        return Decision(
            kind=DecisionKind.NativeSvg,
            confidence=0.9,
            reason="svg_translatable_primitives",
            source_tier="tier1",
        )
    return Decision(
        kind=DecisionKind.Raster,
        confidence=0.95,
        reason="svg_path_or_complex",
        source_tier="tier1",
    )


def rule_caller_rasterize(unit: VisualUnit) -> Decision | None:
    if _has_caller_rasterize_hint(unit):
        return Decision(
            kind=DecisionKind.Raster,
            confidence=1.0,
            reason="caller_hint",
            source_tier="tier1",
        )
    return None


def rule_single_image(unit: VisualUnit) -> Decision | None:
    return _is_single_image(unit)


def rule_native_table(unit: VisualUnit) -> Decision | None:
    """Route an HTML <table> to a single NativeTable emit op when the walker
    captured a translatable grid (no nested tables, no embedded media,
    cells contain only text). The fall-through is the existing pipeline,
    which would otherwise either rasterize the whole region (lossy) or
    emit dozens of stand-alone text frames (loses the table semantics).
    """
    table_el = next(
        (e for e in unit.all_elements() if e.is_table and e.table_data),
        None,
    )
    if table_el is None:
        return None
    rows = table_el.table_data.get("rows") or []
    if not rows:
        return None
    return Decision(
        kind=DecisionKind.NativeTable,
        confidence=1.0,
        reason="html_table",
        metadata={"table_element_id": table_el.id},
        source_tier="tier1",
    )


def rule_role_title(unit: VisualUnit) -> Decision | None:
    if _has_role_title(unit):
        return Decision(
            kind=DecisionKind.NativeText,
            confidence=1.0,
            reason="role=title",
            metadata={"role": "title"},
            source_tier="tier1",
        )
    return None


def rule_list_item(unit: VisualUnit) -> Decision | None:
    if unit.kind != UnitKind.ListItem:
        return None
    # If a list item has child anchor units (e.g., card with H3 + P sub-units),
    # leave it as a complex region — its children classify themselves.
    if unit.children:
        return None
    elems = unit.elements
    if any(e.is_canvas or e.is_svg or e.is_img or e.is_video for e in elems):
        return None
    has_text = any(e.text and e.text.strip() for e in elems)
    if not has_text:
        return None
    return Decision(
        kind=DecisionKind.NativeBullet,
        confidence=0.95,
        reason="list_item",
        source_tier="tier1",
    )


def rule_3d_transform_raster(unit: VisualUnit) -> Decision | None:
    if _has_3d_transform(unit):
        return Decision(
            kind=DecisionKind.Raster,
            confidence=0.95,
            reason="3d_transform",
            source_tier="tier1",
        )
    return None


def rule_filter_raster(unit: VisualUnit) -> Decision | None:
    if _has_filter(unit):
        return Decision(
            kind=DecisionKind.Raster,
            confidence=0.95,
            reason="filter",
            source_tier="tier1",
        )
    return None


def rule_clip_path_raster(unit: VisualUnit) -> Decision | None:
    if not _has_clip_path(unit):
        return None
    if not _has_untranslatable_clip_path(unit):
        # All clip-paths in this unit map to PPTX preset shapes — defer to
        # the native rules (plain_rectangle / leaf_text / etc.) so the
        # emitter can render `inset()` / `circle()` / common `polygon()`
        # via MSO_SHAPE primitives instead of rasterizing the unit.
        return None
    return Decision(
        kind=DecisionKind.Raster,
        confidence=0.9,
        reason="clip_path",
        source_tier="tier1",
    )


def rule_pptx_unsupported_raster(unit: VisualUnit) -> Decision | None:
    """Catch-all for CSS that PPTX can't reproduce natively.

    Each branch here corresponds to a known limit in the OOXML target:
      - `mix-blend-mode` — not supported on Shape elements (only via
        modern PowerPoint's limited blendOptions, which LibreOffice
        ignores).
      - `backdrop-filter` — no equivalent. The "frosted glass" effect
        is a runtime live blur of what's behind the shape; OOXML can
        only express baked-in fills.
      - `background-clip: text` on `background-image: url(...)` — text
        glyphs as a mask over a raster image. PPTX `<a:gradFill>` can
        clip to text, but `<a:blipFill>` (image fill) cannot.
      - `background-image: url(...)` directly on an anchor — slidify
        natively emits `<img>` via `_emit_native_picture` but doesn't
        fetch CSS bg-image URLs.

    Returning `Raster` lets the surgical-hybrid emit pipeline crop the
    source HTML render for the unit's region, which preserves the
    visual at the cost of editability.
    """
    if _has_mix_blend_mode(unit):
        return Decision(
            kind=DecisionKind.Raster,
            confidence=0.95,
            reason="mix_blend_mode",
            source_tier="tier1",
        )
    if _has_backdrop_filter(unit):
        return Decision(
            kind=DecisionKind.Raster,
            confidence=0.95,
            reason="backdrop_filter",
            source_tier="tier1",
        )
    if _has_text_image_mask(unit):
        return Decision(
            kind=DecisionKind.Raster,
            confidence=0.95,
            reason="text_image_mask",
            source_tier="tier1",
        )
    if _has_url_background_image(unit):
        return Decision(
            kind=DecisionKind.Raster,
            confidence=0.9,
            reason="bg_image_url",
            source_tier="tier1",
        )
    return None


def rule_simple_leaf_text(unit: VisualUnit) -> Decision | None:
    if _is_simple_leaf_text(unit):
        return Decision(
            kind=DecisionKind.NativeText,
            confidence=0.95,
            reason="simple_leaf_text",
            source_tier="tier1",
        )
    return None


def rule_plain_rectangle(unit: VisualUnit) -> Decision | None:
    if _is_plain_rectangle(unit):
        # Only emit if it actually has visible presence (else it's structural).
        elems = unit.all_elements()
        anchor_el = elems[0]
        bg_visible = anchor_el.background_color not in ("rgba(0, 0, 0, 0)", "transparent", "")
        border_visible = anchor_el.border and anchor_el.border != "none" and parse_px(anchor_el.border.split()[0] if anchor_el.border else "") > 0
        if not (bg_visible or border_visible):
            return None
        return Decision(
            kind=DecisionKind.NativeShape,
            confidence=0.95,
            reason="plain_rectangle",
            source_tier="tier1",
        )
    return None


# Order matters.
RULES: list[RuleFn] = [
    rule_skip_hint,
    rule_caller_rasterize,
    rule_record_animation_gif,
    rule_canvas_always_raster,
    rule_video_always_raster,
    rule_complex_svg_raster,
    rule_3d_transform_raster,
    rule_filter_raster,
    rule_clip_path_raster,
    rule_pptx_unsupported_raster,
    rule_simple_svg_raster,
    rule_single_image,
    rule_native_table,
    rule_role_title,
    rule_list_item,
    rule_simple_leaf_text,
    rule_plain_rectangle,
]


def classify_tier1(unit: VisualUnit) -> Decision | None:
    for rule in RULES:
        result = rule(unit)
        if result is not None:
            return result
    return None
