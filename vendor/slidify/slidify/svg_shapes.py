"""Compatibility shim for ``slidify.svg.shapes``."""

from slidify.svg.shapes import (
    _NS_A,
    _apply_svg_gradient_fill,
    _build_mapping,
    emit_svg_shapes,
    is_translatable_svg,
)

__all__ = [
    "_NS_A",
    "_apply_svg_gradient_fill",
    "_build_mapping",
    "emit_svg_shapes",
    "is_translatable_svg",
]

