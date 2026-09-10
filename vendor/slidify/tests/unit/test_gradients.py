"""Tests for slidify.gradients — linear/radial parsing + OOXML emission."""

from __future__ import annotations

from slidify.gradients import (
    LinearGradient,
    RadialGradient,
    parse_gradient,
    to_grad_fill_xml,
)

# ---- Parsing -----------------------------------------------------------------


def test_linear_gradient_with_angle():
    g = parse_gradient("linear-gradient(135deg, rgb(99,102,241) 0%, rgb(236,72,153) 100%)")
    assert isinstance(g, LinearGradient)
    assert g.angle_deg == 135
    assert len(g.stops) == 2
    assert g.stops[0].color_hex == "6366F1"
    assert g.stops[-1].color_hex == "EC4899"


def test_linear_gradient_implicit_to_bottom():
    g = parse_gradient("linear-gradient(rgb(255,255,255), rgb(0,0,0))")
    assert isinstance(g, LinearGradient)
    assert g.angle_deg == 180  # CSS default 'to bottom'


def test_linear_to_direction():
    g = parse_gradient("linear-gradient(to right, rgb(0,0,0), rgb(255,255,255))")
    assert isinstance(g, LinearGradient)
    assert g.angle_deg == 90


def test_radial_gradient_default_center():
    g = parse_gradient(
        "radial-gradient(rgba(0,0,0,1) 0%, rgba(255,255,255,1) 100%)"
    )
    assert isinstance(g, RadialGradient)
    assert g.cx == 0.5
    assert g.cy == 0.5
    assert g.shape == "ellipse"


def test_radial_gradient_at_percent():
    g = parse_gradient(
        "radial-gradient(circle at 78% 18%, rgb(30,27,75) 0%, rgb(10,10,15) 60%)"
    )
    assert isinstance(g, RadialGradient)
    assert abs(g.cx - 0.78) < 1e-6
    assert abs(g.cy - 0.18) < 1e-6
    assert g.shape == "circle"


def test_radial_gradient_at_keyword():
    g = parse_gradient(
        "radial-gradient(ellipse at top right, rgb(0,0,0), rgb(255,255,255))"
    )
    assert isinstance(g, RadialGradient)
    assert g.cx == 1.0  # right
    assert g.cy == 0.0  # top


def test_non_gradient_returns_none():
    assert parse_gradient("none") is None
    assert parse_gradient("") is None
    assert parse_gradient("url(./bg.png)") is None


def test_radial_with_transparent_stop_preserves_alpha_zero():
    """Aurora-style decoration: radial-gradient(... transparent 70%).
    The transparent stop must be preserved as alpha=0 so the gradient fades
    to transparent — not held at the previous color's alpha. The parser
    also extends the last stop to position 1.0 (LibreOffice doesn't
    extrapolate past the last stop's position)."""
    g = parse_gradient(
        "radial-gradient(circle, rgba(139,92,246,0.32) 0%, rgba(99,102,241,0.18) 35%, transparent 70%)"
    )
    assert isinstance(g, RadialGradient)
    # 3 user stops + 1 auto-extended terminal stop = 4
    assert len(g.stops) == 4
    assert abs(g.stops[0].alpha - 0.32) < 1e-6
    assert abs(g.stops[1].alpha - 0.18) < 1e-6
    assert g.stops[2].alpha == 0.0  # original transparent stop at 70%
    assert g.stops[3].alpha == 0.0  # duplicated transparent stop at 100%
    assert g.stops[3].position == 1.0
    # Transparent stop's color hex is a legal hex (we inherit the previous
    # stop's hex for cleaner interpolation).
    assert len(g.stops[2].color_hex) == 6


def test_linear_with_transparent_endpoint():
    g = parse_gradient(
        "linear-gradient(90deg, rgb(255,0,0), transparent)"
    )
    assert isinstance(g, LinearGradient)
    assert len(g.stops) == 2
    assert g.stops[0].alpha == 1.0
    assert g.stops[1].alpha == 0.0


def test_transparent_in_computed_form_rgba_zero_alpha():
    """Chromium normalizes `transparent` to `rgba(0, 0, 0, 0)` in the
    computed style. The parser must recognize this form too — otherwise
    the aurora-style fade-to-transparent silently loses its last stop."""
    g = parse_gradient(
        "radial-gradient(circle, rgba(139,92,246,0.32) 0%, rgba(0, 0, 0, 0) 70%)"
    )
    assert isinstance(g, RadialGradient)
    # 2 user stops + 1 auto-extended terminal stop = 3
    assert len(g.stops) == 3
    assert abs(g.stops[0].alpha - 0.32) < 1e-6
    assert g.stops[1].alpha == 0.0  # the rgba(...,0) was recognized
    assert g.stops[2].alpha == 0.0
    assert g.stops[2].position == 1.0


# ---- OOXML emission ---------------------------------------------------------


def test_radial_emits_filltorect_at_center():
    g = parse_gradient(
        "radial-gradient(circle at 78% 18%, rgb(30,27,75) 0%, rgb(10,10,15) 100%)"
    )
    fill = to_grad_fill_xml(g)
    # Find the <a:fillToRect>
    NS_A = "http://schemas.openxmlformats.org/drawingml/2006/main"
    rect = fill.find(f".//{{{NS_A}}}fillToRect")
    assert rect is not None
    # cx=0.78 → l ≈ 78000; r = (1-0.78)*100000 ≈ 22000.
    assert int(rect.get("l")) == 78000
    assert int(rect.get("t")) == 18000
    assert int(rect.get("r")) == 22000
    assert int(rect.get("b")) == 82000


def test_linear_emits_lin_with_pptx_angle():
    g = parse_gradient("linear-gradient(135deg, #000, #fff)")
    fill = to_grad_fill_xml(g)
    NS_A = "http://schemas.openxmlformats.org/drawingml/2006/main"
    lin = fill.find(f".//{{{NS_A}}}lin")
    assert lin is not None
    # pptx_deg = (135 - 90) % 360 = 45 → 45 * 60_000 = 2_700_000
    assert int(lin.get("ang")) == 2_700_000
