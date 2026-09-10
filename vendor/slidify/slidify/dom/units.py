"""Visual Unit Clusterer.

Groups DOM elements into VisualUnits — atomic regions that should be classified
together. See spec §4.4.
"""

from __future__ import annotations

import re

import structlog

from slidify.geom import parse_px
from slidify.models import DomElement, UnitKind, VisualUnit

log = structlog.get_logger(__name__)

# CSS color value indicating "no fill" — these strings appear in computed styles.
_TRANSPARENT_COLORS = {
    "rgba(0, 0, 0, 0)",
    "transparent",
    "",
}


def _is_transparent(color: str) -> bool:
    if not color:
        return True
    c = color.strip().lower()
    if c in _TRANSPARENT_COLORS:
        return True
    # rgba(*, *, *, 0)
    m = re.match(r"rgba?\(\s*\d+\s*,\s*\d+\s*,\s*\d+\s*,\s*0(?:\.0+)?\s*\)", c)
    return bool(m)


def _has_visible_border(border: str) -> bool:
    if not border or border == "none":
        return False
    # Computed border looks like "1px solid rgb(0, 0, 0)" — check non-zero width
    # AND that it isn't just "0px ...".
    parts = border.split()
    if not parts:
        return False
    width = parse_px(parts[0])
    if width <= 0:
        return False
    # Style none means no border
    if "none" in parts and "solid" not in border and "dashed" not in border:
        return False
    return True


def _has_any_visible_border(el: DomElement) -> bool:
    """True iff the element has a visible border on the shorthand OR any
    individual side. The CSS shorthand ``border`` is empty when the four
    sides differ, so an element with only ``border-bottom: 1px solid``
    fails ``_has_visible_border(el.border)`` despite carrying real
    decoration. This helper consults the per-side fields from Phase 1
    so accent-stripe / magazine-rule / spec-row patterns anchor properly.
    """
    if _has_visible_border(el.border):
        return True
    for side in (el.border_top, el.border_right, el.border_bottom, el.border_left):
        if _has_visible_border(side):
            return True
    return False


def _has_shadow(box_shadow: str) -> bool:
    return bool(box_shadow) and box_shadow.lower() != "none"


def _has_radius(border_radius: str) -> bool:
    return parse_px(border_radius) > 0


def _has_transform(transform: str) -> bool:
    return bool(transform) and transform.lower() != "none"


def _has_pseudo(el: DomElement) -> bool:
    """A pseudo only counts as visually-distinguishing decoration if it carries
    a bg-image / url() content. Plain text pseudos are treated as decoration
    folded into the parent."""
    if el.has_before:
        if el.before_content and "url(" in el.before_content:
            return True
        sb = el.pseudo_before_style or {}
        if "url(" in (sb.get("background_image") or ""):
            return True
    if el.has_after:
        if el.after_content and "url(" in el.after_content:
            return True
        sa = el.pseudo_after_style or {}
        if "url(" in (sa.get("background_image") or ""):
            return True
    return False


# Inline-formatting tags whose styling is captured as TextRun on the parent
# text container, never as a separate visual unit. The DOM walker already
# folds these into their parent's `runs` when the parent qualifies as a
# text container; the cases that survive here are inline elements whose
# parent is NOT a text container (mixed inline + block siblings, or a flex
# row of independent <span>s). For those, the inline tag still shouldn't
# anchor a unit by virtue of carrying text alone — a bare `<span>some
# text</span>` is a styled run, not a paragraph. It DOES still anchor when
# it carries real visual decoration of its own (badge bg, pill border-
# radius, button shadow), since at that point it's effectively a block.
_INLINE_FORMATTING_TAGS: frozenset[str] = frozenset({
    "SPAN", "EM", "STRONG", "B", "I", "U", "A", "CODE", "SMALL",
    "MARK", "SUP", "SUB", "INS", "DEL", "KBD", "ABBR", "CITE",
})

