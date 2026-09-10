"""Text layout and styled-run helpers for PPTX emission."""

from __future__ import annotations

import re

from slidify.colors import parse_color
from slidify.geom import parse_px
from slidify.gradients import parse_gradient
from slidify.models import BoundingBox, DomElement, VisualUnit


def _union_line_box(unit: VisualUnit) -> BoundingBox | None:
    """Return the union of every text run's line boxes inside ``unit``."""
    boxes: list[BoundingBox] = []
    for el in unit.all_elements():
        if el.runs:
            for r in el.runs:
                if r.is_break:
                    continue
                boxes.extend(r.line_boxes)
        elif el.text and el.text.strip():
            boxes.append(el.bbox)
    if not boxes:
        return None
    minx = min(b.x for b in boxes)
    miny = min(b.y for b in boxes)
    maxx = max(b.x + b.w for b in boxes)
    maxy = max(b.y + b.h for b in boxes)
    return BoundingBox(x=minx, y=miny, w=maxx - minx, h=maxy - miny)


def _are_spatially_distinct(elems: list[DomElement]) -> bool:
    """True iff elements should each emit as their own textbox."""
    if len(elems) < 2:
        return False
    boxes = [e.bbox for e in elems]
    for i in range(len(boxes)):
        for j in range(i + 1, len(boxes)):
            a, b = boxes[i], boxes[j]
            x_overlap = max(0.0, min(a.x2, b.x2) - max(a.x, b.x))
            y_overlap = max(0.0, min(a.y2, b.y2) - max(a.y, b.y))
            if x_overlap <= 0 or y_overlap <= 0:
                return True
    return False


def _has_title_sized_text(unit: VisualUnit) -> bool:
    """True iff the unit has at least one text element/run rendered at >= 20px."""
    for e in unit.all_elements():
        if e.runs:
            for r in e.runs:
                if parse_px(r.font_size) >= 20.0:
                    return True
        elif e.text and e.text.strip():
            if parse_px(e.font_size) >= 20.0:
                return True
    return False


def _is_bg_clip_text(anchor: DomElement) -> bool:
    """Heuristic detection of the CSS ``background-clip: text`` recipe."""
    if not anchor.background_image or anchor.background_image == "none":
        return False
    if "gradient(" not in anchor.background_image:
        return False
    col_str = (anchor.color or "").strip().lower()
    return (
        re.match(
            r"rgba?\(\s*\d+\s*,\s*\d+\s*,\s*\d+\s*,\s*0(?:\.0+)?\s*\)",
            col_str,
        )
        is not None
    )


def _try_apply_gradient_text_fill(
    font, spec: dict, fallback_el: DomElement | None = None
) -> bool:
    """Apply native gradient fill on a text run when OOXML supports it."""
    raw_color = spec.get("color") or ""
    if parse_color(raw_color) is not None:
        return False
    spec_bg = spec.get("background_image") or "none"
    if spec_bg == "none" and fallback_el is not None:
        bg_image = fallback_el.background_image or "none"
    else:
        bg_image = spec_bg
    if not bg_image or bg_image == "none":
        return False
    grad = parse_gradient(bg_image)
    if grad is None or len(grad.stops) < 2:
        return False
    try:
        font.fill.gradient()
    except Exception:
        return False
    try:
        from lxml import etree as _et

        ns_a = "http://schemas.openxmlformats.org/drawingml/2006/main"
        rpr = font._rPr
        if rpr is None:
            return False
        grad_fill = rpr.find(f"{{{ns_a}}}gradFill")
        if grad_fill is None:
            return False
        gs_lst = grad_fill.find(f"{{{ns_a}}}gsLst")
        if gs_lst is None:
            return False
        for child in list(gs_lst):
            gs_lst.remove(child)
        for stop in grad.stops:
            gs = _et.SubElement(
                gs_lst,
                f"{{{ns_a}}}gs",
                attrib={
                    "pos": str(
                        int(round(max(0.0, min(1.0, stop.position)) * 100_000))
                    )
                },
            )
            srgb = _et.SubElement(
                gs, f"{{{ns_a}}}srgbClr", attrib={"val": stop.color_hex}
            )
            if stop.alpha < 0.999:
                _et.SubElement(
                    srgb,
                    f"{{{ns_a}}}alpha",
                    attrib={"val": str(int(round(stop.alpha * 100_000)))},
                )
        for tag in ("lin", "path"):
            for el in grad_fill.findall(f"{{{ns_a}}}{tag}"):
                grad_fill.remove(el)
        from slidify.gradients import LinearGradient, RadialGradient

        if isinstance(grad, LinearGradient):
            pptx_deg = (grad.angle_deg - 90.0) % 360.0
            _et.SubElement(
                grad_fill,
                f"{{{ns_a}}}lin",
                attrib={"ang": str(int(round(pptx_deg * 60_000))), "scaled": "0"},
            )
        elif isinstance(grad, RadialGradient):
            path = _et.SubElement(
                grad_fill, f"{{{ns_a}}}path", attrib={"path": "circle"}
            )
            cx = max(0.0, min(1.0, grad.cx))
            cy = max(0.0, min(1.0, grad.cy))
            _et.SubElement(
                path,
                f"{{{ns_a}}}fillToRect",
                attrib={
                    "l": str(int(round(cx * 100_000))),
                    "t": str(int(round(cy * 100_000))),
                    "r": str(int(round((1.0 - cx) * 100_000))),
                    "b": str(int(round((1.0 - cy) * 100_000))),
                },
            )
        return True
    except Exception:
        return False


def _resolve_run_color(spec: dict, fallback_el: DomElement):
    """Pick the RGB color for a styled text run."""
    raw_color = spec.get("color") or fallback_el.color
    parsed = parse_color(raw_color)
    if parsed is not None:
        return parsed[0]
    own_bg = spec.get("background_image")
    if not own_bg or own_bg == "none":
        own_bg = None
    bg_image = own_bg or fallback_el.background_image or "none"
    if bg_image and bg_image != "none":
        grad = parse_gradient(bg_image)
        if grad is not None and grad.stops:
            stop = grad.stops[0]
            from pptx.dml.color import RGBColor

            try:
                return RGBColor(
                    int(stop.color_hex[0:2], 16),
                    int(stop.color_hex[2:4], 16),
                    int(stop.color_hex[4:6], 16),
                )
            except (ValueError, IndexError):
                pass
    return None

