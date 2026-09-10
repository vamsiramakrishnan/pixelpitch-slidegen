from __future__ import annotations

from slidify.classifier.tier1 import classify_tier1
from slidify.models import BoundingBox, DecisionKind, DomElement, UnitKind, VisualUnit


def _unit(**attrs) -> VisualUnit:
    bbox = BoundingBox(x=40, y=40, w=160, h=80)
    el = DomElement(
        id=1,
        parent_id=None,
        depth=0,
        tag="DIV",
        bbox=bbox,
        text="",
        background_color="rgb(255,255,255)",
        **attrs,
    )
    return VisualUnit(id="u", kind=UnitKind.Generic, bbox=bbox, elements=[el])


def test_record_hint_routes_region_to_animated_gif_raster():
    decision = classify_tier1(_unit(pptx_record="gif duration=2s fps=15"))

    assert decision is not None
    assert decision.kind == DecisionKind.Raster
    assert decision.reason == "animated_gif"
    assert decision.metadata["animated_gif"] is True


def test_css_animation_routes_region_to_animated_gif_raster():
    decision = classify_tier1(
        _unit(animation_name="pulse", animation_duration="800ms")
    )

    assert decision is not None
    assert decision.kind == DecisionKind.Raster
    assert decision.reason == "animated_gif"