# SVG drawing primitives. These are emitted by `slidify.svg_shapes` as
# native shapes within the parent <svg>'s native-svg op; promoting one to
# its own visual unit causes (a) the SVG renderer to lose track of it and
# (b) tier-0 to log a "circle(xform)..." unmatched signature for every
# rotated SVG path. Always fold into the SVG anchor.
_SVG_PRIMITIVE_TAGS: frozenset[str] = frozenset({
    "PATH", "CIRCLE", "ELLIPSE", "RECT", "LINE", "POLYGON", "POLYLINE",
    "TEXT", "TSPAN", "G", "USE", "DEFS", "MARKER", "MASK", "PATTERN",
    "LINEARGRADIENT", "RADIALGRADIENT", "STOP", "CLIPPATH", "FILTER",
    "SYMBOL", "TITLE", "DESC", "FOREIGNOBJECT",
})


def _is_anchor(el: DomElement, parent_bg: str | None) -> bool:
    """An element is anchor-worthy if it has a visual presence of its own."""
    # Browsers return HTML tag names uppercase but SVG (XML-namespaced)
    # tag names lowercase. Normalize once so the inline / SVG-primitive
    # guards below match either spelling.
    tag_u = (el.tag or "").upper()
    # SVG drawing primitives never anchor — they belong to their parent
    # <svg>'s NativeSvg emit op (see slidify.svg_shapes). Their fills,
    # strokes, and transforms survive via the SVG renderer, not via a
    # standalone visual unit.
    if tag_u in _SVG_PRIMITIVE_TAGS:
        return False
    if el.is_canvas or el.is_svg or el.is_img or el.is_video:
        return True
    if el.is_table:
        return True
    # Inline by tag name AND by computed display. A `<span>` with
    # `display:block` (the `.sub` pattern in editorial layouts) anchors
    # like a div on the text-leaf branch; a `<span>` with default
    # `display:inline` is a styled run and folds.
    display = (el.display or "inline").lower()
    is_inline = (
        tag_u in _INLINE_FORMATTING_TAGS
        and display in ("inline", "contents", "ruby", "")
    )
    # Inline formatting tags only anchor when they carry real visual
    # presence (bg, border, shadow, radius, filter, clip-path, pseudo,
    # role, image-bg). A transform alone is treated as a typographic
    # detail rather than a block boundary; same for plain text.
    if not is_inline:
        if el.transform and el.transform != "none":
            return True
    if _has_pseudo(el):
        return True
    if _has_shadow(el.box_shadow):
        return True
    if _has_radius(el.border_radius):
        return True
    if _has_any_visible_border(el):
        return True
    if not _is_transparent(el.background_color):
        if parent_bg is None or parent_bg != el.background_color:
            return True
    if el.background_image and el.background_image != "none":
        return True
    if el.filter and el.filter != "none":
        return True
    if el.clip_path and el.clip_path != "none":
        return True
    if el.pptx_role:
        return True
    # Leaf text elements with their own meaningful area become anchors so
    # that two different text blocks don't merge into one NativeText frame.
    # Text containers (text + inline formatting children) count too.
    # Mixed-content elements (direct text + block children) also anchor —
    # they emit as Hybrid units so neither the parent's text leaf nor the
    # child stack gets dropped. Inline formatting tags are excluded —
    # they're styled runs, not paragraphs, and folding them keeps the
    # parent text frame whole.
    if not is_inline:
        has_text = bool(el.text and el.text.strip())
        if has_text or el.is_text_container or el.mixed_content_text:
            if el.bbox.area >= 200:
                return True
    return False


def _normalize_class_signature(cls: str) -> frozenset[str]:
    return frozenset(cls.split()) if cls else frozenset()


def _isomorphic_subtree_signature(
    el: DomElement,
    by_parent: dict[int | None, list[DomElement]],
) -> tuple:
    """Return a recursive structural signature: (tag, classes, child sigs)."""
    children = by_parent.get(el.id, [])
    return (
        el.tag,
        _normalize_class_signature(el.cls),
        tuple(_isomorphic_subtree_signature(c, by_parent) for c in children),
    )


