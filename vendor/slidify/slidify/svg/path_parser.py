"""SVG path → PPTX `<a:custGeom>` translation.

Parses an SVG path `d` attribute and emits a normalized absolute command
list plus the corresponding OOXML path-XML fragment.

Supported commands:
    M / m   moveTo
    L / l   lineTo
    H / h   horizontal lineTo
    V / v   vertical lineTo
    C / c   cubic Bezier
    S / s   smooth cubic Bezier (reflected first control)
    Q / q   quadratic Bezier
    T / t   smooth quadratic Bezier (reflected control)
    A / a   elliptical arc (approximated as up to four cubic Beziers)
    Z / z   close
"""

from __future__ import annotations

import math
import re

# Public command tuple shapes:
#   ("moveTo",     x, y)
#   ("lineTo",     x, y)
#   ("cubicBezTo", c1x, c1y, c2x, c2y, x, y)
#   ("quadBezTo",  cx, cy, x, y)
#   ("close",)

PathCommand = tuple

_TOKEN_RE = re.compile(r"[MmLlHhVvCcSsQqTtAaZz]|-?\d*\.?\d+(?:[eE][-+]?\d+)?")


def _tokenize(d: str) -> list[str]:
    return _TOKEN_RE.findall(d or "")


def _take_floats(tokens: list[str], i: int, n: int) -> tuple[list[float], int]:
    vals = [float(tokens[i + k]) for k in range(n)]
    return vals, i + n


def _arc_to_cubics(
    x1: float,
    y1: float,
    rx: float,
    ry: float,
    phi_deg: float,
    large_arc: bool,
    sweep: bool,
    x2: float,
    y2: float,
) -> list[tuple[float, float, float, float, float, float]]:
    """Convert an SVG endpoint-form arc into cubic Bezier segments.

    Returns a list of (c1x, c1y, c2x, c2y, x, y) absolute control/end points,
    each spanning at most pi/2 radians for good circular approximation.
    Per the SVG spec:
      - if (x1, y1) == (x2, y2): the arc is omitted entirely (empty list).
      - if rx == 0 or ry == 0: degenerate to a straight line, returned as a
        single zero-curvature cubic (control points on the chord) so callers
        can keep emitting cubicBezTo uniformly.
    """
    # Endpoints coincide → no arc.
    if x1 == x2 and y1 == y2:
        return []

    rx = abs(rx)
    ry = abs(ry)
    if rx == 0.0 or ry == 0.0:
        # Spec: treat as a line. Emit a degenerate cubic so the caller path
        # stays consistent (control points on the chord).
        return [(x1, y1, x2, y2, x2, y2)]

    phi = math.radians(phi_deg)
    cos_phi = math.cos(phi)
    sin_phi = math.sin(phi)

    # Step 1: compute (x1', y1') in the rotated/translated frame.
    dx = (x1 - x2) / 2.0
    dy = (y1 - y2) / 2.0
    x1p = cos_phi * dx + sin_phi * dy
    y1p = -sin_phi * dx + cos_phi * dy

    # Out-of-range radii correction (F.6.6.2).
    lam = (x1p * x1p) / (rx * rx) + (y1p * y1p) / (ry * ry)
    if lam > 1.0:
        s = math.sqrt(lam)
        rx *= s
        ry *= s

    # Step 2: compute (cx', cy').
    rx2 = rx * rx
    ry2 = ry * ry
    x1p2 = x1p * x1p
    y1p2 = y1p * y1p
    num = rx2 * ry2 - rx2 * y1p2 - ry2 * x1p2
    den = rx2 * y1p2 + ry2 * x1p2
    factor_sq = max(0.0, num / den) if den != 0.0 else 0.0
    factor = math.sqrt(factor_sq)
    if large_arc == sweep:
        factor = -factor
    cxp = factor * (rx * y1p) / ry
    cyp = factor * -(ry * x1p) / rx

    # Step 3: (cx, cy) in the original frame.
    cx = cos_phi * cxp - sin_phi * cyp + (x1 + x2) / 2.0
    cy = sin_phi * cxp + cos_phi * cyp + (y1 + y2) / 2.0

    # Step 4: start angle and sweep delta.
    def _angle(ux: float, uy: float, vx: float, vy: float) -> float:
        n = math.hypot(ux, uy) * math.hypot(vx, vy)
        if n == 0.0:
            return 0.0
        c = max(-1.0, min(1.0, (ux * vx + uy * vy) / n))
        a = math.acos(c)
        if ux * vy - uy * vx < 0.0:
            a = -a
        return a

    sx = (x1p - cxp) / rx
    sy = (y1p - cyp) / ry
    ex = (-x1p - cxp) / rx
    ey = (-y1p - cyp) / ry
    theta1 = _angle(1.0, 0.0, sx, sy)
    delta = _angle(sx, sy, ex, ey)
    if not sweep and delta > 0.0:
        delta -= 2.0 * math.pi
    elif sweep and delta < 0.0:
        delta += 2.0 * math.pi

    # Split into segments of at most pi/2 for accurate approximation.
    n_segments = max(1, int(math.ceil(abs(delta) / (math.pi / 2.0))))
    seg_delta = delta / n_segments

    segments: list[tuple[float, float, float, float, float, float]] = []
    t = math.tan(seg_delta / 2.0)
    alpha = math.sin(seg_delta) * (math.sqrt(4.0 + 3.0 * t * t) - 1.0) / 3.0

    def _to_world(px: float, py: float) -> tuple[float, float]:
        return (
            cos_phi * px - sin_phi * py + cx,
            sin_phi * px + cos_phi * py + cy,
        )

    for k in range(n_segments):
        a0 = theta1 + k * seg_delta
        a1 = a0 + seg_delta

        cos_a0 = math.cos(a0)
        sin_a0 = math.sin(a0)
        cos_a1 = math.cos(a1)
        sin_a1 = math.sin(a1)

        # Endpoints in unrotated ellipse frame.
        p0x = rx * cos_a0
        p0y = ry * sin_a0
        p1x = rx * cos_a1
        p1y = ry * sin_a1

        # Control points using the standard cubic-arc approximation.
        c1x_pre = p0x - alpha * rx * sin_a0
        c1y_pre = p0y + alpha * ry * cos_a0
        c2x_pre = p1x + alpha * rx * sin_a1
        c2y_pre = p1y - alpha * ry * cos_a1

        c1 = _to_world(c1x_pre, c1y_pre)
        c2 = _to_world(c2x_pre, c2y_pre)
        end = _to_world(p1x, p1y)
        segments.append((c1[0], c1[1], c2[0], c2[1], end[0], end[1]))

    return segments


