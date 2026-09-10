"""Regression tests for oracle-driven raster fallback.

When the oracle promotes a unit to Raster (via ``_force_raster_overlapping``
or ``_force_full_raster``), every unit whose pixels would now be covered
by that raster needs to be Skip'd — otherwise descendants and absolutely-
positioned siblings of the rastered region keep emitting native shapes
on top of the raster and paint the same content twice.

The original bug was visible on slide-27 (``divergent-deck.pptx`` deck
position 21): a brutalist ``.frame`` div is a SIBLING of ``.head`` /
``.lay`` rather than their ancestor. Rastering only the ``.frame``
unit's DOM subtree left the ``.head``'s ``<h1>`` (a NativeText) drawing
on top of the rastered frame interior, producing a doubled "SATURATION
// 14 / 18" headline.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from slidify.api import (
    _SlidePlan,
    _force_full_raster,
    _force_raster_overlapping,
)
from slidify.models import (
    BoundingBox,
    Decision,
    DecisionKind,
    DomElement,
    RenderedSlide,
    UnitKind,
    VisualUnit,
)


def _bbox(x: float, y: float, w: float, h: float) -> BoundingBox:
    return BoundingBox(x=x, y=y, w=w, h=h)


def _unit(
    uid: str,
    bbox: BoundingBox,
    *,
    children: list[VisualUnit] | None = None,
) -> VisualUnit:
    return VisualUnit(
        id=uid,
        kind=UnitKind.Generic,
        bbox=bbox,
        elements=[
            DomElement(
                id=hash(uid) & 0xFFFF,
                parent_id=None,
                depth=0,
                tag="DIV",
                bbox=bbox,
                stable_selector=f"#{uid}",
            )
        ],
        children=children or [],
    )


def _make_plan(units_flat: list[VisualUnit], decisions: dict[str, Decision]) -> _SlidePlan:
    units_by_id = {u.id: u for u in units_flat}
    rendered = MagicMock(spec=RenderedSlide)
    return _SlidePlan(
        index=0,
        rendered=rendered,
        units=[u for u in units_flat if u.id == "u_root"],
        units_flat=units_flat,
        units_by_id=units_by_id,
        decisions=dict(decisions),
    )


def test_force_raster_overlapping_skips_deep_descendants():
    """Bug A (slide-27): when oracle rasters an ancestor, every descendant
    along the DOM path must be Skip'd, not just the direct children.
    Prior behaviour skipped only the direct children, leaving deeper
    NativeText shapes (e.g. an h1 nested two levels under .frame) drawing
    on top of the raster.
    """
    # u_frame (1232x672) > u_head (505x148) > u_h1 (505x42)
    h1 = _unit("u_h1", _bbox(40, 101, 505, 42))
    head = _unit("u_head", _bbox(40, 80, 505, 148), children=[h1])
    frame = _unit("u_frame", _bbox(24, 24, 1232, 672), children=[head])
    root = _unit("u_root", _bbox(0, 0, 1280, 720), children=[frame])

    decisions = {
        "u_root": Decision(kind=DecisionKind.NativeShape, source_tier="tier2"),
        "u_frame": Decision(kind=DecisionKind.NativeShape, source_tier="tier2"),
        "u_head": Decision(kind=DecisionKind.NativeShape, source_tier="tier2"),
        "u_h1": Decision(kind=DecisionKind.NativeText, source_tier="tier1"),
    }
    plan = _make_plan([root, frame, head, h1], decisions)

    # Failing region is the entire frame interior — picks u_frame as target.
    region = _bbox(24, 24, 1232, 672)
    changed = _force_raster_overlapping(plan, region)
    assert changed

    assert plan.decisions["u_frame"].kind == DecisionKind.Raster
    # Direct child + grandchild must BOTH be Skip — the previous bug only
    # touched u_head, leaving u_h1 as NativeText on top of the raster.
    assert plan.decisions["u_head"].kind == DecisionKind.Skip
    assert plan.decisions["u_h1"].kind == DecisionKind.Skip


def test_force_raster_overlapping_absorbs_contained_siblings():
    """Bug A (slide-27 sibling-overlay variant): a brutalist ``.frame``
    div is rendered as an absolutely-positioned overlay — its DOM siblings
    (``.head``, ``.lay``, ``.footer``) live SPATIALLY inside its bbox
    but are not its DOM descendants. Rastering only the DOM subtree
    leaves those siblings repainting on top of the raster.
    """
    # frame is a sibling of head and h1, all rooted under .slide.
    h1 = _unit("u_h1", _bbox(40, 101, 505, 42))
    head = _unit("u_head", _bbox(40, 80, 505, 148), children=[h1])
    frame = _unit("u_frame", _bbox(24, 24, 1232, 672))  # NO children — overlay div
    root = _unit("u_root", _bbox(0, 0, 1280, 720), children=[frame, head])

    decisions = {
        "u_root": Decision(kind=DecisionKind.NativeShape, source_tier="tier2"),
        "u_frame": Decision(kind=DecisionKind.NativeShape, source_tier="tier2"),
        "u_head": Decision(kind=DecisionKind.NativeShape, source_tier="tier2"),
        "u_h1": Decision(kind=DecisionKind.NativeText, source_tier="tier1"),
    }
    plan = _make_plan([root, frame, head, h1], decisions)

    # Failing region matches the frame's bbox (which spatially contains head + h1).
    region = _bbox(24, 24, 1232, 672)
    changed = _force_raster_overlapping(plan, region)
    assert changed

    assert plan.decisions["u_frame"].kind == DecisionKind.Raster
    # head and h1 are spatially inside the frame even though they're DOM
    # siblings — they must be Skip'd to prevent text-doubling.
    assert plan.decisions["u_head"].kind == DecisionKind.Skip
    assert plan.decisions["u_h1"].kind == DecisionKind.Skip


def test_force_raster_overlapping_does_not_absorb_outside_siblings():
    """Defensive: a sibling that sits entirely OUTSIDE the rastered
    region must keep its decision. The skip set is bbox-contained, so a
    .footer below the frame stays NativeText.
    """
    footer = _unit("u_footer", _bbox(40, 690, 1200, 20))
    h1 = _unit("u_h1", _bbox(40, 101, 505, 42))
    head = _unit("u_head", _bbox(40, 80, 505, 148), children=[h1])
    frame = _unit("u_frame", _bbox(24, 24, 1232, 660))  # ends at y=684, before footer
    root = _unit("u_root", _bbox(0, 0, 1280, 720), children=[frame, head, footer])

    decisions = {
        "u_root": Decision(kind=DecisionKind.NativeShape, source_tier="tier2"),
        "u_frame": Decision(kind=DecisionKind.NativeShape, source_tier="tier2"),
        "u_head": Decision(kind=DecisionKind.NativeShape, source_tier="tier2"),
        "u_h1": Decision(kind=DecisionKind.NativeText, source_tier="tier1"),
        "u_footer": Decision(kind=DecisionKind.NativeText, source_tier="tier1"),
    }
    plan = _make_plan([root, frame, head, h1, footer], decisions)

    region = _bbox(24, 24, 1232, 660)
    changed = _force_raster_overlapping(plan, region)
    assert changed

    assert plan.decisions["u_frame"].kind == DecisionKind.Raster
    # Footer is OUTSIDE the rastered region's y-range — must survive.
    assert plan.decisions["u_footer"].kind == DecisionKind.NativeText


def test_force_full_raster_skips_arbitrarily_deep_descendants():
    """``_force_full_raster`` previously hand-rolled two levels of recursion
    (children + grandchildren) and missed level 3+. Brutalist decks
    routinely nest 4–5 levels deep (``.slide > .frame > .head > div >
    h1``) — the deepest text element must still be Skip'd.
    """
    deepest = _unit("u_deep", _bbox(40, 100, 100, 40))
    level3 = _unit("u_l3", _bbox(40, 80, 200, 80), children=[deepest])
    level2 = _unit("u_l2", _bbox(40, 70, 400, 100), children=[level3])
    level1 = _unit("u_l1", _bbox(40, 60, 600, 120), children=[level2])
    root = _unit("u_root", _bbox(0, 0, 1280, 720), children=[level1])

    decisions = {
        "u_root": Decision(kind=DecisionKind.NativeShape, source_tier="tier2"),
        "u_l1": Decision(kind=DecisionKind.NativeShape, source_tier="tier2"),
        "u_l2": Decision(kind=DecisionKind.NativeShape, source_tier="tier2"),
        "u_l3": Decision(kind=DecisionKind.NativeShape, source_tier="tier2"),
        "u_deep": Decision(kind=DecisionKind.NativeText, source_tier="tier1"),
    }
    plan = _make_plan(
        [root, level1, level2, level3, deepest], decisions
    )

    _force_full_raster(plan)

    # Every unit at every depth must be Raster (root) or Skip (descendants).
    assert plan.decisions["u_root"].kind == DecisionKind.Raster
    assert plan.decisions["u_l1"].kind == DecisionKind.Skip
    assert plan.decisions["u_l2"].kind == DecisionKind.Skip
    assert plan.decisions["u_l3"].kind == DecisionKind.Skip
    # Level 4 — the historical bug: was left as NativeText, painted
    # over the full-slide raster.
    assert plan.decisions["u_deep"].kind == DecisionKind.Skip