def cluster(elements: list[DomElement]) -> list[VisualUnit]:
    """Cluster a flat element list into a list of top-level VisualUnits.

    Returns the children of an implicit root (the page body). Top-level units
    are the anchor-rooted regions of the slide.
    """
    if not elements:
        return []

    # Drop elements the walker flagged as off-canvas (sr-only spans pinned
    # at left:-9999px, width:1px height:1px tricks, or anything else
    # entirely outside the slide frame plus slack). Authors who want such
    # elements in the slide can opt back in via `data-pptx-allow-overflow`.
    # Rewrite each survivor's parent_id to the nearest non-dropped
    # ancestor so the parent_id graph stays intact.
    _all_by_id = {e.id: e for e in elements}
    _dropped_ids = {
        e.id for e in elements
        if getattr(e, "is_offcanvas", False) and not e.allow_overflow
    }
    if _dropped_ids:
        survivors: list[DomElement] = []
        for e in elements:
            if e.id in _dropped_ids:
                continue
            pid = e.parent_id
            while pid is not None and pid in _dropped_ids:
                pid = _all_by_id[pid].parent_id
            if pid != e.parent_id:
                e = e.model_copy(update={"parent_id": pid})
            survivors.append(e)
        elements = survivors
    if not elements:
        return []

    by_id: dict[int, DomElement] = {e.id: e for e in elements}
    by_parent: dict[int | None, list[DomElement]] = {}
    for e in elements:
        by_parent.setdefault(e.parent_id, []).append(e)

    # Step 1: Identify anchors. parent_bg() walks up the DOM to find the
    # nearest non-transparent ancestor background, used for the
    # transparent-vs-not heuristic in `_is_anchor`.

    def parent_bg(e: DomElement) -> str | None:
        p = e.parent_id
        while p is not None:
            pe = by_id.get(p)
            if pe is None:
                return None
            if not _is_transparent(pe.background_color):
                return pe.background_color
            p = pe.parent_id
        return None

    anchor_ids: set[int] = set()
    for e in elements:
        if _is_anchor(e, parent_bg(e)):
            anchor_ids.add(e.id)

    # Body itself is always an anchor for grouping purposes.
    if elements:
        # The walker emits the body first; conventionally id=0 has parent=None.
        roots = by_parent.get(None, [])
        for r in roots:
            anchor_ids.add(r.id)

    # Step 2: Assign each element to nearest anchor ancestor (including itself).
    nearest_anchor: dict[int, int] = {}
    for e in elements:
        cur: int | None = e.id
        while cur is not None and cur not in anchor_ids:
            parent = by_id[cur].parent_id
            cur = parent
        if cur is None:
            # Fallback to first ancestor that exists (root)
            cur = e.id
        nearest_anchor[e.id] = cur

    # Group elements by anchor
    grouped: dict[int, list[DomElement]] = {}
    for e in elements:
        grouped.setdefault(nearest_anchor[e.id], []).append(e)

    # Build provisional units
    units_by_anchor: dict[int, VisualUnit] = {}
    for anchor_id, members in grouped.items():
        anchor_el = by_id[anchor_id]
        # Envelope of all member bboxes (use anchor's bbox as starting point)
        env = anchor_el.bbox
        for m in members:
            env = env.envelope(m.bbox)
        unit = VisualUnit(
            id=f"u_{anchor_id}",
            kind=UnitKind.Generic,
            bbox=env,
            elements=members,
            anchor_element_id=anchor_id,
        )
        units_by_anchor[anchor_id] = unit

    # Step 3: Merge spatially-overlapping anchor units with shared style.
    # Apply only between siblings (same parent anchor in DOM hierarchy).
    anchor_parent: dict[int, int | None] = {}
    for aid in units_by_anchor:
        # Walk up from anchor's element to find next anchor ancestor
        p = by_id[aid].parent_id
        while p is not None and p not in anchor_ids:
            p = by_id[p].parent_id
        anchor_parent[aid] = p

    # Group anchors by parent for merge candidates
    by_anchor_parent: dict[int | None, list[int]] = {}
    for aid, pid in anchor_parent.items():
        by_anchor_parent.setdefault(pid, []).append(aid)

    merged_into: dict[int, int] = {}
    for siblings in by_anchor_parent.values():
        # Compare each pair
        for i in range(len(siblings)):
            ai = siblings[i]
            if ai in merged_into:
                continue
            for j in range(i + 1, len(siblings)):
                aj = siblings[j]
                if aj in merged_into:
                    continue
                ei = by_id[ai]
                ej = by_id[aj]
                ratio = ei.bbox.overlap_ratio(ej.bbox)
                if ratio < 0.7:
                    continue
                # Don't merge an anchor with `pptx_role` (the author has
                # explicitly tagged it — title / kicker / footer). Same
                # for elements carrying text. The merge step is for
                # visually-similar bg layers (two divs sharing surface
                # styling), not for absorbing content into decoration.
                if ei.pptx_role or ej.pptx_role:
                    continue
                if (ei.text and ei.text.strip()) or (ej.text and ej.text.strip()):
                    continue
                if ei.is_text_container or ej.is_text_container:
                    continue
                # Tables own their cells, not el.text. Merging one into a
                # full-slide plate discards those cells before editability
                # accounting even starts. Media are independent layers too.
                if any(
                    e.is_table or e.is_img or e.is_canvas or e.is_video
                    for e in (ei, ej)
                ):
                    continue
                # Don't merge text into non-text or vice versa: an SVG
                # canvas overlay covering the slide must not absorb a
                # blockquote sitting on top of it (slide-45 quote lost).
                if ei.is_svg != ej.is_svg:
                    continue
                if ei.background_color != ej.background_color:
                    continue
                if ei.border_radius != ej.border_radius:
                    continue
                # Don't merge across different bg-images — a body with a
                # radial-gradient and an overlay grid-pattern div are visually
                # *layered*, not the same unit.
                if (ei.background_image or "none") != (ej.background_image or "none"):
                    continue
                # Don't merge if either element has a filter / transform / opacity
                # — those signal a distinct visual layer.
                if (ei.filter or "none") != "none" or (ej.filter or "none") != "none":
                    continue
                if ei.opacity < 0.99 or ej.opacity < 0.99:
                    continue
                # Merge aj → ai (keep larger as canonical)
                small, big = (aj, ai) if ei.bbox.area >= ej.bbox.area else (ai, aj)
                merged_into[small] = big
                u_big = units_by_anchor[big]
                u_small = units_by_anchor[small]
                u_big.elements.extend(u_small.elements)
                u_big.bbox = u_big.bbox.envelope(u_small.bbox)
                if small == ai:
                    break  # ai was merged away; stop comparing j
        # Apply transitive merges within this group (rare but safe)
    # Drop merged anchors
    for small_id in merged_into:
        units_by_anchor.pop(small_id, None)

    # Recompute anchor_parent map after merges (skip merged ids)
    # When pid points to a merged anchor, redirect to its absorber.
    def resolve(aid: int) -> int:
        while aid in merged_into:
            aid = merged_into[aid]
        return aid

    new_parent: dict[int, int | None] = {}
    for aid in units_by_anchor:
        pid = anchor_parent[aid]
        new_parent[aid] = resolve(pid) if pid is not None else None

    # Step 4: Repeated-pattern detection (lists). Look for sibling units whose
    # underlying anchor elements share a structural signature, and tag the
    # parent as ListContainer with ListItem children.
    siblings_by_parent: dict[int | None, list[int]] = {}
    for aid, pid in new_parent.items():
        siblings_by_parent.setdefault(pid, []).append(aid)

    for pid, sibs in siblings_by_parent.items():
        if len(sibs) < 3:
            continue
        sigs: dict[tuple, list[int]] = {}
        for aid in sibs:
            sig = _isomorphic_subtree_signature(by_id[aid], by_parent)
            sigs.setdefault(sig, []).append(aid)
        # Composite-row guard: when the parent has *multiple* parallel
        # isomorphic groups (e.g., a TOC where each row is `.num/.label/.page`
        # contributes 3 distinct groups of 6), this is a structured layout,
        # not a bullet list. Tagging every cell as ListItem causes tier-1's
        # bullet recipe to fire on each cell, prepending stray "•" markers.
        # Only tag when there's exactly one repeated-pattern group.
        groups_with_3_plus = [g for g in sigs.values() if len(g) >= 3]
        if len(groups_with_3_plus) != 1:
            continue
        for _sig, group in sigs.items():
            if len(group) < 3:
                continue
            # Check size similarity (within 10%)
            ws = [by_id[a].bbox.w for a in group]
            hs = [by_id[a].bbox.h for a in group]
            if not ws or not hs:
                continue
            mean_w = sum(ws) / len(ws)
            mean_h = sum(hs) / len(hs)
            if mean_w == 0 or mean_h == 0:
                continue
            if any(abs(w - mean_w) / mean_w > 0.10 for w in ws):
                continue
            if any(abs(h - mean_h) / mean_h > 0.10 for h in hs):
                continue
            # Tag the parent (if it exists in our unit set) and the items.
            if pid is not None and pid in units_by_anchor:
                units_by_anchor[pid].kind = UnitKind.ListContainer
            for aid in group:
                u = units_by_anchor[aid]
                u.kind = UnitKind.ListItem

    # Step 5: Build DAG by attaching child units under their parent units.
    children_map: dict[int | None, list[int]] = {}
    for aid, pid in new_parent.items():
        children_map.setdefault(pid, []).append(aid)

    def build(aid: int) -> VisualUnit:
        unit = units_by_anchor[aid]
        unit.parent_id = (
            f"u_{new_parent[aid]}" if new_parent.get(aid) is not None else None
        )
        unit.children = [build(c) for c in children_map.get(aid, [])]
        return unit

    roots: list[VisualUnit] = []
    for top in children_map.get(None, []):
        roots.append(build(top))

    # Apply post-clustering kind hints
    _apply_kind_hints(roots, by_id)

    log.info(
        "units.cluster",
        n_elements=len(elements),
        n_units=len(units_by_anchor),
        n_top=len(roots),
    )
    return roots


