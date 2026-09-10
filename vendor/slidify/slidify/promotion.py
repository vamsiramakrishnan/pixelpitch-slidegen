"""Promotion engine.

Walks the VisualUnit DAG bottom-up. Resolves cases where a parent and its
children disagree, applying the rules in spec §4.8.

The promotion engine is biased toward *surgical hybrid* — when a parent has
decoration (bg image, pseudo-element) AND any child is native, we keep the
parent as a hybrid background (raster the decoration only) and let the
children emit on top. The cascade-rastering of earlier versions is reserved
for the corner case where every child is raster anyway.
"""

from __future__ import annotations

import structlog

from slidify.colors import parse_color
from slidify.geom import parse_px
from slidify.gradients import parse_gradient
from slidify.models import (
    Decision,
    DecisionKind,
    EmitOp,
    ExclusivityViolation,
    VisualUnit,
)
from slidify.shadows import is_translatable_shadow

log = structlog.get_logger(__name__)


_RASTER_KINDS = {DecisionKind.Raster, DecisionKind.Hybrid}
_NATIVE_KINDS = {
    DecisionKind.NativeText,
    DecisionKind.NativeShape,
    DecisionKind.NativeBullet,
    DecisionKind.NativePicture,
    DecisionKind.NativeSvg,
    DecisionKind.NativeTable,
}


def _has_visual_presence(unit: VisualUnit) -> bool:
    elems = unit.all_elements()
    if not elems:
        return False
    a = elems[0]
    if a.background_color and a.background_color not in ("rgba(0, 0, 0, 0)", "transparent", ""):
        return True
    if a.background_image and a.background_image != "none":
        return True
    if a.box_shadow and a.box_shadow != "none":
        return True
    if a.transform and a.transform != "none":
        return True
    if a.has_before or a.has_after:
        return True
    if parse_px(a.border_radius) > 0:
        return True
    return False


def _has_bg_image_or_pseudo(unit: VisualUnit) -> bool:
    elems = unit.all_elements()
    if not elems:
        return False
    a = elems[0]
    if a.background_image and a.background_image != "none":
        return True
    if a.has_before and a.before_content and "url(" in a.before_content:
        return True
    if a.has_after and a.after_content and "url(" in a.after_content:
        return True
    return False


def _native_decoration_only(unit: VisualUnit) -> bool:
    """True iff the unit's anchor has *only* decoration we can render natively
    (gradient, translatable shadow, solid bg, border, radius). No raster-required
    layers like url() backgrounds, pseudo-elements, transforms, filters, clip-paths.
    """
    if not unit.elements:
        return False
    a = unit.elements[0]
    # SVG anchors must NEVER be promoted to NativeShape via this rule —
    # an SVG container has child primitives (rect/path/text/etc) that
    # are NOT regular DOM units, so they wouldn't emit independently.
    # Promotion to NativeShape paints an empty box; the child primitives
    # then vanish (slide-05 tile map: 51 colored state-rects disappeared
    # because the SVG container got promoted to a fill-less NativeShape).
    # Let SVGs reach the dedicated NativeSvg / Raster classifier rules.
    if a.is_svg:
        return False
    if a.has_before or a.has_after:
        return False
    if a.transform and a.transform != "none":
        return False
    if a.filter and a.filter != "none":
        return False
    if a.clip_path and a.clip_path != "none":
        from slidify.preset_shapes import clip_path_to_preset

        if clip_path_to_preset(a.clip_path, a.bbox) is None:
            return False
    if a.background_image and a.background_image != "none":
        if "url(" in a.background_image:
            return False
        if not parse_gradient(a.background_image):
            return False
    if (
        a.box_shadow
        and a.box_shadow != "none"
        and not is_translatable_shadow(a.box_shadow)
    ):
        return False
    return True


def _has_low_opacity(unit: VisualUnit) -> bool:
    """True only if the *anchor* itself has opacity < 1.

    A non-anchor descendant's opacity (e.g., a faint overlay div) shouldn't
    cascade-raster the entire unit — it's a separate layer that emits as its
    own unit when classified.
    """
    if not unit.elements:
        return False
    return unit.elements[0].opacity < 0.99


def _has_translucent_background(unit: VisualUnit) -> bool:
    if not unit.elements:
        return False
    bg = parse_color(unit.elements[0].background_color)
    return bg is not None and bg[1] < 0.98


def _unit_has_text(unit: VisualUnit) -> bool:
    for el in unit.all_elements():
        if (el.text and el.text.strip()) or (
            getattr(el, "mixed_content_text", None) or ""
        ).strip():
            return True
        if el.runs and any(r.text.strip() for r in el.runs if not r.is_break):
            return True
    return False


