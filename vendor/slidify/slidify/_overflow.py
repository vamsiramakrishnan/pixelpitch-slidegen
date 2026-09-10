"""Slide-bounds overflow detector.

The DOM walker captures the resolved pixel layout — every element's
``getBoundingClientRect()`` becomes a `DomElement.bbox`. Any element whose
bbox extends past the slide frame (1280×720 by default) is, by definition,
clipped or off-canvas. That's a layout bug the author would have caught by
eyeballing a PNG; surfacing it here turns it into a structured compile-time
signal so agents can fix the source HTML deterministically instead of
guessing from raster previews.

The detector is intentionally narrow:

  * Only "leaf-ish" elements are reported (own_text or no children, or a
    direct visual element like an svg/img). A wrapper div whose children
    overflow gets reported via the children, not twice.
  * Tiny overflows (< 1px) are ignored — sub-pixel layout noise.
  * Elements explicitly tagged ``data-pptx-skip="true"`` are skipped.
  * Pseudo-elements (``::before`` / ``::after``) are ignored — they don't
    have bboxes in the walker output anyway.

The output is a flat list of `OverflowElement` records that ride on
`ConversionResult.overflow_elements`. Empty list = clean.
"""

from __future__ import annotations

from slidify.models import DomElement, OverflowElement

# Tolerance for sub-pixel rounding in getBoundingClientRect(). Layout that
# overflows by less than this is treated as flush-aligned and not reported.
_TOLERANCE_PX = 1.0

# Atoms whose visual recipe *requires* overflow at the leaves. The author
# tags the cluster anchor (e.g. ``data-atom="type.echo"``); requiring them
# to also tag every descendant span with ``data-pptx-allow-overflow="true"``
# would be a tax on the canonical recipes the atomic seed is meant to
# encourage. The pipeline reads the parent's atom hint and treats every
# descendant as if it carried the allow-overflow attribute itself.
#
# Keep this list narrow — the rule should only cover atoms where overflow
# is *intrinsic* to the recipe, not "any atom that ever happens to bleed."
_OVERFLOW_BY_DESIGN_ATOMS: frozenset[str] = frozenset(
    {
        # Type recipes that stamp the same word with progressive offset:
        # `type.echo` (4-6 spans translated horizontally) and
        # `type.longshadow` (8-deep stack translated diagonally) both put
        # the trailing layers outside the focal element's bbox by design.
        "type.echo",
        "type.longshadow",
        # `type.marquee` is a tape that continues past the viewport — the
        # parent has overflow:hidden, so it's clipped visually, but the
        # spans themselves report their full content width.
        "type.marquee",
        # Motion implications behave the same way: ghost trails, scrolling
        # tapes, and speed lines all depend on bleeds.
        "motion.echo",
        "motion.marquee",
        "motion.speed-lines",
    }
)