def _apply_kind_hints(roots: list[VisualUnit], by_id: dict[int, DomElement]) -> None:
    """Promote unit kinds based on DOM hints (data-pptx-role, tags, etc.)."""

    def visit(u: VisualUnit) -> None:
        anchor = by_id.get(u.anchor_element_id) if u.anchor_element_id is not None else None
        if anchor:
            if anchor.is_canvas or anchor.is_video:
                u.kind = UnitKind.Chart if anchor.is_canvas else UnitKind.Generic
            elif anchor.is_img:
                u.kind = UnitKind.Image
            elif anchor.is_table:
                u.kind = UnitKind.Table
            elif anchor.pptx_role == "title":
                u.kind = UnitKind.Title
            elif anchor.pptx_role in ("heading", "body", "caption"):
                u.kind = UnitKind.Body
            elif u.kind == UnitKind.Generic and anchor.tag in ("H1", "H2", "H3"):
                u.kind = UnitKind.Title if anchor.tag == "H1" else UnitKind.Body
        for c in u.children:
            visit(c)

    for r in roots:
        visit(r)


def flatten(roots: list[VisualUnit]) -> list[VisualUnit]:
    """Pre-order walk of the unit DAG."""
    out: list[VisualUnit] = []

    def visit(u: VisualUnit) -> None:
        out.append(u)
        for c in u.children:
            visit(c)

    for r in roots:
        visit(r)
    return out
