"""DOM → unit coverage oracle.

The harvester reports unmatched DOM *signatures* — units that the clusterer
produced but no tier-0 pattern recognized. It cannot report DOM subtrees
that the clusterer dropped entirely. We just spent a long debugging cycle
finding that ``<div class="spec-row"><span>...</span><span>...</span></div>``
was silently disappearing because the clusterer's anchor heuristic missed
inline-element-only containers.

This module is the dual of the harvester: it walks every DOM element with
text content and checks whether *some* produced unit's bbox intersects it.
Anything with effectively no overlap (< 10% by element area) becomes a
``CoverageGap`` row on `ConversionResult.coverage_gaps`.

The pass is purely observational — it never mutates the elements, units,
or decisions list. Any exception inside the gap-finding loop is caught
and skipped so this auditing pass cannot break a real conversion.
"""

from __future__ import annotations

import structlog

from slidify.dom.units import flatten
from slidify.models import CoverageGap, DomElement, VisualUnit

log = structlog.get_logger(__name__)


# SVG drawing primitives — descendants of an <svg> are emitted via the
# NativeSvg op for the parent, NOT as their own units. Reporting them as
# coverage gaps would be a false positive (the text/tspan/path is owned
# by the SVG renderer, not the unit clusterer).
_SVG_PRIMITIVE_TAGS: frozenset[str] = frozenset(
    {
        "path", "line", "circle", "polygon", "polyline", "rect", "ellipse",
        "text", "tspan", "g", "use", "defs", "marker", "mask", "pattern",
        "lineargradient", "radialgradient", "stop", "clippath", "filter",
        "symbol", "title", "desc", "foreignobject",
    }
)

# Walker should already skip these; defense in depth.
_NEVER_REPORT_TAGS: frozenset[str] = frozenset(
    {"SCRIPT", "STYLE", "META", "LINK", "NOSCRIPT"}
)

# Threshold below which an element's bbox area is considered "noise" (a
# zero-size flex helper, a hairline rule, etc.). Tunable via the public
# ``min_bbox_area`` parameter; this is just the default.
_DEFAULT_MIN_BBOX_AREA = 200.0

# Ignore elements whose own text is shorter than this — icon ligatures,
# single-character pseudos, leading-letter accents.
_DEFAULT_MIN_TEXT_CHARS = 4

# An element is treated as "uncovered" when no unit's bbox overlaps more
# than this fraction of the element's own area. 0.10 is generous enough
# that a partial banner / ribbon spilling out of a card still counts as
# covered, but a fully-disjoint subtree (the spec-row case) doesn't.
_OVERLAP_FLOOR = 0.10