def parse_path(d: str) -> list[PathCommand]:
    """Parse an SVG path `d` string into normalized absolute commands."""
    tokens = _tokenize(d)
    commands: list[PathCommand] = []

    i = 0
    cur_x = 0.0
    cur_y = 0.0
    start_x = 0.0
    start_y = 0.0
    last_cmd: str | None = None
    # Last cubic control2 (absolute) — for S/s reflection.
    last_cubic_ctrl: tuple[float, float] | None = None
    # Last quadratic control (absolute) — for T/t reflection.
    last_quad_ctrl: tuple[float, float] | None = None

    while i < len(tokens):
        tok = tokens[i]
        if tok.isalpha():
            cmd = tok
            i += 1
        else:
            # Implicit repeat of the previous command. After M/m, repeats are L/l.
            if last_cmd is None:
                raise ValueError(f"path data starts with a number: {d!r}")
            if last_cmd == "M":
                cmd = "L"
            elif last_cmd == "m":
                cmd = "l"
            else:
                cmd = last_cmd

        if cmd in ("M", "m"):
            (x, y), i = _take_floats(tokens, i, 2)
            if cmd == "m" and commands:
                x += cur_x
                y += cur_y
            cur_x, cur_y = x, y
            start_x, start_y = x, y
            commands.append(("moveTo", x, y))
            last_cubic_ctrl = None
            last_quad_ctrl = None

        elif cmd in ("L", "l"):
            (x, y), i = _take_floats(tokens, i, 2)
            if cmd == "l":
                x += cur_x
                y += cur_y
            cur_x, cur_y = x, y
            commands.append(("lineTo", x, y))
            last_cubic_ctrl = None
            last_quad_ctrl = None

        elif cmd in ("H", "h"):
            (x,), i = _take_floats(tokens, i, 1)
            if cmd == "h":
                x += cur_x
            cur_x = x
            commands.append(("lineTo", cur_x, cur_y))
            last_cubic_ctrl = None
            last_quad_ctrl = None

        elif cmd in ("V", "v"):
            (y,), i = _take_floats(tokens, i, 1)
            if cmd == "v":
                y += cur_y
            cur_y = y
            commands.append(("lineTo", cur_x, cur_y))
            last_cubic_ctrl = None
            last_quad_ctrl = None

        elif cmd in ("C", "c"):
            (c1x, c1y, c2x, c2y, x, y), i = _take_floats(tokens, i, 6)
            if cmd == "c":
                c1x += cur_x
                c1y += cur_y
                c2x += cur_x
                c2y += cur_y
                x += cur_x
                y += cur_y
            commands.append(("cubicBezTo", c1x, c1y, c2x, c2y, x, y))
            cur_x, cur_y = x, y
            last_cubic_ctrl = (c2x, c2y)
            last_quad_ctrl = None

        elif cmd in ("S", "s"):
            (c2x, c2y, x, y), i = _take_floats(tokens, i, 4)
            if cmd == "s":
                c2x += cur_x
                c2y += cur_y
                x += cur_x
                y += cur_y
            # Reflect previous cubic control2 about the current point; if the
            # previous command wasn't C/c/S/s, the first control coincides with
            # the current point (per SVG spec).
            if last_cubic_ctrl is not None:
                c1x = 2 * cur_x - last_cubic_ctrl[0]
                c1y = 2 * cur_y - last_cubic_ctrl[1]
            else:
                c1x, c1y = cur_x, cur_y
            commands.append(("cubicBezTo", c1x, c1y, c2x, c2y, x, y))
            cur_x, cur_y = x, y
            last_cubic_ctrl = (c2x, c2y)
            last_quad_ctrl = None

        elif cmd in ("Q", "q"):
            (cx, cy, x, y), i = _take_floats(tokens, i, 4)
            if cmd == "q":
                cx += cur_x
                cy += cur_y
                x += cur_x
                y += cur_y
            commands.append(("quadBezTo", cx, cy, x, y))
            cur_x, cur_y = x, y
            last_quad_ctrl = (cx, cy)
            last_cubic_ctrl = None

        elif cmd in ("T", "t"):
            (x, y), i = _take_floats(tokens, i, 2)
            if cmd == "t":
                x += cur_x
                y += cur_y
            # Reflect previous quad control about current point; otherwise the
            # control coincides with the current point.
            if last_quad_ctrl is not None:
                cx = 2 * cur_x - last_quad_ctrl[0]
                cy = 2 * cur_y - last_quad_ctrl[1]
            else:
                cx, cy = cur_x, cur_y
            commands.append(("quadBezTo", cx, cy, x, y))
            cur_x, cur_y = x, y
            last_quad_ctrl = (cx, cy)
            last_cubic_ctrl = None

        elif cmd in ("A", "a"):
            # Arc payload: rx ry x-axis-rotation large-arc sweep x y.
            (rx, ry, rot, laf, sf, x, y), i = _take_floats(tokens, i, 7)
            if cmd == "a":
                x += cur_x
                y += cur_y
            for seg in _arc_to_cubics(
                cur_x, cur_y, rx, ry, rot, laf != 0.0, sf != 0.0, x, y
            ):
                commands.append(("cubicBezTo", *seg))
            cur_x, cur_y = x, y
            last_cubic_ctrl = None
            last_quad_ctrl = None

        elif cmd in ("Z", "z"):
            commands.append(("close",))
            cur_x, cur_y = start_x, start_y
            last_cubic_ctrl = None
            last_quad_ctrl = None

        else:
            raise ValueError(f"unsupported path command: {cmd!r}")

        last_cmd = cmd

    return commands


