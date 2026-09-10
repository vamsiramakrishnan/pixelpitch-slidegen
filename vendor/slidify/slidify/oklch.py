"""sRGB ↔ OKLCH color space conversion + gradient stop densification.

PowerPoint renders gradients in sRGB-linear space, which produces muddy
mid-stops when authors intended a perceptually-uniform interpolation
(e.g. ``indigo-500 → pink-500`` collapses through grey at t=0.5 in sRGB,
but stays a vibrant purple in OKLCH).

We work around the renderer by pre-densifying stops in OKLCH and emitting
the intermediates as ordinary sRGB stops. The renderer's piecewise-linear
interpolation between many close stops then closely approximates the
perceptually-uniform curve.

The OKLab math here is the standard one published by Björn Ottosson:
    https://bottosson.github.io/posts/oklab/
"""

from __future__ import annotations

import math

from slidify.gradients import GradientStop

__all__ = [
    "densify_stops",
    "hex_to_oklch",
    "lerp_oklch",
    "oklch_to_hex",
    "oklch_to_srgb",
    "srgb_to_oklch",
]


# ---------------------------------------------------------------------------
# sRGB ↔ linear sRGB (gamma)
# ---------------------------------------------------------------------------


def _srgb_to_linear(c: float) -> float:
    """Standard sRGB → linear gamma decode. Input/output in [0, 1]."""
    if c <= 0.04045:
        return c / 12.92
    return ((c + 0.055) / 1.055) ** 2.4


def _linear_to_srgb(c: float) -> float:
    """Standard linear → sRGB gamma encode. Input/output in [0, 1]."""
    if c <= 0.0031308:
        return 12.92 * c
    return 1.055 * (c ** (1.0 / 2.4)) - 0.055


# ---------------------------------------------------------------------------
# Linear sRGB ↔ OKLab (Ottosson's matrix)
# ---------------------------------------------------------------------------


def _linear_srgb_to_oklab(r: float, g: float, b: float) -> tuple[float, float, float]:
    l = 0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b
    m = 0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b
    s = 0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b

    l_ = l ** (1.0 / 3.0) if l >= 0 else -((-l) ** (1.0 / 3.0))
    m_ = m ** (1.0 / 3.0) if m >= 0 else -((-m) ** (1.0 / 3.0))
    s_ = s ** (1.0 / 3.0) if s >= 0 else -((-s) ** (1.0 / 3.0))

    L = 0.2104542553 * l_ + 0.7936177850 * m_ - 0.0040720468 * s_
    a = 1.9779984951 * l_ - 2.4285922050 * m_ + 0.4505937099 * s_
    b_ax = 0.0259040371 * l_ + 0.7827717662 * m_ - 0.8086757660 * s_
    return L, a, b_ax


def _oklab_to_linear_srgb(L: float, a: float, b: float) -> tuple[float, float, float]:
    l_ = L + 0.3963377774 * a + 0.2158037573 * b
    m_ = L - 0.1055613458 * a - 0.0638541728 * b
    s_ = L - 0.0894841775 * a - 1.2914855480 * b

    l = l_ * l_ * l_
    m = m_ * m_ * m_
    s = s_ * s_ * s_

    r = +4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s
    g = -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s
    bb = -0.0041960863 * l - 0.7034186147 * m + 1.7076147010 * s
    return r, g, bb


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def srgb_to_oklch(r: int, g: int, b: int) -> tuple[float, float, float]:
    """Convert sRGB (0..255 each channel) to OKLCH.

    Returns (L, C, H) where L in [0, 1], C in [0, ~0.4], H in [0, 360).
    """
    rl = _srgb_to_linear(r / 255.0)
    gl = _srgb_to_linear(g / 255.0)
    bl = _srgb_to_linear(b / 255.0)
    L, a, b_ax = _linear_srgb_to_oklab(rl, gl, bl)
    C = math.sqrt(a * a + b_ax * b_ax)
    if C < 1e-7:
        # Achromatic — hue is undefined; use 0 for stable round-trips.
        H = 0.0
    else:
        H = math.degrees(math.atan2(b_ax, a)) % 360.0
    return L, C, H


