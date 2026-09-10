"""Geometry and transform helpers for PPTX emission."""

from __future__ import annotations

import math
import re

from pptx.util import Emu

from slidify.geom import SLIDE_H_PX, SLIDE_W_PX, px_to_emu
from slidify.models import BoundingBox, DomElement
from slidify.text_metrics import estimate_wrapped_lines, genre_for_family


def _clamp_bbox(b: BoundingBox) -> BoundingBox:
    """Clamp a bounding box to slide bounds for contained regions."""
    x = max(0.0, min(b.x, SLIDE_W_PX - 1))
    y = max(0.0, min(b.y, SLIDE_H_PX - 1))
    w = max(1.0, min(b.w, SLIDE_W_PX - x))
    h = max(1.0, min(b.h, SLIDE_H_PX - y))
    return BoundingBox(x=x, y=y, w=w, h=h)


def _emu_rect(b: BoundingBox) -> tuple[Emu, Emu, Emu, Emu]:
    b = _clamp_bbox(b)
    return (
        Emu(px_to_emu(b.x)),
        Emu(px_to_emu(b.y)),
        Emu(px_to_emu(b.w)),
        Emu(px_to_emu(b.h)),
    )


def _grow_bbox_for_fallback_wrap(
    bbox: BoundingBox,
    text: str,
    font_size_pt: float,
    font_family: str | None,
    line_height_factor: float = 1.25,
    max_lines: int = 4,
) -> BoundingBox:
    """Grow body text boxes when fallback fonts would otherwise wrap and clip."""
    if not text or font_size_pt <= 0:
        return bbox
    if font_size_pt >= 24.0:
        return bbox
    line_height_px = font_size_pt * 1.333 * line_height_factor
    if bbox.h >= line_height_px * 2:
        return bbox
    genre = genre_for_family(font_family)
    try:
        n_lines = estimate_wrapped_lines(
            text, bbox_w_px=bbox.w, font_size_pt=font_size_pt, genre=genre
        )
    except Exception:
        return bbox
    if n_lines <= 1:
        return bbox
    n_lines = min(n_lines, max_lines)
    new_h = max(bbox.h, n_lines * line_height_px)
    return BoundingBox(x=bbox.x, y=bbox.y, w=bbox.w, h=new_h)


def _emu_rect_offcanvas(b: BoundingBox) -> tuple[Emu, Emu, Emu, Emu]:
    """Convert px bbox to EMU while preserving negative off-canvas origins."""
    w = max(1.0, b.w)
    h = max(1.0, b.h)
    return (
        Emu(px_to_emu(b.x)),
        Emu(px_to_emu(b.y)),
        Emu(px_to_emu(w)),
        Emu(px_to_emu(h)),
    )


def _rotation_degrees(transform: str) -> float:
    """Extract native 2D rotation degrees from CSS transform text."""
    t = (transform or "").strip()
    if not t or t.lower() == "none":
        return 0.0
    m = re.search(r"rotate\(\s*([-+]?\d*\.?\d+)(deg|rad|turn)?\s*\)", t, re.I)
    if m:
        value = float(m.group(1))
        unit = (m.group(2) or "deg").lower()
        if unit == "rad":
            return math.degrees(value)
        if unit == "turn":
            return value * 360.0
        return value
    m = re.search(r"matrix\(\s*([^)]+)\)", t, re.I)
    if not m:
        return 0.0
    parts = [p.strip() for p in m.group(1).split(",")]
    if len(parts) < 4:
        return 0.0
    try:
        a = float(parts[0])
        b = float(parts[1])
    except ValueError:
        return 0.0
    angle = math.degrees(math.atan2(b, a))
    return 0.0 if abs(angle) < 0.01 else angle


def _apply_rotation(shape, el: DomElement | None) -> None:
    if el is None:
        return
    angle = _rotation_degrees(el.transform)
    if not angle:
        return
    try:
        shape.rotation = angle
    except Exception:
        pass

