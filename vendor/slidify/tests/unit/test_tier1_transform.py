from __future__ import annotations

from slidify.classifier.tier1 import classify_tier1, rule_3d_transform_raster
from slidify.models import BoundingBox, DecisionKind, DomElement, UnitKind, VisualUnit


def _unit(transform: str, *, text: str = "Native glyph") -> VisualUnit:
    bbox = BoundingBox(x=40, y=40, w=120, h=40)
    el = DomElement(
        id=1,
        parent_id=None,
        depth=0,
        tag="SPAN",
        bbox=bbox,
        text=text,
        display="inline-block",
        transform=transform,
        font_size="24px",
    )
    return VisualUnit(id="u", kind=UnitKind.Generic, bbox=bbox, elements=[el])


def test_2d_rotate_stays_native_text():
    decision = classify_tier1(_unit("matrix(0, 1, -1, 0, 0, 0)"))
    assert decision is not None
    assert decision.kind == DecisionKind.NativeText


def test_unsupported_transform_still_rasterizes():
    decision = rule_3d_transform_raster(_unit("skewX(12deg)"))
    assert decision is not None
    assert decision.kind == DecisionKind.Raster
