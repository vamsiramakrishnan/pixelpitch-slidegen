"""Generic YAML pattern matcher.

Loads `patterns.yaml`, compiles each entry into a Predicate, and returns the
first matching pattern's emit spec. Patterns are sorted by priority (lower
first); ties broken by manifest order. Patterns with `tag: structural` never
override an existing decision — they're observed for telemetry only.

The predicate language is intentionally narrow: every clause is a function
of (unit, anchor, catalog) → bool. Adding a new clause type means extending
`_PREDICATE_HANDLERS` below — no manifest changes required for callers.
"""

from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass, field
from functools import lru_cache
from importlib import resources
from typing import Any

import yaml

from slidify.geom import parse_px
from slidify.gradients import parse_gradient
from slidify.models import Decision, DecisionKind, DomElement, VisualUnit
from slidify.patterns.tailwind import TailwindCatalog, _split_classes
from slidify.shadows import _split_top_level, is_translatable_shadow

# ---------------------------------------------------------------------------
# Pattern dataclasses
# ---------------------------------------------------------------------------


@dataclass
class Pattern:
    id: str
    priority: int
    match: dict
    emit: dict
    tag: str = "decision"  # "decision" | "structural"


@dataclass
class PatternHit:
    pattern_id: str
    decision: Decision | None  # None for structural-tag patterns
    metadata: dict = field(default_factory=dict)


@dataclass
class PatternStats:
    """Telemetry: which patterns fired, which units missed everything."""

    hits_by_id: dict[str, int] = field(default_factory=dict)
    structural_hits_by_id: dict[str, int] = field(default_factory=dict)
    n_units: int = 0
    n_decided: int = 0

    @property
    def coverage(self) -> float:
        return self.n_decided / self.n_units if self.n_units > 0 else 0.0


# ---------------------------------------------------------------------------
# Predicate evaluation
# ---------------------------------------------------------------------------


def _anchor_classes(anchor: DomElement) -> list[str]:
    return _split_classes(anchor.cls)


def _has_visible_decoration(anchor: DomElement) -> bool:
    if anchor.background_color and anchor.background_color != "rgba(0, 0, 0, 0)":
        return True
    if anchor.background_image and anchor.background_image != "none":
        return True
    if parse_px(anchor.border_radius) > 0:
        return True
    # Border check: parse the width — the computed value can be e.g.
    # "0px none rgb(245,245,247)" which is "no visible border" but
    # ``!= "none"`` would falsely flag it as decorated. Look for any
    # parseable width > 0 in the leading token.
    if anchor.border and anchor.border != "none":
        leading = anchor.border.split(None, 1)[0]
        if parse_px(leading) > 0:
            return True
    for border in (
        anchor.border_top,
        anchor.border_right,
        anchor.border_bottom,
        anchor.border_left,
    ):
        if border and border != "none":
            leading = border.split(None, 1)[0]
            if parse_px(leading) > 0 and "none" not in border.split():
                return True
    return False


def _bbox_aspect(unit: VisualUnit) -> float:
    w = max(0.0001, unit.bbox.w)
    h = max(0.0001, unit.bbox.h)
    return max(w / h, h / w)


def _bg_image_kind(anchor: DomElement) -> str:
    bg = anchor.background_image
    if not bg or bg == "none":
        return "none"
    if "url(" in bg:
        return "url"
    if parse_gradient(bg):
        return "gradient"
    return "any"


def _shadow_kind(anchor: DomElement) -> str:
    if not anchor.box_shadow or anchor.box_shadow == "none":
        return "none"
    return "translatable" if is_translatable_shadow(anchor.box_shadow) else "any"


# Each handler returns True iff the *clause* is satisfied by the unit.
_PREDICATE_HANDLERS: dict[str, Any] = {}


def _handler(name: str):
    def deco(fn):
        _PREDICATE_HANDLERS[name] = fn
        return fn
    return deco


@_handler("anchor.tag_in")
def _h_tag_in(unit, anchor, catalog, value):
    # Chromium's getComputedStyle yields tag names in uppercase
    # (`BUTTON`, `HR`, `DIV`).  Normalise both sides so atoms.yaml can
    # be written in the conventional lowercase form (`[button]`,
    # `[hr]`).  Without this, every tag-based atom misses silently.
    tag = (anchor.tag or "").lower()
    return tag in [str(v).lower() for v in value]