def _should_raster_backplate(unit: VisualUnit) -> bool:
    """True for decorated containers whose CSS surface is higher fidelity as
    a textless raster layer than as approximate PPTX primitives.

    These units keep descendants native via Hybrid emission; only the visual
    backplate comes from the browser's no-text render.
    """
    if not unit.elements or not unit.children:
        return False
    a = unit.elements[0]
    if a.is_svg or a.is_img or a.is_canvas or a.is_video:
        return False
    if not _has_visual_presence(unit):
        return False

    has_gradient = bool(a.background_image and a.background_image != "none")
    has_shadow = bool(a.box_shadow and a.box_shadow != "none")
    has_radius = parse_px(a.border_radius) >= 8
    has_decorate_hint = bool((a.decorate_hint or "").strip())
    has_translucency = _has_translucent_background(unit)
    has_border = bool(a.border and a.border != "none")

    return (
        has_decorate_hint
        or (has_gradient and (has_shadow or has_radius or has_translucency))
        or (has_shadow and has_radius and (has_translucency or has_border))
    )


def _has_full_cover_native_child(unit: VisualUnit, decisions: dict[str, Decision]) -> bool:
    """True iff at least one direct child unit has a native-shape decision and
    its bbox covers ≥98% of this unit's bbox area. Such a child fully occludes
    the parent's fill — the parent's own emit is wasted.
    """
    parent_area = unit.bbox.w * unit.bbox.h
    if parent_area <= 0:
        return False
    for c in unit.children:
        d = decisions.get(c.id)
        if d is None or d.kind not in (DecisionKind.NativeShape, DecisionKind.Hybrid):
            continue
        # Child must contain ≥98% of the parent.
        intersect = unit.bbox.intersect_area(c.bbox)
        coverage = intersect / parent_area
        if coverage >= 0.98:
            # And the child's anchor must have an opaque-ish fill (solid
            # color or gradient with no fully-transparent stops covering it).
            if c.elements:
                ca = c.elements[0]
                # Quick proxy: child has opacity == 1 and either a non-trivial
                # bg-color or a translatable gradient bg-image.
                if ca.opacity >= 0.99 and (
                    (ca.background_color and ca.background_color != "rgba(0, 0, 0, 0)")
                    or (ca.background_image and ca.background_image != "none")
                ):
                    return True
    return False


