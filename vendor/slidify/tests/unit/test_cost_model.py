from __future__ import annotations

from slidify.classifier.cost_model import classify_with_cost_model, extract_features, predict_costs
from slidify.models import BoundingBox, Decision, DecisionKind, DomElement, VisualUnit


def _el(
    id_: int,
    *,
    text: str | None = None,
    background_color: str = "rgba(0, 0, 0, 0)",
    background_image: str = "none",
    filter_: str = "none",
    tag: str = "DIV",
    bbox: BoundingBox | None = None,
) -> DomElement:
    return DomElement(
        id=id_,
        parent_id=None,
        depth=0,
        tag=tag,
        bbox=bbox or BoundingBox(x=0, y=0, w=200, h=80),
        text=text,
        background_color=background_color,
        background_image=background_image,
        filter=filter_,
        font_family="Inter",
        font_size="24px",
        font_weight="600",
        color="rgb(245, 245, 247)",
    )


def test_cost_model_pushes_clean_leaf_text_native() -> None:
    unit = VisualUnit(
        id="u_text",
        bbox=BoundingBox(x=96, y=96, w=420, h=80),
        elements=[_el(1, text="Clean editable headline")],
    )

    prediction = predict_costs(unit, {})
    decision = classify_with_cost_model(unit, {})

    assert prediction.decision == DecisionKind.NativeText
    assert decision is not None
    assert decision.kind == DecisionKind.NativeText
    assert decision.source_tier == "tier2:model"
    assert decision.metadata["model_version"].startswith("v0.calibrated-prior")


def test_cost_model_routes_unsupported_css_to_raster() -> None:
    unit = VisualUnit(
        id="u_filter",
        bbox=BoundingBox(x=0, y=0, w=800, h=420),
        elements=[
            _el(
                1,
                background_image="url(hero.jpg)",
                filter_="grayscale(1) contrast(1.2)",
                bbox=BoundingBox(x=0, y=0, w=800, h=420),
            )
        ],
    )

    features = extract_features(unit, {})
    decision = classify_with_cost_model(unit, {})

    assert features.has_unsupported_css is True
    assert decision is not None
    assert decision.kind == DecisionKind.Raster


def test_cost_model_prefers_hybrid_for_decorated_parent_with_native_children() -> None:
    child = VisualUnit(
        id="u_child",
        bbox=BoundingBox(x=120, y=120, w=360, h=80),
        elements=[_el(2, text="Native child text")],
    )
    parent = VisualUnit(
        id="u_parent",
        bbox=BoundingBox(x=96, y=96, w=640, h=280),
        elements=[
            _el(
                1,
                background_image="url(texture.png)",
                bbox=BoundingBox(x=96, y=96, w=640, h=280),
            )
        ],
        children=[child],
    )
    prior = {
        child.id: Decision(
            kind=DecisionKind.NativeText,
            confidence=0.95,
            reason="test",
            source_tier="tier1",
        )
    }

    prediction = predict_costs(parent, prior)
    decision = classify_with_cost_model(parent, prior)

    assert prediction.decision == DecisionKind.Hybrid
    assert prediction.hybrid_ev > prediction.raster_ev
    assert decision is not None
    assert decision.kind == DecisionKind.Hybrid
