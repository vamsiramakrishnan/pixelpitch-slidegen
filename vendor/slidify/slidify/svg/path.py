"""Wave-2 PathShape support — IR → OOXML `<a:custGeom>` translation.

The IR carries discriminated `IRPathCommand`s (`M / L / C / Q / A / Z`) in
slide-pixel coordinates. PPTX `<a:custGeom>` requires path commands in
**path-local space** — integer coordinates inside `<a:path w="…" h="…">`
mapped to the freeform shape's bbox.

This module bridges those two worlds:

  * `arc_to_cubic()` flattens an SVG `A` arc to ≤4 cubic Béziers, the
    standard approach because OOXML `<a:custGeom>` has no arc primitive.
  * `path_bbox()` returns a tight bounding box for a command list.
  * `path_to_ooxml()` emits the `<a:path>` XML body, transforming
    slide-pixel coordinates to the path-local space defined by the bbox.

The pre-existing `slidify.svg_path` module solves the same translation for
HTML-derived `d` strings; the IR side reuses its arc-flattening kernel
under the hood for byte-exact output parity.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from slidify.svg.path_parser import _arc_to_cubics

# Default w/h used inside `<a:path w=… h=…>` — large enough that integer
# rounding loses <1px on a 1280-wide slide. PPTX accepts any integer; the
# shape's xfrm scales the path to its on-slide bbox.
PATH_LOCAL_UNITS = 100_000


@dataclass(frozen=True)
class CubicSegment:
    """Output of `arc_to_cubic` — one cubic Bezier in absolute coordinates."""

    c1x: float
    c1y: float
    c2x: float
    c2y: float
    x: float
    y: float


def arc_to_cubic(
    rx: float,
    ry: float,
    x_axis_rotation: float,
    large_arc: bool,
    sweep: bool,
    x0: float,
    y0: float,
    x1: float,
    y1: float,
) -> list[CubicSegment]:
    """Flatten an SVG endpoint-form `A` arc to ≤4 cubic Bezier segments.

    Algorithm follows the SVG 1.1 implementation notes (Appendix F.6),
    splitting the arc into segments of at most π/2 rad for high accuracy.

    Args:
        rx, ry: Ellipse radii (any sign — absolute value used).
        x_axis_rotation: Rotation of the ellipse, degrees CW.
        large_arc: True selects the larger of the two possible arcs.
        sweep:     True selects the CW-traversed arc.
        x0, y0:    Start point (current path position).
        x1, y1:    End point (the arc command's `x`, `y`).

    Returns:
        List of CubicSegment; empty when (x0,y0) == (x1,y1).
    """
    raw = _arc_to_cubics(
        x0, y0, rx, ry, x_axis_rotation, bool(large_arc), bool(sweep), x1, y1
    )
    return [CubicSegment(*seg) for seg in raw]


def _expand_command(
    prev_x: float,
    prev_y: float,
    cmd,  # IRPathCommand-shaped
) -> list[tuple[str, list[float]]]:
    """Lower one IRPathCommand into a list of (op, [coords]) primitives.

    Returns:
        List of tuples whose first element is one of {'M','L','C','Q','Z'}
        (no 'A' — arcs are flattened to 'C') and whose second is the
        coordinate payload in absolute slide-pixel space.
    """
    op = cmd.op
    if op == "M":
        return [("M", [float(cmd.x), float(cmd.y)])]
    if op == "L":
        return [("L", [float(cmd.x), float(cmd.y)])]
    if op == "C":
        return [(
            "C",
            [
                float(cmd.x1), float(cmd.y1),
                float(cmd.x2), float(cmd.y2),
                float(cmd.x), float(cmd.y),
            ],
        )]
    if op == "Q":
        return [(
            "Q",
            [float(cmd.x1), float(cmd.y1), float(cmd.x), float(cmd.y)],
        )]
    if op == "A":
        segs = arc_to_cubic(
            float(cmd.rx),
            float(cmd.ry),
            float(cmd.xAxisRotationDeg),
            bool(cmd.largeArc),
            bool(cmd.sweep),
            prev_x,
            prev_y,
            float(cmd.x),
            float(cmd.y),
        )
        if not segs:
            # Endpoints coincide — emit a no-op line to keep cur point synced.
            return [("L", [float(cmd.x), float(cmd.y)])]
        return [
            ("C", [s.c1x, s.c1y, s.c2x, s.c2y, s.x, s.y]) for s in segs
        ]
    if op == "Z":
        return [("Z", [])]
    raise ValueError(f"unknown path op: {op!r}")


def _flatten(commands) -> list[tuple[str, list[float]]]:
    """Walk command list once, expanding A → cubics and tracking current point.

    OOXML `<a:close/>` ends a subpath; subsequent draw ops without a
    fresh `<a:moveTo>` are malformed. SVG/CSS, by contrast, treat a
    post-Z `L` as drawing from the subpath start. Bridge the two by
    synthesizing a `moveTo(start)` whenever a draw op appears without
    an open subpath. Same logic also handles the (malformed but
    plausible) case of an L/C/Q at the very start with no preceding M.
    """
    out: list[tuple[str, list[float]]] = []
    cur_x = 0.0
    cur_y = 0.0
    start_x = 0.0
    start_y = 0.0
    subpath_open = False
    for cmd in commands:
        prims = _expand_command(cur_x, cur_y, cmd)
        for prim in prims:
            op, coords = prim
            if op in ("L", "C", "Q") and not subpath_open:
                out.append(("M", [start_x, start_y]))
                subpath_open = True
            out.append(prim)
            if op == "M":
                cur_x, cur_y = coords[0], coords[1]
                start_x, start_y = cur_x, cur_y
                subpath_open = True
            elif op == "L":
                cur_x, cur_y = coords[0], coords[1]
            elif op == "C":
                cur_x, cur_y = coords[4], coords[5]
            elif op == "Q":
                cur_x, cur_y = coords[2], coords[3]
            elif op == "Z":
                cur_x, cur_y = start_x, start_y
                subpath_open = False
    return out


def path_bbox(commands) -> tuple[float, float, float, float]:
    """Compute a tight bounding box for an IR command list.

    Returns:
        (x_min, y_min, x_max, y_max) in slide-pixel space. Bezier control
        points are included in the bounds (not glyph-tight, but the
        compiler's freeform shape needs to enclose them so PPTX clips
        nothing).

    For an empty path or a path with no usable points returns
    `(0.0, 0.0, 0.0, 0.0)`.
    """
    flat = _flatten(commands)
    xs: list[float] = []
    ys: list[float] = []
    for op, coords in flat:
        if op == "Z":
            continue
        # Coords are sequences of (x, y) pairs.
        for i in range(0, len(coords), 2):
            xs.append(coords[i])
            ys.append(coords[i + 1])
    if not xs or not ys:
        return (0.0, 0.0, 0.0, 0.0)
    return (min(xs), min(ys), max(xs), max(ys))


def _project(
    x: float,
    y: float,
    bbox: tuple[float, float, float, float],
    units: int,
) -> tuple[int, int]:
    """Map a slide-pixel point to integer path-local coordinates."""
    x_min, y_min, x_max, y_max = bbox
    w = max(1e-9, x_max - x_min)
    h = max(1e-9, y_max - y_min)
    px = (x - x_min) / w * units
    py = (y - y_min) / h * units
    return (int(round(px)), int(round(py)))


def _pt_xml(x: int, y: int) -> str:
    return f'<a:pt x="{x}" y="{y}"/>'


def path_to_ooxml(
    commands,
    bbox: tuple[float, float, float, float],
    units: int = PATH_LOCAL_UNITS,
) -> str:
    """Emit the `<a:path>` XML body for an IR command list.

    Coordinates are projected from `bbox` (slide-pixel space) into a
    `units × units` integer grid. The caller wraps this in a `<a:custGeom>`
    container and applies the shape's xfrm to scale back to slide units.

    Args:
        commands: Iterable of IRPathCommand-shaped objects.
        bbox: `(x_min, y_min, x_max, y_max)` in slide-pixel space — the
              same bbox used to size the freeform shape on the slide.
        units: Path-local resolution (default 100_000, sub-pixel even at
              full slide width).

    Returns:
        The `<a:path w=… h=…>…</a:path>` XML string. Caller is responsible
        for namespacing (the string uses the `a:` prefix bound by the
        surrounding spPr).
    """
    flat = _flatten(commands)
    parts: list[str] = [f'<a:path w="{units}" h="{units}">']
    for op, coords in flat:
        if op == "M":
            x, y = _project(coords[0], coords[1], bbox, units)
            parts.append(f"<a:moveTo>{_pt_xml(x, y)}</a:moveTo>")
        elif op == "L":
            x, y = _project(coords[0], coords[1], bbox, units)
            parts.append(f"<a:lnTo>{_pt_xml(x, y)}</a:lnTo>")
        elif op == "C":
            c1x, c1y = _project(coords[0], coords[1], bbox, units)
            c2x, c2y = _project(coords[2], coords[3], bbox, units)
            x, y = _project(coords[4], coords[5], bbox, units)
            parts.append(
                "<a:cubicBezTo>"
                + _pt_xml(c1x, c1y)
                + _pt_xml(c2x, c2y)
                + _pt_xml(x, y)
                + "</a:cubicBezTo>"
            )
        elif op == "Q":
            cx, cy = _project(coords[0], coords[1], bbox, units)
            x, y = _project(coords[2], coords[3], bbox, units)
            parts.append(
                "<a:quadBezTo>" + _pt_xml(cx, cy) + _pt_xml(x, y) + "</a:quadBezTo>"
            )
        elif op == "Z":
            parts.append("<a:close/>")
    parts.append("</a:path>")
    return "".join(parts)


def make_custgeom_xml(
    commands,
    bbox: tuple[float, float, float, float],
    units: int = PATH_LOCAL_UNITS,
) -> str:
    """Wrap `path_to_ooxml` in a complete `<a:custGeom>` element.

    Use this when patching a freeform shape's spPr directly (replaces the
    auto-generated <a:prstGeom> from `slide.shapes.add_shape`).
    """
    body = path_to_ooxml(commands, bbox, units=units)
    return (
        "<a:custGeom>"
        "<a:avLst/><a:gdLst/><a:ahLst/><a:cxnLst/>"
        f'<a:rect l="0" t="0" r="{units}" b="{units}"/>'
        f"<a:pathLst>{body}</a:pathLst>"
        "</a:custGeom>"
    )


# ---- Stretch goal §9.1: text-on-path raster fallback ------------------------


def raster_text_on_path(text_node) -> None:
    """Render text following an arbitrary path via Pillow → RasterNode.

    NOT IMPLEMENTED in Wave-2. Per CONTRACT §9.1, only `<a:prstTxWarp>`
    preset paths (textArchUp, textCircle, textWave1, textCanDown) ship
    natively in Wave-2; arbitrary `onPath` falls through here.

    Crews using `MaskedHeading.maskKind='image'` or non-preset
    `KineticType` rotations should expect this NotImplementedError until
    Wave-3.
    """
    raise NotImplementedError(
        "text-on-path raster fallback pending — see CONTRACT §9.1"
    )


# Map IR onPath geometry → OOXML `prstTxWarp` preset name when possible.
# The preset list is intentionally tiny: PPTX warps are heuristic and
# don't accept arbitrary path data, so we recognize only the most common
# IR shapes (semicircle arc, full circle, sine wave) and fall back to
# raster otherwise.
_PRESET_TXWARP_NAMES = {
    "arch-up": "textArchUp",
    "arch-down": "textArchDown",
    "circle": "textCircle",
    "wave-1": "textWave1",
    "wave-2": "textWave2",
    "can-down": "textCanDown",
    "can-up": "textCanUp",
}


def detect_prst_txwarp(commands) -> str | None:
    """Identify a `prstTxWarp` preset that approximates the given path.

    Returns the OOXML preset name (e.g. `'textArchUp'`) when the path is
    a clean approximation of a known warp shape. Returns None otherwise;
    callers should fall back to `raster_text_on_path`.

    Wave-2 ships a deliberately-narrow detector: only single-A-arc paths
    matching a half-circle map to `textArchUp` / `textArchDown`. Crews
    needing other warps must redesign as straight text + rotation.
    """
    if len(commands) != 2:
        return None
    move, arc = commands[0], commands[1]
    if move.op != "M" or arc.op != "A":
        return None
    # Half-circle arc whose endpoints share a Y coordinate?
    if move.y is None or arc.y is None:
        return None
    if abs(float(move.y) - float(arc.y)) > 1.0:
        return None
    # Sweep determines arch direction.
    if arc.largeArc:
        return None
    return "textArchUp" if not arc.sweep else "textArchDown"


def angle_at(
    commands, t: float
) -> float:
    """Tangent angle (radians) at parameter `t` ∈ [0,1] along the path.

    Used by future text-on-path raster fallback to rotate glyphs. Wave-2
    implementation is approximate (linear interpolation between command
    endpoints) — sufficient for sanity-checking; not pixel-accurate.
    """
    flat = _flatten(commands)
    pts: list[tuple[float, float]] = []
    for op, coords in flat:
        if op in ("L", "Q"):
            pts.append((coords[-2], coords[-1]))
        elif op == "C":
            pts.append((coords[4], coords[5]))
        elif op == "M":
            pts.append((coords[0], coords[1]))
    if len(pts) < 2:
        return 0.0
    idx = max(0, min(len(pts) - 2, int(t * (len(pts) - 1))))
    (x0, y0), (x1, y1) = pts[idx], pts[idx + 1]
    return math.atan2(y1 - y0, x1 - x0)


__all__ = [
    "PATH_LOCAL_UNITS",
    "CubicSegment",
    "angle_at",
    "arc_to_cubic",
    "detect_prst_txwarp",
    "make_custgeom_xml",
    "path_bbox",
    "path_to_ooxml",
    "raster_text_on_path",
]