def find_coverage_gaps(
    elements: list[DomElement],
    roots: list[VisualUnit],
    *,
    min_text_chars: int = _DEFAULT_MIN_TEXT_CHARS,
    min_bbox_area: float = _DEFAULT_MIN_BBOX_AREA,
    slide_index: int = 0,
) -> list[CoverageGap]:
    """For every DOM element with text content whose region is NOT covered
    by any unit's bbox, emit a CoverageGap row.

    A "gap" is a DOM subtree whose own text sits in slide space that no
    produced unit owns. These are content drops the clusterer made
    silently — the equivalent of an unmatched signature, but for content,
    not shape.

    Parameters:
      elements: the flat DomElement list from the walker (post off-canvas
        filtering — pass the same list `cluster()` consumed).
      roots: top-level units returned by `cluster()`.
      min_text_chars: ignore elements whose own text is shorter than
        this (avoid noise from icon ligatures, single-character pseudos).
      min_bbox_area: ignore elements smaller than this (hairlines,
        zero-size flex helpers).
      slide_index: written into each row so consumers can pivot by slide.
    """
    if not elements:
        return []

    try:
        units = flatten(roots) if roots else []
        unit_bboxes = [u.bbox for u in units if u.bbox is not None]
    except Exception as e:  # pragma: no cover — defensive
        log.warning("coverage.flatten_failed", error=str(e))
        return []

    # Build parent_id → ancestor map once so the SVG-descendant guard is O(1)
    # per element after a single hop-walk amortized via memoization.
    by_id: dict[int, DomElement] = {e.id: e for e in elements}
    svg_descendant_cache: dict[int, bool] = {}

    def _is_svg_descendant(el: DomElement) -> bool:
        """True iff this element is an SVG primitive itself OR has any
        SVG ancestor (`is_svg=True`). Memoized via parent walk.
        """
        if el.id in svg_descendant_cache:
            return svg_descendant_cache[el.id]
        # The element itself is an SVG primitive tag — covered by NativeSvg.
        if (el.tag or "").lower() in _SVG_PRIMITIVE_TAGS:
            svg_descendant_cache[el.id] = True
            return True
        # The element itself is an <svg> — it anchors as its own unit, but
        # its descendants will be filtered. The svg element is fine to
        # report when it has fallback text and no unit owns it; treat
        # is_svg as a "do not recurse-report" flag for descendants only.
        cur_id = el.parent_id
        for _ in range(64):  # capped — DOM depth in a slide is bounded
            if cur_id is None:
                svg_descendant_cache[el.id] = False
                return False
            if cur_id in svg_descendant_cache:
                ans = svg_descendant_cache[cur_id]
                svg_descendant_cache[el.id] = ans
                return ans
            parent = by_id.get(cur_id)
            if parent is None:
                svg_descendant_cache[el.id] = False
                return False
            if parent.is_svg:
                svg_descendant_cache[el.id] = True
                return True
            cur_id = parent.parent_id
        svg_descendant_cache[el.id] = False
        return False

    out: list[CoverageGap] = []
    for el in elements:
        try:
            tag_u = (el.tag or "").upper()
            if tag_u in _NEVER_REPORT_TAGS:
                continue
            if el.is_offcanvas and not el.allow_overflow:
                continue
            # Skip pseudo-element placeholders / non-text elements outright.
            text = (el.text or "").strip()
            if not text:
                continue
            if len(text) < min_text_chars:
                continue
            if el.bbox.area < min_bbox_area:
                continue
            # SVG primitives + any descendant of <svg>: these are owned
            # by the parent <svg>'s NativeSvg op, not by their own unit.
            if _is_svg_descendant(el):
                continue
            # Compute max overlap_ratio over all units.
            max_ratio = 0.0
            el_area = el.bbox.area
            if el_area <= 0:
                continue
            for ub in unit_bboxes:
                inter = el.bbox.intersect_area(ub)
                if inter <= 0:
                    continue
                r = inter / el_area
                if r > max_ratio:
                    max_ratio = r
                    if max_ratio >= 1.0:
                        break
            if max_ratio >= _OVERLAP_FLOOR:
                continue

            reason = _classify_reason(el)
            sample = text if len(text) <= 80 else text[:77] + "..."
            out.append(
                CoverageGap(
                    slide_index=slide_index,
                    element_id=el.id,
                    tag=el.tag or "",
                    cls=el.cls or "",
                    bbox_x=el.bbox.x,
                    bbox_y=el.bbox.y,
                    bbox_w=el.bbox.w,
                    bbox_h=el.bbox.h,
                    sample_text=sample,
                    overlap_ratio=round(max_ratio, 4),
                    reason=reason,
                    stable_selector=el.stable_selector or "",
                )
            )
        except Exception as e:  # pragma: no cover — defensive
            # Auditing pass MUST NOT break a real conversion.
            log.warning(
                "coverage.gap_iter_failed",
                element_id=getattr(el, "id", "?"),
                error=str(e),
            )
            continue

    if out:
        log.info(
            "coverage.gaps_found",
            slide=slide_index,
            n=len(out),
            sample_tag=out[0].tag,
            sample_text=out[0].sample_text[:40],
        )
    return out


def _classify_reason(el: DomElement) -> str:
    """Pick a short human-readable cause for the gap.

    Defaults to ``"no unit anchored this subtree"`` and only narrows the
    diagnosis when we can identify a concrete failure mode.
    """
    display = (el.display or "inline").lower()
    if display == "contents":
        return "display:contents; parent did not anchor"
    return "no unit anchored this subtree"