def promote(
    roots: list[VisualUnit], decisions: dict[str, Decision]
) -> dict[str, Decision]:
    """Bottom-up walk; mutates a copy of decisions and returns it."""
    out = dict(decisions)

    def visit(unit: VisualUnit) -> None:
        for child in unit.children:
            visit(child)

        my_decision = out.get(unit.id)
        if not unit.children:
            return

        child_decisions = [out.get(c.id) for c in unit.children]
        child_kinds = [d.kind for d in child_decisions if d is not None]
        if not child_kinds:
            return

        all_raster = all(k in _RASTER_KINDS for k in child_kinds)
        any_native = any(k in _NATIVE_KINDS for k in child_kinds)

        # Rule 0: occlusion. If any opaque NativeShape child covers ≥98% of
        # this unit's bbox, the parent's own fill is invisible — skip the
        # parent emit. This eliminates the "body bg + .slide div bg" double-
        # layer that wastes a shape and confuses LibreOffice's z-order.
        if (
            my_decision is not None
            and my_decision.kind == DecisionKind.NativeShape
            and _has_full_cover_native_child(unit, out)
        ):
            out[unit.id] = Decision(
                kind=DecisionKind.Skip,
                confidence=1.0,
                reason="occluded_by_full_cover_child",
                source_tier="promotion",
            )
            return

        # Edge case: opacity < 1 on a unit with children → rasterize whole unit.
        if _has_low_opacity(unit) and unit.children:
            out[unit.id] = Decision(
                kind=DecisionKind.Raster,
                confidence=1.0,
                reason="opacity<1 with children",
                source_tier="promotion",
            )
            # Skip the entire descendant subtree, not just direct children —
            # otherwise grand-descendants' native_text shapes still emit on
            # top of the raster.
            stack = list(unit.children)
            while stack:
                c = stack.pop()
                if out.get(c.id) and out[c.id].kind != DecisionKind.Skip:
                    out[c.id] = Decision(
                        kind=DecisionKind.Skip,
                        confidence=1.0,
                        reason="absorbed by raster parent (opacity)",
                        source_tier="promotion",
                    )
                stack.extend(c.children)
            return

        # Complex decorated containers: rasterize only the textless backplate
        # and keep child text/icons editable. Native PPTX gradients/shadows do
        # not match CSS glassmorphism well enough for these surfaces.
        if (
            my_decision is not None
            and my_decision.kind in (DecisionKind.NativeShape, DecisionKind.Hybrid)
            and _should_raster_backplate(unit)
        ):
            out[unit.id] = Decision(
                kind=DecisionKind.Hybrid,
                confidence=max(my_decision.confidence, 0.9),
                reason="raster_backplate",
                metadata={**my_decision.metadata, "raster_backplate": True},
                source_tier="promotion",
            )
            stack = list(unit.children)
            while stack:
                c = stack.pop()
                d = out.get(c.id)
                if (
                    d is not None
                    and d.kind in (DecisionKind.NativeShape, DecisionKind.NativeSvg)
                    and not _unit_has_text(c)
                ):
                    out[c.id] = Decision(
                        kind=DecisionKind.Skip,
                        confidence=1.0,
                        reason="absorbed by raster backplate",
                        source_tier="promotion",
                    )
                stack.extend(c.children)
            return

        # Rule N0: parent's own decoration is fully native-translatable
        # (gradient/shadow/solid bg). Promote the parent to NativeShape and let
        # children emit independently — no rastering anywhere.
        if _native_decoration_only(unit):
            if my_decision is None or my_decision.kind in (
                DecisionKind.Skip,
                DecisionKind.Raster,
                DecisionKind.Hybrid,
            ):
                out[unit.id] = Decision(
                    kind=DecisionKind.NativeShape,
                    confidence=0.85,
                    reason="native_decoration_promoted",
                    source_tier="promotion",
                )
            return

        # Rule N1: parent has un-translatable decoration (url() bg or pseudo)
        # but at least one child is native → emit hybrid (raster crop of
        # the decoration layer + native children on top). Surgical hybrid.
        if _has_bg_image_or_pseudo(unit) and any_native:
            out[unit.id] = Decision(
                kind=DecisionKind.Hybrid,
                confidence=0.9,
                reason="surgical_hybrid",
                source_tier="promotion",
            )
            return

        # Rule 1: All children raster + parent has visual presence and no
        # native-translatable decoration we could spare → fully raster.
        if all_raster and _has_visual_presence(unit):
            out[unit.id] = Decision(
                kind=DecisionKind.Raster,
                confidence=1.0,
                reason="all_children_raster_with_presence",
                source_tier="promotion",
            )
            # Skip the entire descendant subtree to keep grand-descendants'
            # native_text shapes from emitting over the raster.
            stack = list(unit.children)
            while stack:
                c = stack.pop()
                out[c.id] = Decision(
                    kind=DecisionKind.Skip,
                    confidence=1.0,
                    reason="absorbed by raster parent",
                    source_tier="promotion",
                )
                stack.extend(c.children)
            return

        # Rule 2 / 4: parent is plain wrapper — keep children's decisions.
        # If parent has no decision, give it a transparent shape so we don't
        # lose its structural slot.
        if my_decision is None:
            out[unit.id] = Decision(
                kind=DecisionKind.Skip,
                confidence=1.0,
                reason="plain_wrapper",
                source_tier="promotion",
            )

    for r in roots:
        visit(r)

    return out


def _has_mixed_content_anchor(unit: VisualUnit) -> bool:
    """True iff the unit's anchor element carries a non-empty
    ``mixed_content_text``. Used by ``to_emit_ops`` to keep child units
    visible when the parent emits as a Hybrid text-leaf alongside blocks.
    """
    if not unit.elements:
        return False
    anchor = unit.elements[0]
    return bool((getattr(anchor, "mixed_content_text", None) or "").strip())


