"""Tests for slidify.oklch — sRGB↔OKLCH conversion + perceptual densification."""

from __future__ import annotations

from slidify.gradients import GradientStop, LinearGradient, with_oklch_density
from slidify.oklch import (
    densify_stops,
    hex_to_oklch,
    lerp_oklch,
    oklch_to_hex,
    oklch_to_srgb,
    srgb_to_oklch,
)


# ---------------------------------------------------------------------------
# Round-trip fidelity
# ---------------------------------------------------------------------------


def _hex_close(a: str, b: str, tol: int = 1) -> bool:
    """True if every channel of two hex colors is within ``tol`` LSBs."""
    a = a.lstrip("#").lower()
    b = b.lstrip("#").lower()
    for i in (0, 2, 4):
        if abs(int(a[i : i + 2], 16) - int(b[i : i + 2], 16)) > tol:
            return False
    return True


def test_round_trip_indigo_500():
    # Tailwind's indigo-500 — the canonical "is OKLCH math correct" probe.
    assert _hex_close(oklch_to_hex(*hex_to_oklch("6366f1")), "6366f1")


def test_round_trip_pure_red():
    assert _hex_close(oklch_to_hex(*hex_to_oklch("ff0000")), "ff0000")


def test_round_trip_pure_black():
    assert _hex_close(oklch_to_hex(*hex_to_oklch("000000")), "000000")


def test_round_trip_pure_white():
    assert _hex_close(oklch_to_hex(*hex_to_oklch("ffffff")), "ffffff")


def test_round_trip_via_srgb_tuple():
    # Belt-and-braces: also exercise the int-tuple form.
    L, C, H = srgb_to_oklch(99, 102, 241)
    r, g, b = oklch_to_srgb(L, C, H)
    assert (r, g, b) == (99, 102, 241)


# ---------------------------------------------------------------------------
# Perceptual midpoint quality
# ---------------------------------------------------------------------------


def test_indigo_pink_midpoint_stays_chromatic():
    """Indigo→pink in OKLCH should NOT pass through grey at t=0.5.

    sRGB-linear interpolation collapses these two saturated colors through
    a desaturated mauve (chroma ~0.03 in OKLCH terms). The OKLCH midpoint
    should preserve substantial chroma — we assert > 0.05 to leave plenty
    of headroom for floating-point wiggle.
    """
    a = hex_to_oklch("818cf8")  # indigo-400
    b = hex_to_oklch("f472b6")  # pink-400
    L, C, H = lerp_oklch(a, b, 0.5)
    assert C > 0.05, f"midpoint chroma {C:.4f} too low — looks grey"
    # Sanity: the midpoint should also be a valid round-tripping color.
    mid_hex = oklch_to_hex(L, C, H)
    assert len(mid_hex) == 6


def test_hue_lerp_takes_short_arc():
    """350° → 10° should pass through 0°, NOT 180°."""
    L, C, H = lerp_oklch((0.5, 0.1, 350.0), (0.5, 0.1, 10.0), 0.5)
    # Allowed answers: ~0° or ~360°. Forbid 180°.
    short_arc = min(H, 360.0 - H)
    assert short_arc < 1.0, f"expected H near 0/360, got {H}"


def test_hue_lerp_takes_short_arc_reverse():
    """Symmetric: 10° → 350° should also use the short arc through 0°."""
    L, C, H = lerp_oklch((0.5, 0.1, 10.0), (0.5, 0.1, 350.0), 0.5)
    short_arc = min(H, 360.0 - H)
    assert short_arc < 1.0, f"expected H near 0/360, got {H}"


# ---------------------------------------------------------------------------
# Stop densification
# ---------------------------------------------------------------------------


def test_densify_stops_inserts_evenly_spaced():
    stops = [
        GradientStop(color_hex="FF0000", alpha=1.0, position=0.0),
        GradientStop(color_hex="00FF00", alpha=1.0, position=1.0),
    ]
    out = densify_stops(stops, n_intermediate=3)
    assert len(out) == 5
    positions = [round(s.position, 4) for s in out]
    assert positions == [0.0, 0.25, 0.5, 0.75, 1.0]
    # Endpoints preserved exactly.
    assert out[0].color_hex == "FF0000"
    assert out[-1].color_hex == "00FF00"


def test_densify_stops_skips_identical_neighbors():
    stops = [
        GradientStop(color_hex="FF0000", alpha=1.0, position=0.0),
        GradientStop(color_hex="FF0000", alpha=1.0, position=0.5),
        GradientStop(color_hex="00FF00", alpha=1.0, position=1.0),
    ]
    out = densify_stops(stops, n_intermediate=3)
    # First gap (red→red) skipped; second gap (red→green) gets 3 inserts.
    assert len(out) == 3 + 3  # 3 originals + 3 inserts
    # The first three stops should be the originals up to position=0.5.
    assert out[0].position == 0.0
    assert out[1].position == 0.5
    assert out[1].color_hex == "FF0000"


def test_densify_stops_zero_intermediate_is_copy():
    stops = [
        GradientStop(color_hex="FF0000", alpha=1.0, position=0.0),
        GradientStop(color_hex="00FF00", alpha=1.0, position=1.0),
    ]
    out = densify_stops(stops, n_intermediate=0)
    assert len(out) == 2
    # Must be a fresh list (not aliasing).
    assert out is not stops
    assert out[0] is not stops[0]


# ---------------------------------------------------------------------------
# gradients.with_oklch_density wrapper
# ---------------------------------------------------------------------------


def test_with_oklch_density_linear_preserves_angle():
    grad = LinearGradient(
        angle_deg=135.0,
        stops=[
            GradientStop(color_hex="6366F1", alpha=1.0, position=0.0),
            GradientStop(color_hex="EC4899", alpha=1.0, position=1.0),
        ],
    )
    out = with_oklch_density(grad, n=5)
    assert isinstance(out, LinearGradient)
    assert out.angle_deg == 135.0
    assert len(out.stops) == 2 + 5
    # Original input unchanged.
    assert len(grad.stops) == 2


def test_with_oklch_density_radial_preserves_geometry():
    from slidify.gradients import RadialGradient

    grad = RadialGradient(
        stops=[
            GradientStop(color_hex="6366F1", alpha=1.0, position=0.0),
            GradientStop(color_hex="EC4899", alpha=1.0, position=1.0),
        ],
        cx=0.78,
        cy=0.18,
        shape="circle",
    )
    out = with_oklch_density(grad, n=4)
    assert isinstance(out, RadialGradient)
    assert out.cx == 0.78
    assert out.cy == 0.18
    assert out.shape == "circle"
    assert len(out.stops) == 2 + 4
