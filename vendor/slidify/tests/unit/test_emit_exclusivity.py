"""Tests for the emit-pathway exclusivity audit (slidify.promotion).

The audit flags absorbing-parent + descendant-emit overlaps — the structural
fingerprint of visual duplication in produced PPTX files. The legitimate
Phase-A hybrid case (parent anchor carries ``mixed_content_text``) is
explicitly skipped.
"""

from __future__ import annotations

from slidify.models import (
    BoundingBox,
    Decision,
    DecisionKind,
    DomElement,
    EmitOp,
    ExclusivityViolation,
    UnitKind,
    VisualUnit,
)
from slidify.promotion import audit_emit_exclusivity


def _bbox(x: float, y: float, w: float, h: float) -> BoundingBox:
    return BoundingBox(x=x, y=y, w=w, h=h)


def _el(
    eid: int,
    parent: int | None,
    depth: int,
    tag: str,
    *,
    bbox: tuple[float, float, float, float] = (0, 0, 100, 100),
    text: str | None = None,
    mixed_content_text: str | None = None,
) -> DomElement:
    x, y, w, h = bbox
    return DomElement(
        id=eid,
        parent_id=parent,
        depth=depth,
        tag=tag,
        bbox=_bbox(x, y, w, h),
        text=text,
        mixed_content_text=mixed_content_text,
    )


def _unit(
    uid: str,
    bbox: tuple[float, float, float, float],
    *,
    elements: list[DomElement] | None = None,
    children: list[VisualUnit] | None = None,
) -> VisualUnit:
    x, y, w, h = bbox
    return VisualUnit(
        id=uid,
        kind=UnitKind.Generic,
        bbox=_bbox(x, y, w, h),
        elements=elements or [],
        children=children or [],
    )


def _op(uid: str, kind: DecisionKind, bbox: tuple[float, float, float, float]) -> EmitOp:
    x, y, w, h = bbox
    return EmitOp(
        unit_id=uid,
        decision=Decision(kind=kind, confidence=1.0, source_tier="tier1"),
        z_order=0,
        bbox=_bbox(x, y, w, h),
    )


def test_clean_emit_no_violations():
    """All units classified as NativeShape (non-absorbing) — no violations."""
    child = _unit(
        "c1", (10, 10, 80, 80),
        elements=[_el(2, 1, 1, "DIV", bbox=(10, 10, 80, 80))],
    )
    parent = _unit(
        "p1", (0, 0, 100, 100),
        elements=[_el(1, None, 0, "DIV", bbox=(0, 0, 100, 100))],
        children=[child],
    )
    decisions = {
        "p1": Decision(kind=DecisionKind.NativeShape, confidence=1.0),
        "c1": Decision(kind=DecisionKind.NativeShape, confidence=1.0),
    }
    ops = [
        _op("p1", DecisionKind.NativeShape, (0, 0, 100, 100)),
        _op("c1", DecisionKind.NativeShape, (10, 10, 80, 80)),
    ]
    assert audit_emit_exclusivity([parent], decisions, ops) == []


def test_native_text_with_descendant_op_violates():
    """NativeText parent + descendant emit op → one violation."""
    child = _unit(
        "c1", (10, 10, 80, 80),
        elements=[_el(2, 1, 1, "SPAN", bbox=(10, 10, 80, 80), text="child")],
    )
    parent = _unit(
        "p1", (0, 0, 100, 100),
        elements=[_el(1, None, 0, "DIV", bbox=(0, 0, 100, 100), text="parent")],
        children=[child],
    )
    decisions = {
        "p1": Decision(kind=DecisionKind.NativeText, confidence=1.0),
        "c1": Decision(kind=DecisionKind.NativeText, confidence=1.0),
    }
    ops = [
        _op("p1", DecisionKind.NativeText, (0, 0, 100, 100)),
        _op("c1", DecisionKind.NativeText, (10, 10, 80, 80)),
    ]
    violations = audit_emit_exclusivity([parent], decisions, ops, slide_index=3)
    assert len(violations) == 1
    v = violations[0]
    assert v.parent_unit_id == "p1"
    assert v.descendant_unit_id == "c1"
    assert v.parent_kind == "native_text"
    assert v.descendant_kind == "native_text"
    assert v.slide_index == 3
    assert "absorbing parent" in v.reason
    assert v.overlap_ratio >= 0.5


