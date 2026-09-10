"""Tests for slidify.units (visual unit clusterer)."""

from __future__ import annotations

from slidify.models import BoundingBox, DomElement
from slidify.units import cluster, flatten


def _el(
    eid: int,
    parent: int | None,
    depth: int,
    tag: str,
    *,
    bbox: tuple[float, float, float, float] = (0, 0, 100, 100),
    bg: str = "rgba(0, 0, 0, 0)",
    text: str | None = None,
    cls: str = "",
    radius: str = "0px",
) -> DomElement:
    x, y, w, h = bbox
    return DomElement(
        id=eid,
        parent_id=parent,
        depth=depth,
        tag=tag,
        cls=cls,
        bbox=BoundingBox(x=x, y=y, w=w, h=h),
        background_color=bg,
        text=text,
        border_radius=radius,
        stable_selector=f"#e{eid}",
    )


def test_empty():
    assert cluster([]) == []


def test_simple_text_inside_card():
    body = _el(0, None, 0, "BODY", bbox=(0, 0, 1280, 720))
    card = _el(1, 0, 1, "DIV", bbox=(40, 40, 400, 200), bg="rgb(255,255,255)", radius="12px")
    title = _el(2, 1, 2, "H3", bbox=(64, 64, 360, 32), text="Hi")
    p = _el(3, 1, 2, "P", bbox=(64, 110, 360, 60), text="Body copy")
    roots = cluster([body, card, title, p])
    assert len(roots) == 1  # body
    body_unit = roots[0]
    # body has one anchor child: the card.
    assert len(body_unit.children) == 1
    card_unit = body_unit.children[0]
    # Leaf-text elements (H3, P) are themselves anchors so each becomes a
    # sub-unit of the card. This keeps separate text blocks editable.
    text_child_tags = sorted(
        c.elements[0].tag for c in card_unit.children if c.elements
    )
    assert text_child_tags == ["H3", "P"]


def test_card_without_text_keeps_simple_anchor():
    body = _el(0, None, 0, "BODY", bbox=(0, 0, 1280, 720))
    card = _el(1, 0, 1, "DIV", bbox=(40, 40, 400, 200), bg="rgb(255,255,255)")
    icon = _el(2, 1, 2, "SPAN", bbox=(48, 48, 24, 24))  # no text, tiny
    roots = cluster([body, card, icon])
    body_unit = roots[0]
    assert len(body_unit.children) == 1
    card_unit = body_unit.children[0]
    # Plain non-text descendants stay inside the card unit.
    assert card_unit.children == []
    assert any(e.tag == "SPAN" for e in card_unit.elements)


def test_repeated_pattern_detected_as_list():
    body = _el(0, None, 0, "BODY", bbox=(0, 0, 1280, 720))
    grid = _el(1, 0, 1, "DIV", bbox=(40, 80, 1200, 400), bg="rgb(255,255,255)")
    items = []
    parent_id = 1
    next_id = 2
    for i in range(3):
        item = _el(next_id, parent_id, 2, "DIV", bbox=(40 + i * 400, 80, 380, 380), bg="rgb(250,250,250)", radius="8px")
        items.append(item)
        next_id += 1
        # Each card has a title and body — same structure.
        items.append(
            _el(next_id, item.id, 3, "H3", bbox=(50 + i * 400, 100, 360, 30), text=f"Title {i}")
        )
        next_id += 1
        items.append(
            _el(next_id, item.id, 3, "P", bbox=(50 + i * 400, 140, 360, 30), text="copy")
        )
        next_id += 1
    elems = [body, grid, *items]
    roots = cluster(elems)
    flat = flatten(roots)
    list_items = [u for u in flat if u.kind.value == "list_item"]
    assert len(list_items) == 3
