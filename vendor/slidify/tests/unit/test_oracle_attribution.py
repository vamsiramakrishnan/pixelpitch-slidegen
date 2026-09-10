"""Tests for `slidify.oracle.attribute_regions_to_units` and the
`FidelityOracle.evaluate` back-compat path with no `units_per_slide` arg."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from slidify.models import (
    BoundingBox,
    Decision,
    DecisionKind,
    DomElement,
    FidelityReport,
    UnitKind,
    VisualUnit,
)
from slidify.oracle import FidelityOracle, attribute_regions_to_units

# --- helpers -----------------------------------------------------------------


def _bbox(x: float, y: float, w: float, h: float) -> BoundingBox:
    return BoundingBox(x=x, y=y, w=w, h=h)


def _unit(uid: str, bbox: BoundingBox, *, with_svg: bool = False) -> VisualUnit:
    elements: list[DomElement] = []
    if with_svg:
        elements.append(
            DomElement(
                id=hash(uid) & 0xFFFF,
                parent_id=None,
                depth=0,
                tag="svg",
                bbox=bbox,
                is_svg=True,
                svg_path_count=4,
            )
        )
    return VisualUnit(id=uid, kind=UnitKind.Generic, bbox=bbox, elements=elements)


def _decision(
    kind: DecisionKind,
    *,
    tier: str = "tier1",
    reason: str = "test",
    metadata: dict | None = None,
) -> Decision:
    return Decision(
        kind=kind,
        confidence=1.0,
        reason=reason,
        metadata=metadata or {},
        source_tier=tier,
    )


# --- region-to-unit attribution ---------------------------------------------


def test_region_inside_one_unit_attributes_to_that_unit():
    u = _unit("u_1", _bbox(0, 0, 200, 200))
    units = {u.id: u}
    decisions = {u.id: _decision(DecisionKind.NativeText, reason="simple_leaf_text")}
    region = _bbox(50, 50, 30, 30)

    rows = attribute_regions_to_units([region], units, decisions)

    assert len(rows) == 1
    row = rows[0]
    assert row.unit_id == "u_1"
    assert row.decision_kind == "NativeText"
    assert row.source_tier == "tier1"
    assert row.reason == "simple_leaf_text"
    assert row.region == region


def test_region_spanning_two_units_picks_smallest_containing():
    big = _unit("u_big", _bbox(0, 0, 1000, 1000))
    small = _unit("u_small", _bbox(100, 100, 200, 200))
    units = {big.id: big, small.id: small}
    decisions = {
        big.id: _decision(DecisionKind.NativeShape),
        small.id: _decision(DecisionKind.NativeText, tier="tier1"),
    }
    # Region fully inside both, but the smaller unit is the "more specific"
    # owner.
    region = _bbox(150, 150, 40, 40)

    rows = attribute_regions_to_units([region], units, decisions)

    assert len(rows) == 1
    assert rows[0].unit_id == "u_small"
    assert rows[0].decision_kind == "NativeText"


def test_region_with_no_unit_overlap_is_skipped_not_crashed():
    u = _unit("u_off", _bbox(0, 0, 50, 50))
    units = {u.id: u}
    decisions = {u.id: _decision(DecisionKind.NativeText)}
    # Region nowhere near any unit.
    region = _bbox(900, 900, 100, 100)

    rows = attribute_regions_to_units([region], units, decisions)

    assert rows == []


def test_attribution_skips_regions_partially_overlapping_below_threshold():
    # 50% containment threshold — 20% containment must be skipped.
    u = _unit("u_1", _bbox(0, 0, 100, 100))
    units = {u.id: u}
    decisions = {u.id: _decision(DecisionKind.NativeText)}
    region = _bbox(80, 80, 100, 100)  # only 20x20 inside (4%)

    rows = attribute_regions_to_units([region], units, decisions)

    assert rows == []


def test_attribution_handles_missing_decision_gracefully():
    u = _unit("u_orphan", _bbox(0, 0, 200, 200))
    units = {u.id: u}
    region = _bbox(50, 50, 30, 30)

    rows = attribute_regions_to_units([region], units, decisions={})

    assert len(rows) == 1
    assert rows[0].unit_id == "u_orphan"
    assert rows[0].decision_kind == "Unknown"
    assert rows[0].suspected_failure == "unknown"


# --- suspected_failure heuristic --------------------------------------------


def test_suspected_failure_native_text_tall_narrow_is_wrap_overflow():
    u = _unit("u_text", _bbox(0, 0, 800, 800))
    units = {u.id: u}
    decisions = {u.id: _decision(DecisionKind.NativeText, reason="leaf_text")}
    # Tall+narrow region => wrap_overflow.
    region = _bbox(100, 100, 30, 200)

    rows = attribute_regions_to_units([region], units, decisions)

    assert len(rows) == 1
    assert rows[0].suspected_failure == "wrap_overflow"


def test_suspected_failure_native_text_wide_short_is_font_metrics():
    u = _unit("u_text", _bbox(0, 0, 800, 800))
    units = {u.id: u}
    decisions = {u.id: _decision(DecisionKind.NativeText, reason="leaf_text")}
    # Wide+short region => font_metrics.
    region = _bbox(100, 100, 300, 20)

    rows = attribute_regions_to_units([region], units, decisions)

    assert len(rows) == 1
    assert rows[0].suspected_failure == "font_metrics"


def test_suspected_failure_native_shape_gradient_is_render_drift():
    u = _unit("u_shape", _bbox(0, 0, 800, 800))
    units = {u.id: u}
    decisions = {
        u.id: _decision(
            DecisionKind.NativeShape,
            metadata={"recipe": "linear_gradient_fill"},
        )
    }
    region = _bbox(100, 100, 200, 100)

    rows = attribute_regions_to_units([region], units, decisions)

    assert len(rows) == 1
    assert rows[0].suspected_failure == "gradient_render_drift"


def test_suspected_failure_native_shape_over_svg_unit_is_svg_path_render():
    # No NativeSvg DecisionKind exists; SVG drift surfaces via NativeShape
    # over a unit whose elements contain svg nodes.
    u = _unit("u_svg", _bbox(0, 0, 800, 800), with_svg=True)
    units = {u.id: u}
    decisions = {u.id: _decision(DecisionKind.NativeShape, metadata={})}
    region = _bbox(100, 100, 200, 100)

    rows = attribute_regions_to_units([region], units, decisions)

    assert len(rows) == 1
    assert rows[0].suspected_failure == "svg_path_render"


def test_suspected_failure_hybrid_is_raster_overlap():
    u = _unit("u_hybrid", _bbox(0, 0, 800, 800))
    units = {u.id: u}
    decisions = {u.id: _decision(DecisionKind.Hybrid)}
    region = _bbox(100, 100, 200, 100)

    rows = attribute_regions_to_units([region], units, decisions)

    assert len(rows) == 1
    assert rows[0].suspected_failure == "raster_overlap"


def test_suspected_failure_raster_is_raster_quality():
    u = _unit("u_raster", _bbox(0, 0, 800, 800))
    units = {u.id: u}
    decisions = {u.id: _decision(DecisionKind.Raster)}
    region = _bbox(100, 100, 200, 100)

    rows = attribute_regions_to_units([region], units, decisions)

    assert len(rows) == 1
    assert rows[0].suspected_failure == "raster_quality"


def test_suspected_failure_native_shape_no_gradient_no_svg_is_unknown():
    u = _unit("u_shape", _bbox(0, 0, 800, 800))
    units = {u.id: u}
    decisions = {u.id: _decision(DecisionKind.NativeShape, metadata={"recipe": "flat_fill"})}
    region = _bbox(100, 100, 200, 100)

    rows = attribute_regions_to_units([region], units, decisions)

    assert len(rows) == 1
    assert rows[0].suspected_failure == "unknown"


# --- evaluate() backward compatibility --------------------------------------


@pytest.mark.asyncio
async def test_evaluate_without_units_per_slide_returns_empty_failing_units(
    tmp_path: Path,
):
    """When `units_per_slide=None`, FidelityReports must come back with
    `failing_units=[]`, regardless of whether regions were found."""
    pptx = tmp_path / "fake.pptx"
    pptx.write_bytes(b"")  # contents irrelevant — render is mocked

    # One ground-truth PNG per slide, contents irrelevant since SSIM/OCR are
    # mocked.
    ground_truths = [b"gt0", b"gt1"]
    fake_renders = [b"r0", b"r1"]
    fake_region = BoundingBox(x=0, y=0, w=10, h=10)

    async def _fake_render(_path):
        return fake_renders

    with (
        patch("slidify.oracle.render_pptx_to_pngs", side_effect=_fake_render),
        patch("slidify.oracle.compute_ssim", return_value=0.5),
        patch(
            "slidify.oracle.compute_ocr_recall",
            return_value=(0.5, set(), set()),
        ),
        patch("slidify.oracle.find_failing_regions", return_value=[fake_region]),
    ):
        oracle = FidelityOracle()
        reports = await oracle.evaluate(pptx, ground_truths)

    assert len(reports) == 2
    for r in reports:
        assert isinstance(r, FidelityReport)
        # Regions are populated (oracle saw a fail) but no attribution is
        # done in back-compat mode.
        assert r.failing_regions == [fake_region]
        assert r.failing_units == []


@pytest.mark.asyncio
async def test_evaluate_with_units_per_slide_populates_failing_units(
    tmp_path: Path,
):
    """When `units_per_slide` is provided and the oracle reports a failing
    region, the matching unit shows up in `failing_units`."""
    pptx = tmp_path / "fake.pptx"
    pptx.write_bytes(b"")

    ground_truths = [b"gt0"]
    fake_renders = [b"r0"]
    failing_region = BoundingBox(x=10, y=10, w=20, h=200)  # tall+narrow

    u = _unit("u_42", _bbox(0, 0, 800, 800))
    units = {u.id: u}
    decisions = {u.id: _decision(DecisionKind.NativeText, reason="leaf_text")}

    async def _fake_render(_path):
        return fake_renders

    with (
        patch("slidify.oracle.render_pptx_to_pngs", side_effect=_fake_render),
        patch("slidify.oracle.compute_ssim", return_value=0.5),
        patch(
            "slidify.oracle.compute_ocr_recall",
            return_value=(0.5, set(), set()),
        ),
        patch("slidify.oracle.find_failing_regions", return_value=[failing_region]),
    ):
        oracle = FidelityOracle()
        reports = await oracle.evaluate(
            pptx,
            ground_truths,
            units_per_slide=[(units, decisions)],
        )

    assert len(reports) == 1
    r = reports[0]
    assert len(r.failing_units) == 1
    row = r.failing_units[0]
    assert row.unit_id == "u_42"
    assert row.decision_kind == "NativeText"
    assert row.suspected_failure == "wrap_overflow"
    assert row.reason == "leaf_text"
