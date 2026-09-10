"""Tests for slidify.unit_coverage (DOM → unit coverage oracle).

The oracle reports DOM elements with text content whose region is not
covered by any produced VisualUnit's bbox. The dual of the harvester:
harvester finds unrecognised shapes; this finds dropped content.
"""

from __future__ import annotations

from slidify.models import (
    BoundingBox,
    CoverageGap,
    DomElement,
    UnitKind,
    VisualUnit,
)
from slidify.unit_coverage import find_coverage_gaps


def _el(
    eid: int,
    parent: int | None,
    depth: int,
    tag: str,
    *,
    bbox: tuple[float, float, float, float] = (0, 0, 100, 100),
    text: str | None = None,
    cls: str = "",
    is_svg: bool = False,
    display: str = "inline",
    is_offcanvas: bool = False,
) -> DomElement:
    x, y, w, h = bbox
    return DomElement(
        id=eid,
        parent_id=parent,
        depth=depth,
        tag=tag,
        cls=cls,
        bbox=BoundingBox(x=x, y=y, w=w, h=h),
        text=text,
        is_svg=is_svg,
        display=display,
        is_offcanvas=is_offcanvas,
        stable_selector=f"#e{eid}",
    )


def _unit(uid: str, bbox: tuple[float, float, float, float]) -> VisualUnit:
    x, y, w, h = bbox
    return VisualUnit(
        id=uid,
        kind=UnitKind.Generic,
        bbox=BoundingBox(x=x, y=y, w=w, h=h),
    )


def test_coverage_gap_model():
    """CoverageGap round-trips through model_dump_json."""
    gap = CoverageGap(
        slide_index=0,
        element_id=42,
        tag="DIV",
        cls="spec-row",
        bbox_x=100.0,
        bbox_y=100.0,
        bbox_w=200.0,
        bbox_h=30.0,
        sample_text="Hello world",
        overlap_ratio=0.0,
        reason="no unit anchored this subtree",
        stable_selector="#e42",
    )
    js = gap.model_dump_json()
    again = CoverageGap.model_validate_json(js)
    assert again == gap


def test_no_gaps_when_all_text_owned():
    """When a unit covers every text element's bbox, no gaps emit."""
    body = _el(0, None, 0, "BODY", bbox=(0, 0, 1280, 720))
    title = _el(
        1, 0, 1, "H1",
        bbox=(40, 40, 600, 80),
        text="Hello world how are you",
    )
    para = _el(
        2, 0, 1, "P",
        bbox=(40, 140, 600, 60),
        text="A paragraph of body copy",
    )
    # One unit big enough to cover both text elements.
    roots = [_unit("u1", (0, 0, 1280, 720))]
    gaps = find_coverage_gaps([body, title, para], roots)
    assert gaps == []


def test_dropped_subtree_surfaces_as_gap():
    """An orphan text element with no covering unit shows up as a gap."""
    body = _el(0, None, 0, "BODY", bbox=(0, 0, 1280, 720))
    orphan = _el(
        1, 0, 1, "DIV",
        bbox=(100, 100, 200, 30),
        text="this should be in some unit",
        cls="spec-row",
    )
    # Unit far away from orphan's bbox.
    roots = [_unit("u1", (500, 500, 100, 100))]
    gaps = find_coverage_gaps([body, orphan], roots, slide_index=7)
    assert len(gaps) == 1
    g = gaps[0]
    assert g.element_id == 1
    assert g.tag == "DIV"
    assert g.cls == "spec-row"
    assert g.slide_index == 7
    assert g.overlap_ratio == 0.0
    assert g.reason == "no unit anchored this subtree"
    assert "this should be" in g.sample_text


def test_svg_descendants_filtered():
    """An SVG primitive (or descendant of <svg>) does NOT report as a gap.

    These elements are emitted by the parent <svg>'s NativeSvg op, so the
    coverage oracle would be a false positive on them.
    """
    body = _el(0, None, 0, "BODY", bbox=(0, 0, 1280, 720))
    svg = _el(1, 0, 1, "SVG", bbox=(100, 100, 200, 200), is_svg=True)
    # Direct primitive tag — should be filtered by tag alone.
    text_node = _el(
        2, 1, 2, "text",
        bbox=(110, 110, 100, 30),
        text="some svg label here",
    )
    # Wrapped descendant — filtered via ancestor walk (parent has is_svg=True).
    g_wrapper = _el(3, 1, 2, "g", bbox=(120, 140, 100, 30))
    deep_text = _el(
        4, 3, 3, "tspan",
        bbox=(130, 150, 80, 20),
        text="deep svg label",
    )
    # No covering units.
    roots: list[VisualUnit] = [_unit("u1", (900, 900, 50, 50))]
    gaps = find_coverage_gaps([body, svg, text_node, g_wrapper, deep_text], roots)
    reported_ids = {g.element_id for g in gaps}
    assert 2 not in reported_ids
    assert 4 not in reported_ids


def test_partial_overlap_above_threshold_not_a_gap():
    """An element 50% overlapping a unit is considered owned."""
    body = _el(0, None, 0, "BODY", bbox=(0, 0, 1280, 720))
    el = _el(
        1, 0, 1, "DIV",
        bbox=(100, 100, 200, 30),
        text="partly covered text content",
    )
    # Unit covers exactly half of el's bbox horizontally → 50% overlap.
    roots = [_unit("u1", (100, 100, 100, 30))]
    gaps = find_coverage_gaps([body, el], roots)
    assert gaps == []


def test_display_contents_reason():
    """display:contents elements get a more specific reason string."""
    body = _el(0, None, 0, "BODY", bbox=(0, 0, 1280, 720))
    el = _el(
        1, 0, 1, "DIV",
        bbox=(100, 100, 200, 30),
        text="text under display contents",
        display="contents",
    )
    roots = [_unit("u1", (900, 900, 50, 50))]
    gaps = find_coverage_gaps([body, el], roots)
    assert len(gaps) == 1
    assert gaps[0].reason == "display:contents; parent did not anchor"


def test_short_text_filtered():
    """Elements with very short text don't emit a gap."""
    body = _el(0, None, 0, "BODY", bbox=(0, 0, 1280, 720))
    el = _el(1, 0, 1, "SPAN", bbox=(100, 100, 200, 30), text="x")
    roots: list[VisualUnit] = [_unit("u1", (900, 900, 50, 50))]
    gaps = find_coverage_gaps([body, el], roots)
    assert gaps == []


def test_small_bbox_filtered():
    """Elements with very small bbox area don't emit a gap."""
    body = _el(0, None, 0, "BODY", bbox=(0, 0, 1280, 720))
    # area = 5 * 5 = 25, well below the default 200 threshold.
    el = _el(
        1, 0, 1, "SPAN",
        bbox=(100, 100, 5, 5),
        text="some long enough text",
    )
    roots: list[VisualUnit] = [_unit("u1", (900, 900, 50, 50))]
    gaps = find_coverage_gaps([body, el], roots)
    assert gaps == []


def test_offcanvas_semantic_text_filtered():
    body = _el(0, None, 0, "BODY", bbox=(0, 0, 1280, 720))
    el = _el(
        1,
        0,
        1,
        "SPAN",
        bbox=(-9999, 700, 600, 24),
        text="The infrastructure for AI-native product teams",
        is_offcanvas=True,
    )
    roots: list[VisualUnit] = [_unit("u1", (900, 900, 50, 50))]
    assert find_coverage_gaps([body, el], roots) == []
