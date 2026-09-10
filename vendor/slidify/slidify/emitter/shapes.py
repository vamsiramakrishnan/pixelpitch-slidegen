"""Shape selection helpers for PPTX emission."""

from __future__ import annotations

from pptx.enum.shapes import MSO_SHAPE

from slidify.models import BoundingBox, DomElement
from slidify.preset_shapes import detect_preset_shape


def _shape_kind_for_anchor(
    anchor: DomElement, radius_px: float
) -> tuple[int, BoundingBox | None]:
    """Pick the right MSO_SHAPE and optional bbox override for an anchor."""
    match = detect_preset_shape(anchor)
    if match is not None:
        return match.preset, match.bbox_override
    w = anchor.bbox.w
    h = anchor.bbox.h
    if w > 0 and h > 0 and radius_px >= min(w, h) * 0.45:
        return MSO_SHAPE.OVAL, None
    return (
        MSO_SHAPE.ROUNDED_RECTANGLE if radius_px > 0 else MSO_SHAPE.RECTANGLE,
        None,
    )