def _pt(x: float, y: float) -> str:
    return f'<a:pt x="{int(round(x))}" y="{int(round(y))}"/>'


def commands_to_path_xml(commands: list[PathCommand], width: int, height: int) -> str:
    """Render a parsed command list as an OOXML `<a:path>` element body.

    Coordinates are emitted as integers in the path coordinate space defined
    by `width`/`height`; the caller is responsible for scaling user units.
    """
    parts: list[str] = [f'<a:path w="{width}" h="{height}">']
    for cmd in commands:
        op = cmd[0]
        if op == "moveTo":
            _, x, y = cmd
            parts.append(f"<a:moveTo>{_pt(x, y)}</a:moveTo>")
        elif op == "lineTo":
            _, x, y = cmd
            parts.append(f"<a:lnTo>{_pt(x, y)}</a:lnTo>")
        elif op == "cubicBezTo":
            _, c1x, c1y, c2x, c2y, x, y = cmd
            parts.append(
                "<a:cubicBezTo>"
                + _pt(c1x, c1y)
                + _pt(c2x, c2y)
                + _pt(x, y)
                + "</a:cubicBezTo>"
            )
        elif op == "quadBezTo":
            _, cx, cy, x, y = cmd
            parts.append("<a:quadBezTo>" + _pt(cx, cy) + _pt(x, y) + "</a:quadBezTo>")
        elif op == "close":
            parts.append("<a:close/>")
    parts.append("</a:path>")
    return "".join(parts)


def path_to_custgeom_xml(d: str, width: int = 100, height: int = 100) -> str:
    """Convert an SVG path `d` string to a complete `<a:custGeom>` element."""
    commands = parse_path(d)
    body = commands_to_path_xml(commands, width, height)
    return (
        "<a:custGeom>"
        "<a:avLst/><a:gdLst/><a:ahLst/><a:cxnLst/>"
        f'<a:rect l="0" t="0" r="{width}" b="{height}"/>'
        f"<a:pathLst>{body}</a:pathLst>"
        "</a:custGeom>"
    )