@_handler("anchor.tag_not_in")
def _h_tag_not_in(unit, anchor, catalog, value):
    tag = (anchor.tag or "").lower()
    return tag not in [str(v).lower() for v in value]


@_handler("classes_all")
def _h_classes_all(unit, anchor, catalog, value):
    cls = set(_anchor_classes(anchor))
    return all(c in cls for c in value)


@_handler("classes_any")
def _h_classes_any(unit, anchor, catalog, value):
    cls = set(_anchor_classes(anchor))
    return any(c in cls for c in value)


@_handler("classes_none")
def _h_classes_none(unit, anchor, catalog, value):
    cls = set(_anchor_classes(anchor))
    return all(c not in cls for c in value)


@_handler("classes_glob_any")
def _h_classes_glob_any(unit, anchor, catalog, value):
    cls = _anchor_classes(anchor)
    for pattern in value:
        for token in cls:
            if fnmatch.fnmatchcase(token, pattern):
                return True
    return False


@_handler("anchor.data_atom_id")
def _h_data_atom_id(unit, anchor, catalog, value):
    """Match `data-atom="..."` on the cluster anchor.

    `value` may be a single id (``"bg.mesh"``), a list of ids, or a glob
    (``"bg.*"``). Atom hints short-circuit signature inference: when an
    author tags ``data-atom="surf.glass"`` slidify routes the unit to the
    canonical recipe for that atom rather than re-deriving it from the
    DOM signature each time. The catalog of registered ids lives in
    ``examples/landing/atoms.html``; the emit recipes live in
    ``slidify/patterns/data/atoms.yaml``.
    """
    atom_id = (anchor.data_atom or "").strip()
    if not atom_id:
        return False
    if isinstance(value, list):
        for v in value:
            if "*" in v or "?" in v:
                if fnmatch.fnmatchcase(atom_id, v):
                    return True
            elif atom_id == v:
                return True
        return False
    if isinstance(value, str):
        if "*" in value or "?" in value:
            return fnmatch.fnmatchcase(atom_id, value)
        return atom_id == value
    return False


@_handler("anchor.data_atom_namespace")
def _h_data_atom_namespace(unit, anchor, catalog, value):
    """Match the namespace of an atom id (e.g. ``"bg"`` matches ``"bg.mesh"``,
    ``"bg.conic"``, …). Useful for axis-wide patterns that handle every atom
    in a family the same way."""
    atom_id = (anchor.data_atom or "").strip()
    if not atom_id or "." not in atom_id:
        return False
    ns = atom_id.split(".", 1)[0]
    if isinstance(value, list):
        return ns in value
    return ns == value


@_handler("classes_any_rasterize_only")
def _h_rasterize_only(unit, anchor, catalog, value):
    if not value:
        return True
    return catalog.has_rasterize_only(anchor.cls)


@_handler("has_token_resolved")
def _h_has_token_resolved(unit, anchor, catalog, value):
    """Check that at least one anchor class resolves to the given family."""
    families = value if isinstance(value, list) else [value]
    for token in _anchor_classes(anchor):
        r = catalog.classify_token(token)
        if r is not None and r.family in families:
            return True
    return False


@_handler("bg_image_kind")
def _h_bg_image_kind(unit, anchor, catalog, value):
    return _bg_image_kind(anchor) == value


@_handler("shadow")
def _h_shadow(unit, anchor, catalog, value):
    if value == "any":
        return _shadow_kind(anchor) != "none"
    return _shadow_kind(anchor) == value


@_handler("transform")
def _h_transform(unit, anchor, catalog, value):
    has_t = bool(anchor.transform) and anchor.transform != "none"
    return (value == "any") if has_t else (value == "none")


@_handler("filter")
def _h_filter(unit, anchor, catalog, value):
    has_f = bool(anchor.filter) and anchor.filter != "none"
    return (value == "any") if has_f else (value == "none")


@_handler("mix_blend_mode")
def _h_mix_blend_mode(unit, anchor, catalog, value):
    """`mix_blend_mode: any` matches anchors / descendants with any non-
    `normal` blend mode. PPTX has no equivalent so the match must route
    the unit to Raster."""
    has = any(
        (getattr(e, "mix_blend_mode", "normal") or "normal") not in ("normal", "")
        for e in unit.all_elements()
    )
    return (value == "any") if has else (value == "none")


@_handler("backdrop_filter")
def _h_backdrop_filter(unit, anchor, catalog, value):
    has = any(
        (getattr(e, "backdrop_filter", "none") or "none") not in ("none", "")
        for e in unit.all_elements()
    )
    return (value == "any") if has else (value == "none")


