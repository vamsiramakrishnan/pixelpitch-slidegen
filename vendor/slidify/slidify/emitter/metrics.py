"""Emission quality metrics."""

from __future__ import annotations

from slidify.emitter.geometry import _clamp_bbox
from slidify.geom import SLIDE_H_PX, SLIDE_W_PX
from slidify.models import DecisionKind, EmitOp


def native_area_ratio(
    ops: list[EmitOp], slide_w: int = SLIDE_W_PX, slide_h: int = SLIDE_H_PX
) -> float:
    """Compute approximate fraction of slide area covered by native ops."""
    total = float(slide_w * slide_h)
    if total <= 0:
        return 0.0
    native_area = 0.0
    for op in ops:
        if op.decision.kind in (
            DecisionKind.NativeText,
            DecisionKind.NativeShape,
            DecisionKind.NativeBullet,
            DecisionKind.NativePicture,
            DecisionKind.NativeSvg,
            DecisionKind.NativeTable,
            DecisionKind.Hybrid,
        ):
            bb = _clamp_bbox(op.bbox)
            native_area += bb.w * bb.h
    return min(1.0, native_area / total)
