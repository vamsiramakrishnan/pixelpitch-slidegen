from __future__ import annotations

from slidify.models import BoundingBox, Decision, DecisionKind, DomElement, VisualUnit
from slidify.promotion import promote, to_emit_ops


def _bbox(x: float = 0, y: float = 0, w: float = 320, h: float = 180) -> BoundingBox:
    return BoundingBox(x=x, y=y, w=w, h=h)


def _el(
    eid: int,
    *,
    cls: str = "",
    text: str | None = None,
    background_color: str = "rgba(0, 0, 0, 0)",
    background_image: str = "none",
    border: str = "none",
    border_radius: str = "0px",
    box_shadow: str = "none",
    decorate_hint: str = "",
) -> DomElement:
    return DomElement(
        id=eid,
        parent_id=None,
        depth=0,
        tag="DIV",
        cls=cls,
        bbox=_bbox(),
        text=text,
        background_color=background_color,
        background_image=background_image,
        border=border,
        border_radius=border_radius,
        box_shadow=box_shadow,
        decorate_hint=decorate_hint,
    )


def test_complex_decorated_parent_promotes_to_raster_backplate_hybrid():
    decorative_child = VisualUnit(
        id="icon",
        bbox=_bbox(8, 8, 24, 24),
        elements=[_el(3, cls="icon", background_color="rgb(0, 102, 255)")],
    )
    child = VisualUnit(
        id="child",
        bbox=_bbox(24, 24, 220, 48),
        elements=[_el(2, text="Editable text")],
    )
    parent = VisualUnit(
        id="card",
        bbox=_bbox(),
        elements=[
            _el(
                1,
                cls="tile",
                background_color="rgba(20, 18, 50, 0.55)",
                background_image="linear-gradient(160deg, rgba(20,18,50,0.55), rgba(10,8,32,0.55))",
                border="1px solid rgba(255,255,255,0.08)",
                border_radius="22px",
                box_shadow="0 18px 50px rgba(0,0,0,0.40)",
            )
        ],
        children=[decorative_child, child],
    )
    decisions = {
        "card": Decision(kind=DecisionKind.NativeShape, confidence=0.8),
        "icon": Decision(kind=DecisionKind.NativeShape, confidence=1.0),
        "child": Decision(kind=DecisionKind.NativeText, confidence=1.0),
    }

    promoted = promote([parent], decisions)
    ops = to_emit_ops([parent], promoted)

    assert promoted["card"].kind == DecisionKind.Hybrid
    assert promoted["card"].metadata["raster_backplate"] is True
    assert promoted["icon"].kind == DecisionKind.Skip
    assert [op.unit_id for op in ops] == ["card", "child"]


def test_plain_native_parent_stays_native_shape():
    child = VisualUnit(
        id="child",
        bbox=_bbox(24, 24, 220, 48),
        elements=[_el(2, text="Editable text")],
    )
    parent = VisualUnit(
        id="plain",
        bbox=_bbox(),
        elements=[_el(1, background_color="rgb(255, 255, 255)")],
        children=[child],
    )
    decisions = {
        "plain": Decision(kind=DecisionKind.NativeShape, confidence=0.8),
        "child": Decision(kind=DecisionKind.NativeText, confidence=1.0),
    }

    promoted = promote([parent], decisions)

    assert promoted["plain"].kind == DecisionKind.NativeShape