@_handler("text_image_mask")
def _h_text_image_mask(unit, anchor, catalog, value):
    """`background-clip: text` over a `background-image: url(...)`. Text
    glyphs are a window into a raster — PPTX text-fill can't express it."""
    found = False
    for e in unit.all_elements():
        clip = getattr(e, "background_clip", "border-box") or "border-box"
        if clip != "text":
            continue
        bg = e.background_image or "none"
        if bg != "none" and "url(" in bg:
            found = True
            break
    return (value == "any") if found else (value == "none")


@_handler("bg_image_url")
def _h_bg_image_url(unit, anchor, catalog, value):
    """Anchor (or any direct element) with `background-image: url(...)`.
    Slidify can't fetch+embed bg-image URLs natively (only `<img>` tags)."""
    has = any(
        (e.background_image or "none") != "none" and "url(" in (e.background_image or "")
        for e in unit.elements
    )
    return (value == "any") if has else (value == "none")


@_handler("own_text")
def _h_own_text(unit, anchor, catalog, value):
    has = any(
        (e.text and e.text.strip())
        or (e.runs and any(r.text.strip() for r in e.runs if not r.is_break))
        for e in unit.elements
    )
    return has if value else not has


@_handler("is_text_container")
def _h_is_text_container(unit, anchor, catalog, value):
    """Match anchors flagged as text containers by the walker — the multi-run
    case ``<p>Some <em>emphasised</em> body.</p>``. The emitter renders those
    as a single text frame with per-run styling; recipes that route to
    NativeText want to opt into matching them in addition to plain leaf
    text."""
    return bool(getattr(anchor, "is_text_container", False)) == bool(value)


@_handler("children")
def _h_children(unit, anchor, catalog, value):
    return (value == "any") if unit.children else (value == "none")


@_handler("has_visible_decoration")
def _h_has_visible_decoration(unit, anchor, catalog, value):
    return _has_visible_decoration(anchor) == bool(value)


# Numeric bound predicates ---------------------------------------------------


def _num_bound(actual: float, comp: str, target: float) -> bool:
    if comp == "max":
        return actual <= target
    if comp == "min":
        return actual >= target
    return False


@_handler("bbox.h_max")
def _h_bbox_h_max(unit, anchor, catalog, value):
    return _num_bound(unit.bbox.h, "max", float(value))


@_handler("bbox.h_min")
def _h_bbox_h_min(unit, anchor, catalog, value):
    return _num_bound(unit.bbox.h, "min", float(value))


@_handler("bbox.w_max")
def _h_bbox_w_max(unit, anchor, catalog, value):
    return _num_bound(unit.bbox.w, "max", float(value))


@_handler("bbox.w_min")
def _h_bbox_w_min(unit, anchor, catalog, value):
    return _num_bound(unit.bbox.w, "min", float(value))


@_handler("bbox.aspect_max")
def _h_bbox_aspect_max(unit, anchor, catalog, value):
    return _bbox_aspect(unit) <= float(value)


@_handler("bbox.aspect_min")
def _h_bbox_aspect_min(unit, anchor, catalog, value):
    return _bbox_aspect(unit) >= float(value)


@_handler("font_size_px_max")
def _h_fsize_max(unit, anchor, catalog, value):
    return _num_bound(parse_px(anchor.font_size), "max", float(value))


@_handler("font_size_px_min")
def _h_fsize_min(unit, anchor, catalog, value):
    return _num_bound(parse_px(anchor.font_size), "min", float(value))


@_handler("border_radius_px_min")
def _h_radius_min(unit, anchor, catalog, value):
    return _num_bound(parse_px(anchor.border_radius), "min", float(value))


@_handler("border_radius_px_max")
def _h_radius_max(unit, anchor, catalog, value):
    return _num_bound(parse_px(anchor.border_radius), "max", float(value))


# ---- New: position, text length, child counts, color luminance ------------


@_handler("bbox.y_max")
def _h_y_max(unit, anchor, catalog, value):
    """unit's top must be at or above this y (in px). Useful for "is in the
    top of the slide" predicates."""
    return unit.bbox.y <= float(value)


@_handler("bbox.y_min")
def _h_y_min(unit, anchor, catalog, value):
    return unit.bbox.y >= float(value)


