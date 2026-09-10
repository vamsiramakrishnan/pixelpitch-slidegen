"""SVG and custom-geometry helpers."""

from slidify.svg.path import (
    PATH_LOCAL_UNITS,
    CubicSegment,
    angle_at,
    arc_to_cubic,
    detect_prst_txwarp,
    make_custgeom_xml,
    path_bbox,
)
from slidify.svg.path_parser import (
    PathCommand,
    commands_to_path_xml,
    parse_path,
    path_to_custgeom_xml,
)
from slidify.svg.shapes import emit_svg_shapes, is_translatable_svg

__all__ = [
    "PATH_LOCAL_UNITS",
    "CubicSegment",
    "PathCommand",
    "angle_at",
    "arc_to_cubic",
    "commands_to_path_xml",
    "detect_prst_txwarp",
    "emit_svg_shapes",
    "is_translatable_svg",
    "make_custgeom_xml",
    "parse_path",
    "path_bbox",
    "path_to_custgeom_xml",
]