def detect_overflow(
    slide_index: int,
    elements: list[DomElement],
    viewport_w: int,
    viewport_h: int,
) -> list[OverflowElement]:
    """Return one `OverflowElement` per leaf element that crosses a slide edge.

    Args:
        slide_index: 0-based slide index in the deck (for reporting).
        elements: the rendered DOM elements (post-walker).
        viewport_w: slide width in px (1280 by default).
        viewport_h: slide height in px (720 by default).

    The detector applies an "allow-overflow inheritance" pass: descendants
    of any element tagged with an `_OVERFLOW_BY_DESIGN_ATOMS` atom are
    treated as if they carry ``data-pptx-allow-overflow="true"``. This
    keeps the canonical type.echo / type.longshadow / type.marquee recipes
    from spamming false positives at the leaves.
    """
    by_id: dict[int, DomElement] = {el.id: el for el in elements}
    auto_allowed: set[int] = _collect_auto_allowed(elements, by_id)
    out: list[OverflowElement] = []
    for el in elements:
        if el.pptx_skip or el.allow_overflow or el.id in auto_allowed:
            continue
        if el.is_offcanvas and not el.allow_overflow:
            continue
        # Resolve the nearest ancestor data-atom for the hint generator.
        nearest_atom = _nearest_atom(el, by_id)
        # Only leaf-ish elements; wrapper containers that overflow because
        # of their kids would otherwise spam duplicate reports.
        if not _is_leafish(el):
            continue
        bbox = el.bbox
        if bbox.w <= 0 or bbox.h <= 0:
            continue
        right = bbox.x + bbox.w
        bottom = bbox.y + bbox.h
        # Iterate in priority order: bottom is the most common landing-page
        # failure (content too tall), so report it first per element.
        for axis, overflow in (
            ("bottom", bottom - viewport_h),
            ("right", right - viewport_w),
            ("left", -bbox.x),
            ("top", -bbox.y),
        ):
            if overflow > _TOLERANCE_PX:
                out.append(
                    OverflowElement(
                        slide_index=slide_index,
                        axis=axis,
                        overflow_px=round(overflow, 2),
                        bbox_x=bbox.x,
                        bbox_y=bbox.y,
                        bbox_w=bbox.w,
                        bbox_h=bbox.h,
                        tag=el.tag,
                        data_atom=el.data_atom,
                        stable_selector=el.stable_selector,
                        sample_text=_sample_text(el),
                        hint=_authoring_hint(el, axis, overflow, nearest_atom),
                    )
                )
                break  # one report per element; biggest violation first
    return out


# Hint table — each entry maps a (atom, axis) hit to a single-line author
# action. Keep the messages imperative, deterministic, and short enough to
# print in a CLI summary. The fallback at the bottom covers the unmatched
# cases (no atom hint), giving the author the viewport-math reminder.
_HINTS_BY_ATOM: dict[str, str] = {
    "type.gfill-2": "shrink the headline (≤ 64 px in a 90 px row, ≤ 84 px in a 160 px row).",
    "type.gfill-4": "shrink the headline or split the title across two rows.",
    "type.dropcap": "lower the ::first-letter font-size or widen the body's container.",
    "type.stroke": "shrink the display word; stroke type measures wider than fill type at the same size.",
    "type.stroke-thick": "shrink the display word; thick stroke adds ~8 px per side.",
    "type.kinetic-baseline": "reduce per-letter translateY so the baseline range stays inside the row.",
    "type.mixed": "shrink the multi-treatment line — mixed-weight runs measure wider than uniform weight.",
    "comp.edge-rotated": "the rotated caption is missing `transform-origin`; pin it (e.g. `right center`) and the right edge stops bleeding.",
    "data.ticker": "trim the tape so it fits 1232 px — the parent's overflow:hidden clips visually but the spans still report full width.",
    "data.logo-cloud": "drop a logo or wrap the row to two lines; logos at 18 px require ~140 px each at 8 columns.",
    "ui.avatar-cluster": "drop an avatar or shrink the badge size; cluster overflows past the column gutter.",
    "mask.arch": "the arch is taller than its column — shrink to ≤ 440 px or move into a row with `height` (not `min-height`).",
    "bg.aurora-band": "tag the aurora with `data-pptx-allow-overflow=\"true\"` — bleeds are part of this atom's visual recipe.",
}


def _authoring_hint(
    el: DomElement, axis: str, overflow_px: float, nearest_atom: str
) -> str:
    """Produce a one-line, fix-it message for an overflowing element.

    Resolution order:
      1. Element's own ``data_atom`` matches the hint table.
      2. Nearest ancestor's ``data_atom`` matches the hint table.
      3. Generic per-axis fallback — viewport math reminder.

    The hint is informational; the overflow is reported regardless. Authors
    can dismiss the hint by tagging the element with
    ``data-pptx-allow-overflow="true"`` or by following the suggested fix.
    """
    own_atom = el.data_atom or ""
    if own_atom and own_atom in _HINTS_BY_ATOM:
        return f"atom `{own_atom}`: {_HINTS_BY_ATOM[own_atom]}"
    if nearest_atom and nearest_atom in _HINTS_BY_ATOM:
        return f"under `{nearest_atom}`: {_HINTS_BY_ATOM[nearest_atom]}"
    px = int(round(overflow_px))
    if axis == "bottom":
        return (
            f"bottom edge crossed by {px} px — shrink the row, the type, or "
            "split the slide. Viewport budget is 720 px (head ~80, footer ~50)."
        )
    if axis == "right":
        return (
            f"right edge crossed by {px} px — trim the line, lower font-size, "
            "or wrap. Viewport width is 1280 px."
        )
    if axis == "left":
        return (
            f"left edge crossed by {px} px — usually a `transform: translateX(-N)` "
            "with N larger than the parent's left padding."
        )
    if axis == "top":
        return (
            f"top edge crossed by {px} px — usually a negative `top` or "
            "`translateY(-N)` larger than the parent's top padding."
        )
    return ""


