"""Mesh-gradient simulator: stacked translucent radial-fill shapes.

PPTX has no native mesh-gradient primitive. To emulate the "Stripe / Vercel /
Linear / Anthropic" hero glow, we paint a small stack (3–5) of large
translucent radial-gradient rectangles that fade from a brand color at
their center to alpha=0 at the edge. With low peak alpha and overlapping
positions the result reads as a single continuous mesh.

The module only emits BACKGROUND DECORATION shapes — content shapes are
the caller's responsibility and are painted on top of these.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from pptx.enum.shapes import MSO_SHAPE

from slidify.gradients import (
    GradientStop,
    RadialGradient,
    apply_gradient_fill,
)
from slidify.models import BoundingBox

__all__ = [
    "GlowBlob",
    "MeshSpec",
    "derive_mesh_from_palette",
    "emit_mesh",
]


@dataclass
class GlowBlob:
    color_hex: str       # 6-hex no leading '#'
    cx_pct: float        # 0..1, center X as fraction of bbox width
    cy_pct: float        # 0..1, center Y as fraction of bbox height
    radius_pct: float    # 0..1.5 (can extend past edge), of max(bbox.w, bbox.h)
    alpha_max: float     # 0..1, peak alpha at center; falls to 0 at edge


@dataclass
class MeshSpec:
    blobs: list[GlowBlob] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _normalize_hex(color: str) -> str:
    """Strip leading '#', upper-case, and pad/truncate to 6 hex chars."""
    s = color.strip().lstrip("#").upper()
    if len(s) == 3:
        # CSS shorthand `#abc` → `AABBCC`.
        s = "".join(c * 2 for c in s)
    return s[:6].rjust(6, "0")


def _emu(value: float) -> int:
    """1 px ≈ 9525 EMU. Caller's bbox uses px-equivalent units."""
    return int(round(value * 9525))


def _blob_to_radial_gradient(blob: GlowBlob) -> RadialGradient:
    """Build a 2-stop radial gradient: color@alpha at center → color@0 at edge.

    The gradient is rendered into a square shape sized 2*radius wide so the
    edge of the square equals the edge of the glow — the radial fill goes
    from focal-point-at-center to fully-transparent at the shape boundary.
    """
    color = _normalize_hex(blob.color_hex)
    alpha = max(0.0, min(1.0, blob.alpha_max))
    return RadialGradient(
        stops=[
            GradientStop(color_hex=color, alpha=alpha, position=0.0),
            GradientStop(color_hex=color, alpha=0.0, position=1.0),
        ],
        cx=0.5,
        cy=0.5,
        shape="circle",
    )


# ---------------------------------------------------------------------------
# Emission
# ---------------------------------------------------------------------------


def emit_mesh(slide, bbox: BoundingBox, mesh: MeshSpec) -> list:
    """Emit each blob as a radial-gradient-filled rectangle on the slide.

    Returns the list of created Shape objects (in z-order, bottom first).
    Each blob becomes a shape sized at (cx ± radius, cy ± radius), filled
    with a radial gradient from (color_hex, alpha_max) at center to
    (color_hex, alpha=0) at edge, with no stroke.
    """
    shapes: list = []
    if not mesh.blobs:
        return shapes

    # Radius is expressed as a fraction of the bbox's longer side so that
    # the same MeshSpec retains visual proportions across slide aspects.
    base = max(bbox.w, bbox.h)

    for blob in mesh.blobs:
        radius_px = max(0.0, blob.radius_pct) * base
        # Square shape inscribing the circular glow; the radial gradient
        # itself fades to alpha=0 at the rectangle's edge so the bounding
        # box never produces a hard square outline.
        cx_px = bbox.x + blob.cx_pct * bbox.w
        cy_px = bbox.y + blob.cy_pct * bbox.h
        x_px = cx_px - radius_px
        y_px = cy_px - radius_px
        side_px = radius_px * 2

        shape = slide.shapes.add_shape(
            MSO_SHAPE.RECTANGLE,
            _emu(x_px),
            _emu(y_px),
            _emu(side_px),
            _emu(side_px),
        )
        # No stroke — the rectangle is purely a carrier for the radial fill.
        try:
            shape.line.fill.background()
        except Exception:
            pass
        apply_gradient_fill(shape, _blob_to_radial_gradient(blob))
        shapes.append(shape)

    return shapes


