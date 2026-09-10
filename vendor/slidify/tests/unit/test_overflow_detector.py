"""Tests for the overflow detector + atom-aware allow-overflow inheritance.

Covers two pipeline-side smarts the atomic-seed authoring loop depends on:

  1. Descendants of a `type.echo` / `type.longshadow` / `type.marquee` /
     `motion.*` atom inherit the equivalent of
     `data-pptx-allow-overflow="true"` — overflow is part of the recipe, not
     an authoring bug.
  2. When the detector *does* report an overflow, it attaches an authoring
     hint: per-atom mapping if the element (or its nearest ancestor) carries
     a known `data-atom`, otherwise a viewport-math reminder keyed off the
     edge that was crossed.
"""

from __future__ import annotations

from slidify._overflow import detect_overflow
from slidify.models import BoundingBox, DomElement


def _el(
    eid: int,
    parent_id: int | None,
    bbox: BoundingBox,
    *,
    tag: str = "DIV",
    text: str | None = None,
    background_color: str = "rgba(0, 0, 0, 0)",
    data_atom: str = "",
    allow_overflow: bool = False,
    is_offcanvas: bool = False,
) -> DomElement:
    return DomElement(
        id=eid,
        parent_id=parent_id,
        depth=0 if parent_id is None else 1,
        tag=tag,
        bbox=bbox,
        text=text,
        background_color=background_color,
        data_atom=data_atom,
        allow_overflow=allow_overflow,
        is_offcanvas=is_offcanvas,
    )


def test_descendant_of_type_echo_is_auto_allowed():
    """Children of a `type.echo` anchor inherit allow-overflow."""
    parent = _el(1, None, BoundingBox(x=0, y=0, w=600, h=200), data_atom="type.echo")
    bleeding_span = _el(
        2,
        1,
        BoundingBox(x=-50, y=0, w=200, h=80),
        tag="SPAN",
        text="RUSH",
    )
    out = detect_overflow(0, [parent, bleeding_span], 1280, 720)
    assert out == []


def test_descendant_of_type_longshadow_is_auto_allowed():
    parent = _el(
        1, None, BoundingBox(x=0, y=0, w=900, h=400), data_atom="type.longshadow"
    )
    span = _el(
        2,
        1,
        BoundingBox(x=900, y=0, w=400, h=200),
        tag="SPAN",
        text="SOLAR",
    )  # crosses the right edge
    assert detect_overflow(0, [parent, span], 1280, 720) == []


def test_descendant_of_motion_marquee_is_auto_allowed():
    parent = _el(
        1, None, BoundingBox(x=0, y=0, w=1280, h=120), data_atom="motion.marquee"
    )
    overflowing_span = _el(
        2,
        1,
        BoundingBox(x=200, y=20, w=1500, h=80),
        tag="SPAN",
        text="SHIP · ITERATE · SHIP",
    )
    assert detect_overflow(0, [parent, overflowing_span], 1280, 720) == []


def test_descendant_of_unrelated_atom_is_still_flagged():
    """`type.gfill-2` does NOT carry the allow-overflow inheritance — a
    headline that bleeds is a real authoring bug."""
    parent = _el(1, None, BoundingBox(x=0, y=0, w=900, h=120), data_atom="type.gfill-2")
    span = _el(
        2,
        1,
        BoundingBox(x=600, y=0, w=900, h=120),
        tag="SPAN",
        text="VELOCITY.",
    )  # crosses right edge by 220 px
    out = detect_overflow(0, [parent, span], 1280, 720)
    assert len(out) == 1
    assert out[0].axis == "right"
    assert out[0].overflow_px == 220.0


def test_hint_uses_nearest_atom_when_descendant_has_no_atom():
    """A bleeding leaf inherits its hint from the nearest atom-tagged ancestor."""
    parent = _el(1, None, BoundingBox(x=0, y=0, w=1280, h=120), data_atom="data.ticker")
    inner = _el(
        2,
        1,
        BoundingBox(x=200, y=20, w=1500, h=80),
        tag="SPAN",
        text="SHIP · STAR · BUG",
    )
    out = detect_overflow(0, [parent, inner], 1280, 720)
    assert len(out) == 1
    assert "data.ticker" in out[0].hint


def test_hint_falls_back_to_viewport_math_with_no_atom():
    el = _el(
        1,
        None,
        BoundingBox(x=0, y=720, w=400, h=120),
        tag="DIV",
        background_color="rgb(255, 0, 0)",
    )  # crosses bottom by 120 px
    out = detect_overflow(0, [el], 1280, 720)
    assert len(out) == 1
    assert out[0].axis == "bottom"
    assert "720 px" in out[0].hint  # viewport-math reminder


def test_explicit_allow_overflow_short_circuits_inheritance_check():
    """`data-pptx-allow-overflow="true"` on the leaf itself is honored — no
    parent walk needed."""
    el = _el(
        1,
        None,
        BoundingBox(x=-100, y=0, w=300, h=120),
        tag="DIV",
        background_color="rgb(0, 0, 255)",
        allow_overflow=True,
    )
    assert detect_overflow(0, [el], 1280, 720) == []


def test_offcanvas_semantic_text_is_not_overflow():
    el = _el(
        1,
        None,
        BoundingBox(x=-9999, y=700, w=600, h=20),
        tag="SPAN",
        text="semantic title for metadata",
        is_offcanvas=True,
    )
    assert detect_overflow(0, [el], 1280, 720) == []
