"""Compatibility shim for ``slidify.svg.path``."""

from slidify.svg.path import (
    PATH_LOCAL_UNITS,
    CubicSegment,
    _flatten,
    angle_at,
    arc_to_cubic,
    detect_prst_txwarp,
    make_custgeom_xml,
    path_bbox,
    path_to_ooxml,
    raster_text_on_path,
)

__all__ = [
    "PATH_LOCAL_UNITS",
    "CubicSegment",
    "_flatten",
    "angle_at",
    "arc_to_cubic",
    "detect_prst_txwarp",
    "make_custgeom_xml",
    "path_bbox",
    "path_to_ooxml",
    "raster_text_on_path",
]