def to_emit_ops(
    roots: list[VisualUnit], decisions: dict[str, Decision]
) -> list[EmitOp]:
    """Linearize the unit DAG to a list of EmitOps in z-order (bottom→top).

    Default z-order is DOM pre-order (depth-first, parent before children).
    """
    ops: list[EmitOp] = []
    counter = [0]

    def visit(unit: VisualUnit) -> None:
        decision = decisions.get(unit.id)
        if decision is None or decision.kind == DecisionKind.Skip:
            for c in unit.children:
                visit(c)
            return

        # Hybrid: emit decoration bg first, then children natively. We keep
        # the decision kind as Hybrid so the emitter dispatches through the
        # surgical-hybrid path (native gradient → no_text crop → fallback) and
        # so native_area_ratio counts the slot as half-native rather than as
        # a plain raster.
        if decision.kind == DecisionKind.Hybrid:
            ops.append(
                EmitOp(
                    unit_id=unit.id,
                    decision=decision,
                    z_order=counter[0],
                    bbox=unit.bbox,
                    payload={"hybrid_role": "background"},
                )
            )
            counter[0] += 1
            for c in unit.children:
                visit(c)
            return

        # Raster absorbs children (they should have been Skip'd by promotion).
        if decision.kind == DecisionKind.Raster:
            ops.append(
                EmitOp(
                    unit_id=unit.id,
                    decision=decision,
                    z_order=counter[0],
                    bbox=unit.bbox,
                    payload={},
                )
            )
            counter[0] += 1
            return

        # Native parent: emit self, then children. NativeText / NativeBullet /
        # NativePicture / NativeSvg normally absorb their region — children
        # would just stack a duplicate on top. Exception: when the parent's
        # own text comes from a `mixed_content_text` capture (parent has
        # direct text alongside block descendants — the editorial
        # `<div class="meta-value">parent text<span class="sub">child</span></div>`
        # pattern), the children are *separate units* with their own text
        # at distinct bboxes, so the absorb-children rule would silently
        # drop them. Visit children in that case.
        ops.append(
            EmitOp(
                unit_id=unit.id,
                decision=decision,
                z_order=counter[0],
                bbox=unit.bbox,
                payload={},
            )
        )
        counter[0] += 1
        absorbing = decision.kind in (
            DecisionKind.NativeText,
            DecisionKind.NativeBullet,
            DecisionKind.NativePicture,
            DecisionKind.NativeSvg,
        )
        if absorbing and not _has_mixed_content_anchor(unit):
            return
        for c in unit.children:
            visit(c)

    for r in roots:
        visit(r)
    return ops


# ---------------------------------------------------------------------------
# Emit-pathway exclusivity audit
# ---------------------------------------------------------------------------

# Kinds whose own frame visually absorbs the parent region — when one of
# these emits AND a descendant unit also emits with overlapping bbox, the
# .pptx ends up with stacked shapes covering the same pixels. Mirrors the
# absorbing set used inside ``to_emit_ops``; ``NativeTable`` is added here
# because a table cell is just as much an absorbing surface as a text
# frame.
_ABSORBING_KINDS = {
    DecisionKind.NativeText,
    DecisionKind.NativeBullet,
    DecisionKind.NativePicture,
    DecisionKind.NativeSvg,
    DecisionKind.NativeTable,
}

# Min descendant/parent overlap before a violation is logged. Below this
# threshold the descendant is mostly outside the parent — could happen for
# off-bbox decoration shifted past the parent edge, no real visual
# duplicate. Keeps the audit's noise floor low.
_MIN_OVERLAP_RATIO = 0.5


def audit_emit_exclusivity(
    roots: list[VisualUnit],
    decisions: dict[str, Decision],
    ops: list[EmitOp],
    *,
    slide_index: int = 0,
) -> list[ExclusivityViolation]:
    """Walk emit ops; flag absorbing-parent + descendant-emit overlaps.

    Returns the empty list when emit pathways are clean. The legitimate
    Phase-A hybrid case (parent anchor carries ``mixed_content_text``) is
    explicitly skipped — that pathway is intended.
    """
    from slidify.units import flatten

    flat = flatten(roots)
    unit_by_id: dict[str, VisualUnit] = {u.id: u for u in flat}
    op_by_unit_id: dict[str, EmitOp] = {o.unit_id: o for o in ops}

    out: list[ExclusivityViolation] = []
    for op in ops:
        if op.decision.kind not in _ABSORBING_KINDS:
            continue
        parent = unit_by_id.get(op.unit_id)
        if parent is None:
            continue
        if _has_mixed_content_anchor(parent):
            continue  # Phase-A legitimate hybrid emit
        # Walk descendants iteratively (skip the parent itself).
        stack = list(parent.children)
        while stack:
            d = stack.pop()
            stack.extend(d.children)
            d_op = op_by_unit_id.get(d.id)
            if d_op is None:
                continue
            overlap = d.bbox.overlap_ratio(parent.bbox)
            if overlap < _MIN_OVERLAP_RATIO:
                continue
            out.append(
                ExclusivityViolation(
                    parent_unit_id=parent.id,
                    parent_kind=op.decision.kind.value,
                    parent_bbox_w=parent.bbox.w,
                    parent_bbox_h=parent.bbox.h,
                    descendant_unit_id=d.id,
                    descendant_kind=d_op.decision.kind.value,
                    descendant_bbox_w=d.bbox.w,
                    descendant_bbox_h=d.bbox.h,
                    overlap_ratio=overlap,
                    reason="absorbing parent + descendant emit; visual duplicate likely",
                    slide_index=slide_index,
                )
            )
    return out