@_handler("bbox.bottom_min")
def _h_bottom_min(unit, anchor, catalog, value):
    """unit's bottom edge ≥ value. Useful for footer detection."""
    return unit.bbox.y + unit.bbox.h >= float(value)


@_handler("text_length_max")
def _h_text_length_max(unit, anchor, catalog, value):
    text = "".join(
        e.text or "" for e in unit.elements if e.text and e.text.strip()
    )
    return len(text.strip()) <= int(value)


@_handler("text_length_min")
def _h_text_length_min(unit, anchor, catalog, value):
    text = "".join(
        e.text or "" for e in unit.elements if e.text and e.text.strip()
    )
    return len(text.strip()) >= int(value)


@_handler("n_children_max")
def _h_n_children_max(unit, anchor, catalog, value):
    return len(unit.children) <= int(value)


@_handler("n_children_min")
def _h_n_children_min(unit, anchor, catalog, value):
    return len(unit.children) >= int(value)


@_handler("anchor.is_img")
def _h_is_img(unit, anchor, catalog, value):
    return anchor.is_img == bool(value)


@_handler("anchor.is_canvas")
def _h_is_canvas(unit, anchor, catalog, value):
    return anchor.is_canvas == bool(value)


@_handler("anchor.is_svg")
def _h_is_svg(unit, anchor, catalog, value):
    return anchor.is_svg == bool(value)


@_handler("anchor.has_svg_shapes")
def _h_has_svg_shapes(unit, anchor, catalog, value):
    """True when the walker captured a non-empty ``svg_shapes`` list on the
    anchor. Used to gate tier-0 ``NativeSvg`` emit so the recipe only fires
    when the emitter has real geometry to render — SVGs that exceed
    ``SVG_NATIVE_PATH_BUDGET`` come through with ``svg_shapes=None`` and
    must defer to tier-1 / Raster instead of claiming the unit and emitting
    an empty native-svg op."""
    has = bool(anchor.svg_shapes)
    return has if value else not has


@_handler("anchor.is_table")
def _h_is_table(unit, anchor, catalog, value):
    """Anchor's ``is_table`` flag — set by the walker only when the table is
    translatable (no nested tables, no embedded media, regular grid). Gates
    tier-0 ``NativeTable`` emit so non-translatable tables fall through to
    the existing pipeline."""
    return bool(getattr(anchor, "is_table", False)) == bool(value)


@_handler("anchor.has_mixed_content")
def _h_has_mixed_content(unit, anchor, catalog, value):
    """Anchor has a non-empty ``mixed_content_text`` — direct text alongside
    block descendants. The walker captures this on the parent so the unit
    clusterer can emit the parent's text leaf above the child unit stack
    (the editorial ``<div class="meta-value">Helmsworth Industries...<span
    class="sub">Steering committee...</span></div>`` pattern). Without a
    recipe gating on this flag, tier-0 typography rules (which require
    ``own_text: true``) wouldn't fire because ``el.text`` is null on
    mixed-content elements."""
    has = bool((getattr(anchor, "mixed_content_text", None) or "").strip())
    return has if value else not has


@_handler("anchor.opacity_max")
def _h_anchor_opacity_max(unit, anchor, catalog, value):
    """Anchor's `opacity` is at most `value`. Use `0.95` as the typical
    threshold for "visibly translucent" (anything less is essentially full)."""
    try:
        return float(anchor.opacity) <= float(value)
    except (TypeError, ValueError):
        return False


@_handler("anchor.opacity_min")
def _h_anchor_opacity_min(unit, anchor, catalog, value):
    try:
        return float(anchor.opacity) >= float(value)
    except (TypeError, ValueError):
        return False


