"""Tests for SVG path curve commands (C/c, S/s, Q/q, T/t, A/a) in slidify.svg_shapes."""

from __future__ import annotations

import math

from slidify.svg_path import commands_to_path_xml, parse_path


def test_cubic_bezier_absolute():
    """`M 10 10 C 20 20, 40 20, 50 10` → 1 moveTo + 1 cubicBezTo."""
    cmds = parse_path("M 10 10 C 20 20, 40 20, 50 10")
    assert cmds == [
        ("moveTo", 10.0, 10.0),
        ("cubicBezTo", 20.0, 20.0, 40.0, 20.0, 50.0, 10.0),
    ]


def test_smooth_cubic_reflects_previous_control():
    """After `C ... 40 20, 50 10`, an `S 80 0, 90 10` segment must reflect
    the prior cubic's second control about the current point: the reflected
    first control is (50 + (50-40), 10 + (10-20)) = (60, 0)."""
    cmds = parse_path("M 10 10 C 20 20, 40 20, 50 10 S 80 0, 90 10")
    assert cmds == [
        ("moveTo", 10.0, 10.0),
        ("cubicBezTo", 20.0, 20.0, 40.0, 20.0, 50.0, 10.0),
        ("cubicBezTo", 60.0, 0.0, 80.0, 0.0, 90.0, 10.0),
    ]


def test_quadratic_bezier_absolute():
    """`M 10 10 Q 25 25, 40 10` → 1 moveTo + 1 quadBezTo."""
    cmds = parse_path("M 10 10 Q 25 25, 40 10")
    assert cmds == [
        ("moveTo", 10.0, 10.0),
        ("quadBezTo", 25.0, 25.0, 40.0, 10.0),
    ]


def test_relative_cubic_advances_current_point():
    """`M 10 10 c 10 10, 30 10, 40 0` → endpoint (50, 10), control points
    are absolute (20,20) and (40,20)."""
    cmds = parse_path("M 10 10 c 10 10, 30 10, 40 0")
    assert cmds == [
        ("moveTo", 10.0, 10.0),
        ("cubicBezTo", 20.0, 20.0, 40.0, 20.0, 50.0, 10.0),
    ]


# --- Arc commands (A / a) ---------------------------------------------------


def _bezier_point(c1, c2, p0, p1, t):
    """Evaluate a cubic Bezier at t given start p0 and end p1."""
    u = 1.0 - t
    return (
        u**3 * p0[0] + 3 * u**2 * t * c1[0] + 3 * u * t**2 * c2[0] + t**3 * p1[0],
        u**3 * p0[1] + 3 * u**2 * t * c1[1] + 3 * u * t**2 * c2[1] + t**3 * p1[1],
    )


def _walk_cubics(cmds):
    """Yield (p0, c1, c2, p1) for every cubic in the command list."""
    cur = (0.0, 0.0)
    for cmd in cmds:
        op = cmd[0]
        if op == "moveTo":
            cur = (cmd[1], cmd[2])
        elif op == "lineTo":
            cur = (cmd[1], cmd[2])
        elif op == "cubicBezTo":
            _, c1x, c1y, c2x, c2y, x, y = cmd
            yield (cur, (c1x, c1y), (c2x, c2y), (x, y))
            cur = (x, y)
        elif op == "quadBezTo":
            cur = (cmd[3], cmd[4])


def test_arc_quarter_circle_emits_one_cubic():
    """`M 100 0 A 100 100 0 0 0 0 100` traces a 90° CCW arc from (100,0)
    to (0,100) on the unit circle of radius 100. One cubic suffices."""
    cmds = parse_path("M 100 0 A 100 100 0 0 0 0 100")
    cubics = list(_walk_cubics(cmds))
    assert len(cubics) == 1
    p0, _, _, p1 = cubics[0]
    assert p0 == (100.0, 0.0)
    assert math.isclose(p1[0], 0.0, abs_tol=1e-6)
    assert math.isclose(p1[1], 100.0, abs_tol=1e-6)


def test_arc_quarter_circle_midpoint_on_circle():
    """The midpoint of the cubic approximation of a 90° arc centered at the
    origin should lie within ~0.03% of the true circle of radius 100.
    `sweep=1` selects the arc whose center is at the origin (bulging away
    from the origin to ~(70.7, 70.7))."""
    cmds = parse_path("M 100 0 A 100 100 0 0 1 0 100")
    cubics = list(_walk_cubics(cmds))
    assert len(cubics) == 1
    p0, c1, c2, p1 = cubics[0]
    mid = _bezier_point(c1, c2, p0, p1, 0.5)
    radius = math.hypot(mid[0], mid[1])
    # Cubic approximation of a 90° arc has ~0.03% peak normal error; the
    # parametric t=0.5 sample isn't the arc midpoint so the radial deviation
    # we measure here is a few tenths of a unit.
    assert abs(radius - 100.0) < 0.5


