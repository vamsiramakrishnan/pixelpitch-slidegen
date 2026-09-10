"""Tier-1 routing for `clip-path` — translatable expressions defer to
native rules; everything else still rasterizes."""

from __future__ import annotations

from slidify.classifier.tier1 import (
    _has_untranslatable_clip_path,
    rule_clip_path_raster,
)
from slidify.models import BoundingBox, DecisionKind, DomElement, UnitKind, VisualUnit


def _unit(clip_path: str, w: int = 200, h: int = 200) -> VisualUnit:
    bbox = BoundingBox(x=0, y=0, w=w, h=h)
    el = DomElement(
        id=1,
        parent_id=None,
        depth=0,
        tag="div",
        bbox=bbox,
        clip_path=clip_path,
    )
    return VisualUnit(id="u", kind=UnitKind.Generic, bbox=bbox, elements=[el])


def test_inset_clip_path_defers_to_native():
    decision = rule_clip_path_raster(_unit("inset(10%)"))
    assert decision is None


def test_circle_clip_path_defers_to_native():
    decision = rule_clip_path_raster(_unit("circle(50%)"))
    assert decision is None


def test_polygon_hexagon_clip_path_defers_to_native():
    # Regular hexagon — preset_shapes.classify_polygon recognises it.
    poly = (
        "polygon(50% 0%, 100% 25%, 100% 75%, 50% 100%, 0% 75%, 0% 25%)"
    )
    decision = rule_clip_path_raster(_unit(poly))
    assert decision is None


def test_unknown_clip_path_still_rasterizes():
    decision = rule_clip_path_raster(_unit("path('M 0 0 L 1 1 Z')"))
    assert decision is not None
    assert decision.kind == DecisionKind.Raster
    assert decision.reason == "clip_path"


def test_url_mask_still_rasterizes():
    decision = rule_clip_path_raster(_unit("url(#mask)"))
    assert decision is not None
    assert decision.kind == DecisionKind.Raster


def test_no_clip_path_returns_none():
    decision = rule_clip_path_raster(_unit("none"))
    assert decision is None


def test_has_untranslatable_clip_path_helper():
    assert _has_untranslatable_clip_path(_unit("inset(5px)")) is False
    assert _has_untranslatable_clip_path(_unit("circle()")) is False
    assert _has_untranslatable_clip_path(_unit("path('M 0 0')")) is True
    assert _has_untranslatable_clip_path(_unit("none")) is False