def _nearest_atom(el: DomElement, by_id: dict[int, DomElement]) -> str:
    """Return the closest ancestor's ``data_atom`` (or empty string)."""
    if el.data_atom:
        return el.data_atom
    parent_id = el.parent_id
    for _ in range(32):
        if parent_id is None:
            return ""
        parent = by_id.get(parent_id)
        if parent is None:
            return ""
        if parent.data_atom:
            return parent.data_atom
        parent_id = parent.parent_id
    return ""


def _collect_auto_allowed(
    elements: list[DomElement],
    by_id: dict[int, DomElement],
) -> set[int]:
    """Return ids of elements that descend from an overflow-by-design atom.

    Walks each element's parent chain (capped — DOM depth in our viewport
    is bounded) and adds the id to the result if any ancestor's
    ``data_atom`` is in ``_OVERFLOW_BY_DESIGN_ATOMS``. Memoizes the
    answer per ancestor id to keep this O(n) in element count.
    """
    # cache[id] == True  → at least one ancestor of this element is tagged
    # with an overflow-by-design atom. The element itself does NOT count: a
    # `type.echo` div on its own should still report its own bleed unless
    # the author intended it (and authors tag the cluster anchor, not the
    # leaf). Keeps the inheritance one-directional: parents grant immunity
    # to descendants only.
    cache: dict[int, bool] = {}

    def _ancestor_is_overflow_atom(el: DomElement) -> bool:
        if el.id in cache:
            return cache[el.id]
        parent_id = el.parent_id
        # Walk up; cap at 32 hops to avoid pathological cycles (the walker
        # snapshots a tree, but defensive ceiling is cheap).
        for _ in range(32):
            if parent_id is None:
                cache[el.id] = False
                return False
            parent = by_id.get(parent_id)
            if parent is None:
                cache[el.id] = False
                return False
            # First: does THIS ancestor carry the trigger? (Must check
            # before consulting the cache — the parent's own cache entry
            # records what's true *above* the parent, not whether the
            # parent itself is the trigger.)
            if parent.data_atom in _OVERFLOW_BY_DESIGN_ATOMS:
                cache[el.id] = True
                return True
            # Otherwise inherit the parent's verdict if known.
            if parent.id in cache:
                cache[el.id] = cache[parent.id]
                return cache[el.id]
            parent_id = parent.parent_id
        cache[el.id] = False
        return False

    return {el.id for el in elements if _ancestor_is_overflow_atom(el)}


def _is_leafish(el: DomElement) -> bool:
    """Worth reporting individually? True for visual leaves and text nodes."""
    if el.tag in ("IMG", "SVG", "svg", "CANVAS", "VIDEO"):
        return True
    if el.is_text_container or (el.text and el.text.strip()):
        return True
    if el.runs:
        return True
    # Self-decorated elements (cards / pills with a background but no kids).
    has_bg = (el.background_color or "rgba(0, 0, 0, 0)") != "rgba(0, 0, 0, 0)"
    has_border = (el.border or "none") not in ("none", "0px none rgb(0, 0, 0)")
    return has_bg or has_border


def _sample_text(el: DomElement) -> str:
    raw = (el.text or "").strip()
    if not raw and el.runs:
        raw = "".join(r.text or "" for r in el.runs if not r.is_break).strip()
    if len(raw) > 60:
        raw = raw[:57] + "…"
    return raw