def test_arc_full_circle_splits_into_four_segments():
    """A full circle (start == end with large-arc) is encoded as two
    half-arcs in SVG. Each half-arc must split into 2 cubic segments
    (≤ π/2 each), giving 4 cubics total."""
    cmds = parse_path("M 100 0 A 100 100 0 1 0 -100 0 A 100 100 0 1 0 100 0")
    cubics = list(_walk_cubics(cmds))
    assert len(cubics) == 4
    # Every cubic endpoint sits on the radius-100 circle (within tolerance).
    for _, _, _, p1 in cubics:
        r = math.hypot(p1[0], p1[1])
        assert abs(r - 100.0) < 0.5


def test_arc_zero_radius_falls_back_to_line():
    """Per SVG F.6.2, an arc with rx=0 or ry=0 collapses to a straight line.
    We emit a degenerate cubic (control points on the chord) so the endpoint
    is reached without curvature."""
    cmds = parse_path("M 0 0 A 0 50 0 0 0 100 0")
    assert len(cmds) == 2
    assert cmds[0] == ("moveTo", 0.0, 0.0)
    assert cmds[1][0] == "cubicBezTo"
    p0 = (0.0, 0.0)
    _, c1x, c1y, c2x, c2y, x, y = cmds[1]
    # All cubic samples lie on the y=0 chord.
    for t in (0.25, 0.5, 0.75):
        _bx, by = _bezier_point((c1x, c1y), (c2x, c2y), p0, (x, y), t)
        assert math.isclose(by, 0.0, abs_tol=1e-9)
    assert math.isclose(x, 100.0, abs_tol=1e-9)


def test_arc_coincident_endpoints_omits_arc():
    """SVG spec: when the arc's endpoints are equal, the arc is treated as
    nonexistent. Subsequent commands continue from the same point."""
    cmds = parse_path("M 0 0 A 50 50 0 0 0 0 0 L 100 0")
    assert cmds == [
        ("moveTo", 0.0, 0.0),
        ("lineTo", 100.0, 0.0),
    ]


def test_arc_sweep_flag_picks_opposite_centers():
    """The two small arcs from (100,0) to (0,100) on radius 100 share their
    chord (midpoint (50,50)) but bulge to opposite sides:
      - sweep=0 selects the arc whose center is at (100,100); the midpoint
        bulges *toward* the origin to ~(29.3, 29.3).
      - sweep=1 selects the arc whose center is at (0,0); the midpoint
        bulges *away* from the origin to ~(70.7, 70.7)."""
    p0, c1, c2, p1 = next(_walk_cubics(parse_path("M 100 0 A 100 100 0 0 0 0 100")))
    mid_sweep0 = _bezier_point(c1, c2, p0, p1, 0.5)
    p0, c1, c2, p1 = next(_walk_cubics(parse_path("M 100 0 A 100 100 0 0 1 0 100")))
    mid_sweep1 = _bezier_point(c1, c2, p0, p1, 0.5)
    # Both midpoints are in (+, +); they differ by which side of the chord
    # x + y = 100 they lie on.
    assert mid_sweep0[0] + mid_sweep0[1] < 100  # toward the origin
    assert mid_sweep1[0] + mid_sweep1[1] > 100  # away from the origin
    # sweep=1 midpoint sits ~radius 100 from origin (center is at origin).
    assert abs(math.hypot(*mid_sweep1) - 100.0) < 0.5


def test_arc_relative_advances_current_point():
    """Lower-case `a` is relative to the current point."""
    cmds = parse_path("M 100 50 a 50 50 0 0 0 -50 50")
    cubics = list(_walk_cubics(cmds))
    assert len(cubics) == 1
    p0, _, _, p1 = cubics[0]
    assert p0 == (100.0, 50.0)
    assert math.isclose(p1[0], 50.0, abs_tol=1e-6)
    assert math.isclose(p1[1], 100.0, abs_tol=1e-6)


def test_arc_emits_cubicbezto_in_xml():
    """The path-XML emitter must surface arc segments as `<a:cubicBezTo>`
    elements (not lnTo, the previous fallback behavior)."""
    cmds = parse_path("M 100 0 A 100 100 0 0 0 0 100")
    xml = commands_to_path_xml(cmds, width=100, height=100)
    assert "<a:cubicBezTo>" in xml
    # No lineTo from the arc itself (the moveTo doesn't emit lnTo either).
    assert "<a:lnTo>" not in xml
