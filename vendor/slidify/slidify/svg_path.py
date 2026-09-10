"""Compatibility shim for ``slidify.svg.path_parser``."""

from slidify.svg.path_parser import (
    PathCommand,
    _arc_to_cubics,
    _take_floats,
    _tokenize,
    commands_to_path_xml,
    parse_path,
    path_to_custgeom_xml,
)

__all__ = [
    "PathCommand",
    "_arc_to_cubics",
    "_take_floats",
    "_tokenize",
    "commands_to_path_xml",
    "parse_path",
    "path_to_custgeom_xml",
]