# Heuristic luminance via background color hex. Useful for "is dark theme."
def _hex_luma(hex_str: str) -> float | None:
    s = (hex_str or "").strip().lower()
    if s.startswith("rgb(") or s.startswith("rgba("):
        # parse "rgb(r, g, b[, a])"
        import re

        m = re.match(r"rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)", s)
        if not m:
            return None
        r, g, b = int(m.group(1)), int(m.group(2)), int(m.group(3))
    elif s.startswith("#") and len(s) >= 7:
        try:
            r = int(s[1:3], 16)
            g = int(s[3:5], 16)
            b = int(s[5:7], 16)
        except ValueError:
            return None
    else:
        return None
    # Rec. 709 luminance.
    return (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255.0


@_handler("anchor.bg_dark")
def _h_bg_dark(unit, anchor, catalog, value):
    """anchor's bg color luminance ≤ 0.30 ⇔ "dark"."""
    luma = _hex_luma(anchor.background_color)
    if luma is None:
        return not bool(value)  # unknown → treat as not-dark
    return (luma <= 0.30) == bool(value)


@_handler("anchor.bg_light")
def _h_bg_light(unit, anchor, catalog, value):
    luma = _hex_luma(anchor.background_color)
    if luma is None:
        return not bool(value)
    return (luma >= 0.85) == bool(value)


@_handler("anchor.text_dark")
def _h_text_dark(unit, anchor, catalog, value):
    luma = _hex_luma(anchor.color)
    if luma is None:
        return not bool(value)
    return (luma <= 0.30) == bool(value)


@_handler("font_weight_min")
def _h_weight_min(unit, anchor, catalog, value):
    from slidify.fonts import parse_weight

    return parse_weight(anchor.font_weight) >= int(value)


@_handler("font_weight_max")
def _h_weight_max(unit, anchor, catalog, value):
    from slidify.fonts import parse_weight

    return parse_weight(anchor.font_weight) <= int(value)


# ---- Composite predicates: inspect child units' shape -----------------------


def _child_anchors(unit):
    """Return the anchor element of each child unit (or None for empty)."""
    out = []
    for c in unit.children:
        if c.elements:
            out.append(c.elements[0])
        else:
            out.append(None)
    return out


@_handler("children_text_count_min")
def _h_children_text_count_min(unit, anchor, catalog, value):
    """Count how many child units are text-bearing (have own text). Useful for
    stat-card recognition: 'has at least 2 text children'."""
    n = 0
    for c in unit.children:
        if any(
            (e.text and e.text.strip())
            or (e.runs and any(r.text.strip() for r in e.runs if not r.is_break))
            for e in c.elements
        ):
            n += 1
    return n >= int(value)


@_handler("children_with_gradient_count_min")
def _h_children_grad_count_min(unit, anchor, catalog, value):
    n = 0
    for a in _child_anchors(unit):
        if a is None:
            continue
        bg = a.background_image or ""
        if "gradient(" in bg:
            n += 1
    return n >= int(value)


@_handler("children_only_text")
def _h_children_only_text(unit, anchor, catalog, value):
    """True iff every child unit is text-only (own text, no further children)."""
    if not unit.children:
        return not bool(value)
    for c in unit.children:
        if c.children:
            return not bool(value)
        has_text = any(
            (e.text and e.text.strip())
            or (e.runs and any(r.text.strip() for r in e.runs if not r.is_break))
            for e in c.elements
        )
        if not has_text:
            return not bool(value)
    return bool(value)


# ---------------------------------------------------------------------------
# Phase 2a: extended predicate vocabulary
# ---------------------------------------------------------------------------


def _parse_border_side(side: str) -> tuple[float, str]:
    """Return (width_px, style) for one CSS per-side border string.

    Examples: "none" → (0, "none"); "4px solid rgb(...)" → (4.0, "solid");
    "0px none rgb(...)" → (0, "none")."""
    s = (side or "").strip()
    if not s or s.lower() == "none":
        return (0.0, "none")
    parts = s.split()
    width = parse_px(parts[0]) if parts else 0.0
    style = parts[1].lower() if len(parts) >= 2 else "none"
    if width <= 0.0:
        style = "none"
    return (width, style)


@_handler("anchor.border_per_side")
def _h_border_per_side(unit, anchor, catalog, value):
    if not isinstance(value, dict):
        return False
    sides = {
        "top": _parse_border_side(anchor.border_top),
        "right": _parse_border_side(anchor.border_right),
        "bottom": _parse_border_side(anchor.border_bottom),
        "left": _parse_border_side(anchor.border_left),
    }
    for side, want in value.items():
        if side not in sides:
            return False
        _width, style = sides[side]
        w = (want or "").lower()
        if w == "none":
            if style != "none":
                return False
        elif w == "any":
            if style == "none":
                return False
        else:
            if style != w:
                return False
    return True


@_handler("anchor.border_top_width_min")
def _h_border_top_w_min(unit, anchor, catalog, value):
    width, _ = _parse_border_side(anchor.border_top)
    return width >= float(value)


@_handler("anchor.border_left_width_min")
def _h_border_left_w_min(unit, anchor, catalog, value):
    width, _ = _parse_border_side(anchor.border_left)
    return width >= float(value)


@_handler("anchor.has_asymmetric_border")
def _h_asymmetric_border(unit, anchor, catalog, value):
    sides = [
        (anchor.border_top or "none").strip(),
        (anchor.border_right or "none").strip(),
        (anchor.border_bottom or "none").strip(),
        (anchor.border_left or "none").strip(),
    ]
    parsed = [_parse_border_side(s) for s in sides]
    any_visible = any(w > 0 for w, _ in parsed)
    uniform = all(s == sides[0] for s in sides)
    is_asym = any_visible and not uniform
    return is_asym == bool(value)


@_handler("anchor.shadow_layers_min")
def _h_shadow_layers_min(unit, anchor, catalog, value):
    s = (anchor.box_shadow or "").strip()
    if not s or s.lower() == "none":
        n = 0
    else:
        n = len([p for p in _split_top_level(s, ",") if p])
    return n >= int(value)


@_handler("anchor.radius_px_range")
def _h_radius_range(unit, anchor, catalog, value):
    if not isinstance(value, list) or len(value) != 2:
        return False
    lo, hi = value
    r = parse_px(anchor.border_radius)
    if lo is not None and r < float(lo):
        return False
    if hi is not None and r > float(hi):
        return False
    return True


_DISPLAY_NAME_TOKENS = (
    "display", "black", "bebas", "anton", "oswald", "archivo black", "druk",
    "editorial",
)
_MONO_NAME_TOKENS = ("mono", "courier", "menlo", "consolas", "monaco")


def _classify_font_family(family: str) -> str:
    f = (family or "").lower()
    if any(t in f for t in _MONO_NAME_TOKENS):
        return "mono"
    has_serif = "serif" in f and "sans-serif" not in f
    if has_serif:
        # Check that the "serif" hit isn't just inside "sans-serif"
        stripped = f.replace("sans-serif", "")
        if "serif" in stripped:
            return "serif"
    if any(t in f for t in _DISPLAY_NAME_TOKENS):
        return "display"
    return "sans"


@_handler("anchor.font_family_family")
def _h_font_family_family(unit, anchor, catalog, value):
    families = value if isinstance(value, list) else [value]
    return _classify_font_family(anchor.font_family) in families


def _parse_letter_spacing_px(ls: str) -> float:
    s = (ls or "").strip().lower()
    if not s or s == "normal":
        return 0.0
    if s.endswith("px"):
        return parse_px(s)
    return 0.0  # em / other units unresolved here


@_handler("anchor.letter_spacing_px_min")
def _h_ls_min(unit, anchor, catalog, value):
    return _parse_letter_spacing_px(anchor.letter_spacing) >= float(value)


@_handler("anchor.letter_spacing_px_max")
def _h_ls_max(unit, anchor, catalog, value):
    return _parse_letter_spacing_px(anchor.letter_spacing) <= float(value)


@_handler("anchor.has_text_shadow")
def _h_has_text_shadow(unit, anchor, catalog, value):
    has = bool(anchor.text_shadow) and anchor.text_shadow not in ("none", "")
    return has == bool(value)


@_handler("anchor.writing_mode_in")
def _h_writing_mode_in(unit, anchor, catalog, value):
    modes = value if isinstance(value, list) else [value]
    return (anchor.writing_mode or "horizontal-tb") in modes


def _parse_aspect_ratio(s: str) -> float | None:
    if not s:
        return None
    txt = s.strip().lower()
    if not txt or txt == "auto":
        return None
    if "/" in txt:
        a, _, b = txt.partition("/")
        try:
            num = float(a.strip())
            den = float(b.strip())
            if den == 0:
                return None
            return num / den
        except ValueError:
            return None
    try:
        return float(txt)
    except ValueError:
        return None


@_handler("anchor.aspect_ratio_range")
def _h_aspect_ratio_range(unit, anchor, catalog, value):
    if not isinstance(value, list) or len(value) != 2:
        return False
    lo, hi = value
    ratio = _parse_aspect_ratio(anchor.aspect_ratio)
    if ratio is None:
        h = anchor.bbox.h
        if h <= 0:
            return False
        ratio = anchor.bbox.w / h
    if lo is not None and ratio < float(lo):
        return False
    if hi is not None and ratio > float(hi):
        return False
    return True


@_handler("anchor.is_grid_container")
def _h_is_grid(unit, anchor, catalog, value):
    has = bool(anchor.grid_template_columns) and anchor.grid_template_columns not in ("none", "")
    return has == bool(value)


_REPEAT_RE = re.compile(r"^repeat\(\s*(\d+)\s*,", re.IGNORECASE)


def _count_grid_tracks(tracks: str) -> int | None:
    s = (tracks or "").strip()
    if not s or s.lower() == "none":
        return 0
    m = _REPEAT_RE.match(s)
    if m:
        try:
            return int(m.group(1))
        except ValueError:
            return None
    # Strip named-line brackets like "[col-start] 1fr [col-end]".
    cleaned = re.sub(r"\[[^\]]*\]", "", s)
    tokens = cleaned.split()
    return len(tokens) if tokens else None


@_handler("anchor.grid_columns_count")
def _h_grid_cols(unit, anchor, catalog, value):
    n = _count_grid_tracks(anchor.grid_template_columns)
    if n is None:
        return False
    return n == int(value)


@_handler("anchor.gap_px_min")
def _h_gap_min(unit, anchor, catalog, value):
    s = (anchor.gap or "").strip().lower()
    if not s or s == "normal":
        first = 0.0
    else:
        token = s.split()[0]
        first = parse_px(token)
    return first >= float(value)


def _aspect_of(bbox) -> float | None:
    if bbox.h <= 0 or bbox.w <= 0:
        return None
    return bbox.w / bbox.h


@_handler("siblings_uniform_aspect")
def _h_siblings_uniform_aspect(unit, anchor, catalog, value):
    if not isinstance(value, dict):
        return False
    tol = float(value.get("tolerance", 0.15))
    n_min = int(value.get("n_min", 3))
    if len(unit.children) < n_min:
        return False
    ratios: list[float] = []
    for c in unit.children:
        if not c.elements:
            return False
        r = _aspect_of(c.elements[0].bbox)
        if r is None:
            return False
        ratios.append(r)
    mean = sum(ratios) / len(ratios)
    if mean <= 0:
        return False
    return all(abs(r - mean) / mean <= tol for r in ratios)


@_handler("siblings_icon_text_pair")
def _h_siblings_icon_text_pair(unit, anchor, catalog, value):
    def _is_icon(el: DomElement) -> bool:
        return bool(el.is_svg) and max(el.bbox.w, el.bbox.h) <= 48
    def _is_text(el: DomElement) -> bool:
        if el.text and el.text.strip():
            return True
        if el.runs and any(r.text.strip() for r in el.runs if not r.is_break):
            return True
        return False
    pairs: list[tuple[DomElement, DomElement]] = []
    if len(unit.children) == 2:
        c0, c1 = unit.children[0].elements, unit.children[1].elements
        if c0 and c1:
            pairs.append((c0[0], c1[0]))
    if len(unit.elements) == 2:
        pairs.append((unit.elements[0], unit.elements[1]))
    has_pair = any(
        (_is_icon(a) and _is_text(b)) or (_is_icon(b) and _is_text(a))
        for a, b in pairs
    )
    return has_pair == bool(value)


@_handler("child.is_svg_icon_max_px")
def _h_child_svg_icon(unit, anchor, catalog, value):
    cap = float(value)
    for c in unit.children:
        if not c.elements:
            continue
        a = c.elements[0]
        if a.is_svg and max(a.bbox.w, a.bbox.h) <= cap:
            return True
    return False


@_handler("bbox.slide_y_band")
def _h_slide_y_band(unit, anchor, catalog, value):
    band = str(value).lower()
    y = unit.bbox.y
    h = unit.bbox.h
    midpoint = y + h / 2
    bottom = y + h
    if band == "top":
        return y < 80
    if band == "upper-third":
        return 80 <= midpoint < 240
    if band == "center":
        return 240 <= midpoint < 480
    if band == "lower-third":
        return 480 <= midpoint < 640
    if band == "bottom":
        return bottom >= 640
    return False


@_handler("mask_image")
def _h_mask_image(unit, anchor, catalog, value):
    has = any(
        (getattr(e, "mask_image", None) or "none") not in ("none", "", None)
        for e in unit.all_elements()
    )
    return (value == "any") if has else (value == "none")


@_handler("background_blend_mode")
def _h_bg_blend_mode(unit, anchor, catalog, value):
    has = any(
        (getattr(e, "background_blend_mode", None) or "normal") not in ("normal", "", None)
        for e in unit.all_elements()
    )
    return (value == "any") if has else (value == "none")


@_handler("pseudo.has_gradient")
def _h_pseudo_has_gradient(unit, anchor, catalog, value):
    found = False
    for e in unit.all_elements():
        for has_flag, style in (
            (e.has_before, e.pseudo_before_style),
            (e.has_after, e.pseudo_after_style),
        ):
            if not has_flag or not isinstance(style, dict):
                continue
            bg = style.get("background_image") or ""
            if "gradient(" in bg:
                found = True
                break
        if found:
            break
    return found == bool(value)


# ---------------------------------------------------------------------------
# Manifest loading
# ---------------------------------------------------------------------------


_MANIFEST_FILES = ("patterns.yaml", "atoms.yaml")


def _load_manifest_yaml() -> list[Pattern]:
    """Load every YAML manifest in ``slidify/patterns/data/``.

    ``patterns.yaml`` carries the always-on tier-0 recipes (typography,
    cards, decorations, must-rasters). ``atoms.yaml`` carries the atom
    catalog — recipes keyed off the ``data-atom`` author hint. Both are
    optional; missing files are skipped quietly so the package keeps
    booting on partial installs.
    """
    pkg = resources.files("slidify.patterns.data")
    out: list[Pattern] = []
    for fname in _MANIFEST_FILES:
        path = pkg / fname
        try:
            text = path.read_text(encoding="utf-8")
        except (FileNotFoundError, OSError):
            continue
        raw = yaml.safe_load(text) or {}
        for entry in raw.get("patterns", []):
            out.append(
                Pattern(
                    id=entry["id"],
                    priority=int(entry.get("priority", 100)),
                    match=entry.get("match", {}),
                    emit=entry.get("emit", {}),
                    tag=entry.get("tag", "decision"),
                )
            )
    out.sort(key=lambda p: p.priority)
    return out


@lru_cache(maxsize=1)
def get_default_patterns() -> list[Pattern]:
    return _load_manifest_yaml()


# ---------------------------------------------------------------------------
# Matcher
# ---------------------------------------------------------------------------


def _check_match(unit: VisualUnit, anchor: DomElement, catalog: TailwindCatalog, match: dict) -> bool:
    for key, value in match.items():
        handler = _PREDICATE_HANDLERS.get(key)
        if handler is None:
            return False  # unknown clause — fail closed
        if not handler(unit, anchor, catalog, value):
            return False
    return True


def _emit_to_decision(emit: dict, pattern_id: str) -> Decision | None:
    kind_str = emit.get("kind", "")
    if kind_str == "observe":
        return None  # structural-only
    try:
        kind = DecisionKind(_camel_to_snake(kind_str))
    except ValueError:
        # Allow either CamelCase ("NativeShape") or snake ("native_shape").
        try:
            kind = DecisionKind[kind_str]
        except KeyError:
            return None
    return Decision(
        kind=kind,
        confidence=float(emit.get("confidence", 0.9)),
        reason=emit.get("reason", f"recipe:{pattern_id}"),
        metadata=dict(emit.get("metadata", {})),
        source_tier="tier0",
    )


def _camel_to_snake(s: str) -> str:
    out = []
    for i, ch in enumerate(s):
        if ch.isupper() and i > 0:
            out.append("_")
        out.append(ch.lower())
    return "".join(out)


def match(
    unit: VisualUnit,
    catalog: TailwindCatalog,
    patterns: list[Pattern] | None = None,
    stats: PatternStats | None = None,
) -> PatternHit | None:
    """Return the first matching pattern (or None for miss).

    Structural-tag patterns are recorded into stats but don't return a Decision
    — they're observation-only.
    """
    pats = patterns if patterns is not None else get_default_patterns()
    if stats is not None:
        stats.n_units += 1
    if not unit.elements:
        return None
    anchor = unit.elements[0]

    decision_hit: PatternHit | None = None
    for p in pats:
        if not _check_match(unit, anchor, catalog, p.match):
            continue
        if p.tag == "structural":
            if stats is not None:
                stats.structural_hits_by_id[p.id] = (
                    stats.structural_hits_by_id.get(p.id, 0) + 1
                )
            continue
        d = _emit_to_decision(p.emit, p.id)
        if d is None:
            continue
        if stats is not None:
            stats.hits_by_id[p.id] = stats.hits_by_id.get(p.id, 0) + 1
            stats.n_decided += 1
        decision_hit = PatternHit(pattern_id=p.id, decision=d, metadata=dict(p.emit.get("metadata", {})))
        break
    return decision_hit