def test_native_text_with_mixed_content_descendant_ok():
    """Same shape, but parent has mixed_content_text — no violation (Phase A)."""
    child = _unit(
        "c1", (10, 10, 80, 80),
        elements=[_el(2, 1, 1, "SPAN", bbox=(10, 10, 80, 80), text="child")],
    )
    parent = _unit(
        "p1", (0, 0, 100, 100),
        elements=[
            _el(
                1, None, 0, "DIV",
                bbox=(0, 0, 100, 100),
                text="parent",
                mixed_content_text="parent-text-here",
            )
        ],
        children=[child],
    )
    decisions = {
        "p1": Decision(kind=DecisionKind.NativeText, confidence=1.0),
        "c1": Decision(kind=DecisionKind.NativeText, confidence=1.0),
    }
    ops = [
        _op("p1", DecisionKind.NativeText, (0, 0, 100, 100)),
        _op("c1", DecisionKind.NativeText, (10, 10, 80, 80)),
    ]
    assert audit_emit_exclusivity([parent], decisions, ops) == []


def test_native_picture_with_descendant_violates():
    """NativePicture parent + descendant emit → violation."""
    child = _unit(
        "c1", (10, 10, 80, 80),
        elements=[_el(2, 1, 1, "DIV", bbox=(10, 10, 80, 80))],
    )
    parent = _unit(
        "p1", (0, 0, 100, 100),
        elements=[_el(1, None, 0, "IMG", bbox=(0, 0, 100, 100))],
        children=[child],
    )
    decisions = {
        "p1": Decision(kind=DecisionKind.NativePicture, confidence=1.0),
        "c1": Decision(kind=DecisionKind.NativeShape, confidence=1.0),
    }
    ops = [
        _op("p1", DecisionKind.NativePicture, (0, 0, 100, 100)),
        _op("c1", DecisionKind.NativeShape, (10, 10, 80, 80)),
    ]
    violations = audit_emit_exclusivity([parent], decisions, ops)
    assert len(violations) == 1
    assert violations[0].parent_kind == "native_picture"
    assert "absorbing parent" in violations[0].reason


def test_low_overlap_descendant_skipped():
    """Descendant mostly outside parent → overlap < 0.5 → no violation."""
    # Parent 100x100 at origin; child sits at (200, 200) — completely outside.
    child = _unit(
        "c1", (200, 200, 80, 80),
        elements=[_el(2, 1, 1, "DIV", bbox=(200, 200, 80, 80))],
    )
    parent = _unit(
        "p1", (0, 0, 100, 100),
        elements=[_el(1, None, 0, "DIV", bbox=(0, 0, 100, 100), text="parent")],
        children=[child],
    )
    decisions = {
        "p1": Decision(kind=DecisionKind.NativeText, confidence=1.0),
        "c1": Decision(kind=DecisionKind.NativeText, confidence=1.0),
    }
    ops = [
        _op("p1", DecisionKind.NativeText, (0, 0, 100, 100)),
        _op("c1", DecisionKind.NativeText, (200, 200, 80, 80)),
    ]
    assert audit_emit_exclusivity([parent], decisions, ops) == []


def test_violation_serializes_through_model_dump_json():
    """ExclusivityViolation round-trips through Pydantic JSON serialization."""
    v = ExclusivityViolation(
        parent_unit_id="p1",
        parent_kind="native_text",
        parent_bbox_w=100.0,
        parent_bbox_h=100.0,
        descendant_unit_id="c1",
        descendant_kind="native_text",
        descendant_bbox_w=80.0,
        descendant_bbox_h=80.0,
        overlap_ratio=0.64,
        reason="absorbing parent + descendant emit; visual duplicate likely",
        slide_index=2,
    )
    js = v.model_dump_json()
    again = ExclusivityViolation.model_validate_json(js)
    assert again == v