def oklch_to_srgb(L: float, C: float, H: float) -> tuple[int, int, int]:
    """Inverse of :func:`srgb_to_oklch` with sRGB-gamut clamping.

    Each linear channel is clamped to [0, 1] before applying the gamma
    encode — we do not attempt gamut-aware chroma reduction; out-of-gamut
    OKLCH values therefore lose some chroma rather than shifting hue
    drastically. This is sufficient for densifying stops that started in
    sRGB to begin with (and so are guaranteed in-gamut).
    """
    h_rad = math.radians(H)
    a = C * math.cos(h_rad)
    b_ax = C * math.sin(h_rad)
    rl, gl, bl = _oklab_to_linear_srgb(L, a, b_ax)
    rl = max(0.0, min(1.0, rl))
    gl = max(0.0, min(1.0, gl))
    bl = max(0.0, min(1.0, bl))
    r = int(round(_linear_to_srgb(rl) * 255.0))
    g = int(round(_linear_to_srgb(gl) * 255.0))
    b = int(round(_linear_to_srgb(bl) * 255.0))
    return (
        max(0, min(255, r)),
        max(0, min(255, g)),
        max(0, min(255, b)),
    )


def hex_to_oklch(hex_str: str) -> tuple[float, float, float]:
    """Parse '#6366f1' or '6366f1' → (L, C, H)."""
    s = hex_str.strip().lstrip("#")
    if len(s) != 6:
        raise ValueError(f"expected 6-hex color, got {hex_str!r}")
    r = int(s[0:2], 16)
    g = int(s[2:4], 16)
    b = int(s[4:6], 16)
    return srgb_to_oklch(r, g, b)


def oklch_to_hex(L: float, C: float, H: float) -> str:
    """(L, C, H) → '6366f1' (lowercase, no leading '#')."""
    r, g, b = oklch_to_srgb(L, C, H)
    return f"{r:02x}{g:02x}{b:02x}"


def lerp_oklch(
    a: tuple[float, float, float],
    b: tuple[float, float, float],
    t: float,
) -> tuple[float, float, float]:
    """Interpolate L and C linearly; H along the shorter arc.

    Handles the 350°→10° wrap by walking through 0° rather than the long
    way around. Returns hue normalized to [0, 360).
    """
    L1, C1, H1 = a
    L2, C2, H2 = b
    L = L1 + (L2 - L1) * t
    C = C1 + (C2 - C1) * t

    # Shortest hue path. Difference normalized to (-180, 180].
    dh = (H2 - H1) % 360.0
    if dh > 180.0:
        dh -= 360.0
    H = (H1 + dh * t) % 360.0
    return L, C, H


def densify_stops(
    stops: list[GradientStop],
    n_intermediate: int = 5,
) -> list[GradientStop]:
    """Insert ``n_intermediate`` perceptually-uniform stops between every
    adjacent pair of original stops.

    Original stops are preserved. Insertion is skipped between adjacent
    stops whose colors are identical (same hex + alpha). Returns a NEW
    list of :class:`GradientStop`.
    """
    if n_intermediate <= 0 or len(stops) < 2:
        return [
            GradientStop(color_hex=s.color_hex, alpha=s.alpha, position=s.position)
            for s in stops
        ]

    out: list[GradientStop] = []
    for i, current in enumerate(stops):
        # Always emit the original stop (a fresh copy).
        out.append(
            GradientStop(
                color_hex=current.color_hex,
                alpha=current.alpha,
                position=current.position,
            )
        )
        if i == len(stops) - 1:
            break
        nxt = stops[i + 1]
        # Skip insertion when both endpoints are visually identical.
        if (
            current.color_hex.lower() == nxt.color_hex.lower()
            and abs(current.alpha - nxt.alpha) < 1e-6
        ):
            continue

        a_lch = hex_to_oklch(current.color_hex)
        b_lch = hex_to_oklch(nxt.color_hex)
        for k in range(1, n_intermediate + 1):
            t = k / (n_intermediate + 1)
            L, C, H = lerp_oklch(a_lch, b_lch, t)
            mid_hex = oklch_to_hex(L, C, H).upper()
            mid_alpha = current.alpha + (nxt.alpha - current.alpha) * t
            mid_pos = current.position + (nxt.position - current.position) * t
            out.append(
                GradientStop(
                    color_hex=mid_hex,
                    alpha=mid_alpha,
                    position=mid_pos,
                )
            )
    return out