# ---------------------------------------------------------------------------
# Palette → MeshSpec presets
# ---------------------------------------------------------------------------


def _pick(palette: list[str], idx: int) -> str:
    """Wrap-around indexing so short palettes still produce valid blobs.

    Returns a normalized 6-hex string (no leading '#', uppercase) so that
    callers can splice the value directly into OOXML <a:srgbClr val=...>.
    """
    if not palette:
        return "888888"
    return _normalize_hex(palette[idx % len(palette)])


def derive_mesh_from_palette(
    palette: list[str], style: str = "hero"
) -> MeshSpec:
    """Build a MeshSpec from a brand palette (list of '#RRGGBB' hex strings).

    Styles:
      'hero'      — 4 corner blobs (TL, TR, BL, BR) using palette[0..3]; classic Linear/Vercel
      'spotlight' — 1 huge centered blob from palette[0], for dramatic CTA backgrounds
      'aurora'    — 3 horizontal-band blobs across top/middle/bottom, like northern-lights
      'orbit'     — 5 blobs arranged in an off-center elliptical orbit
    """
    s = style.lower()
    if s == "spotlight":
        return MeshSpec(
            blobs=[
                GlowBlob(
                    color_hex=_pick(palette, 0),
                    cx_pct=0.5,
                    cy_pct=0.5,
                    radius_pct=0.95,
                    alpha_max=0.55,
                ),
            ]
        )

    if s == "aurora":
        # Three horizontal bands at varying y positions; wide and soft.
        return MeshSpec(
            blobs=[
                GlowBlob(
                    color_hex=_pick(palette, 0),
                    cx_pct=0.25,
                    cy_pct=0.18,
                    radius_pct=0.90,
                    alpha_max=0.40,
                ),
                GlowBlob(
                    color_hex=_pick(palette, 1),
                    cx_pct=0.55,
                    cy_pct=0.50,
                    radius_pct=0.95,
                    alpha_max=0.35,
                ),
                GlowBlob(
                    color_hex=_pick(palette, 2),
                    cx_pct=0.78,
                    cy_pct=0.82,
                    radius_pct=0.90,
                    alpha_max=0.40,
                ),
            ]
        )

    if s == "orbit":
        # Five blobs around an off-center ellipse — the focal lobe is
        # placed slightly upper-left of true center for visual weight.
        cx, cy = 0.42, 0.46
        rx, ry = 0.32, 0.26
        # Angles in degrees, hand-picked to feel non-mechanical.
        positions = [
            (cx, cy),
            (cx + rx, cy - ry * 0.3),
            (cx - rx * 0.8, cy + ry * 0.4),
            (cx + rx * 0.5, cy + ry),
            (cx - rx * 0.6, cy - ry),
        ]
        return MeshSpec(
            blobs=[
                GlowBlob(
                    color_hex=_pick(palette, i),
                    cx_pct=max(0.0, min(1.0, px)),
                    cy_pct=max(0.0, min(1.0, py)),
                    # The central lobe is largest; satellites a touch smaller.
                    radius_pct=0.65 if i == 0 else 0.50,
                    alpha_max=0.45 if i == 0 else 0.35,
                )
                for i, (px, py) in enumerate(positions)
            ]
        )

    # Default: 'hero' — 4 corner blobs (TL, TR, BL, BR).
    # Slightly off the corners so the gradient peak sits inside the canvas
    # instead of clipping off-screen.
    corners = [
        (0.10, 0.15),  # TL
        (0.90, 0.15),  # TR
        (0.10, 0.85),  # BL
        (0.90, 0.85),  # BR
    ]
    return MeshSpec(
        blobs=[
            GlowBlob(
                color_hex=_pick(palette, i),
                cx_pct=cx,
                cy_pct=cy,
                radius_pct=0.70,
                alpha_max=0.45,
            )
            for i, (cx, cy) in enumerate(corners)
        ]
    )
