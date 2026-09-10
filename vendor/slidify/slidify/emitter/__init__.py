"""PPTX emission package."""

from slidify.emitter.core import (
    Emitter,
    _apply_explicit_autofit,
    _grow_bbox_for_fallback_wrap,
    _resolve_run_color,
    _rotation_degrees,
    _try_apply_gradient_text_fill,
    _union_line_box,
)
from slidify.emitter.metrics import native_area_ratio
from slidify.emitter.shapes import _shape_kind_for_anchor

__all__ = [
    "Emitter",
    "_apply_explicit_autofit",
    "_grow_bbox_for_fallback_wrap",
    "_resolve_run_color",
    "_rotation_degrees",
    "_shape_kind_for_anchor",
    "_try_apply_gradient_text_fill",
    "_union_line_box",
    "native_area_ratio",
]
