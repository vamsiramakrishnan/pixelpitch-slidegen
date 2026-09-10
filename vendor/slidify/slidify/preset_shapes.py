"""Catalog mapping CSS visual signatures to PPTX preset shapes."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

from pptx.enum.shapes import MSO_SHAPE

from slidify.models import BoundingBox, DomElement

__all__ = [
    "KNOWN_PRESETS",
    "PresetMatch",
    "classify_polygon",
    "clip_path_to_preset",
    "detect_preset_shape",
    "emit_preset",
    "parse_circle_clip_path",
    "parse_inset_clip_path",
    "parse_polygon_clip_path",
]


@dataclass
class PresetMatch:
    preset: int
    rotation_deg: float = 0.0
    confidence: float = 1.0
    reason: str = ""
    # When set, the emitter should place the shape at this absolute bbox
    # instead of the source element's bbox — used by `clip-path: inset(...)`
    # and `clip-path: circle(...)` which shrink the visible area.
    bbox_override: BoundingBox | None = None


# ---------------------------------------------------------------------------
# Catalog
# ---------------------------------------------------------------------------

KNOWN_PRESETS: dict[str, int] = {
    "rectangle": MSO_SHAPE.RECTANGLE,
    "rounded-rectangle": MSO_SHAPE.ROUNDED_RECTANGLE,
    "oval": MSO_SHAPE.OVAL,
    "ellipse": MSO_SHAPE.OVAL,
    "circle": MSO_SHAPE.OVAL,
    "chevron": MSO_SHAPE.CHEVRON,
    "chevron-right": MSO_SHAPE.CHEVRON,
    "chevron-left": MSO_SHAPE.CHEVRON,
    "pentagon": MSO_SHAPE.PENTAGON,
    "badge-pentagon": MSO_SHAPE.PENTAGON,
    "hexagon": MSO_SHAPE.HEXAGON,
    "badge-hexagon": MSO_SHAPE.HEXAGON,
    "octagon": MSO_SHAPE.OCTAGON,
    "trapezoid": MSO_SHAPE.TRAPEZOID,
    "parallelogram": MSO_SHAPE.PARALLELOGRAM,
    "arrow": MSO_SHAPE.RIGHT_ARROW,
    "arrow-right": MSO_SHAPE.RIGHT_ARROW,
    "arrow-left": MSO_SHAPE.LEFT_ARROW,
    "arrow-up": MSO_SHAPE.UP_ARROW,
    "arrow-down": MSO_SHAPE.DOWN_ARROW,
    "arrow-left-right": MSO_SHAPE.LEFT_RIGHT_ARROW,
    "arrow-up-down": MSO_SHAPE.UP_DOWN_ARROW,
    "arrow-bent": MSO_SHAPE.BENT_ARROW,
    "arrow-circular": MSO_SHAPE.CIRCULAR_ARROW,
    "arrow-striped": MSO_SHAPE.STRIPED_RIGHT_ARROW,
    "arrow-notched": MSO_SHAPE.NOTCHED_RIGHT_ARROW,
    "star": MSO_SHAPE.STAR_5_POINT,
    "star-4": MSO_SHAPE.STAR_4_POINT,
    "star-5": MSO_SHAPE.STAR_5_POINT,
    "star-6": MSO_SHAPE.STAR_6_POINT,
    "star-8": MSO_SHAPE.STAR_8_POINT,
    "badge-star": MSO_SHAPE.STAR_5_POINT,
    "callout": MSO_SHAPE.RECTANGULAR_CALLOUT,
    "callout-rounded": MSO_SHAPE.ROUNDED_RECTANGULAR_CALLOUT,
    "callout-oval": MSO_SHAPE.OVAL_CALLOUT,
    "speech-bubble": MSO_SHAPE.ROUNDED_RECTANGULAR_CALLOUT,
    "ribbon": MSO_SHAPE.LEFT_BRACE,
    "banner": MSO_SHAPE.LEFT_BRACE,
    "brace-left": MSO_SHAPE.LEFT_BRACE,
    "brace-right": MSO_SHAPE.RIGHT_BRACE,
    "bracket-left": MSO_SHAPE.LEFT_BRACKET,
    "bracket-right": MSO_SHAPE.RIGHT_BRACKET,
    "lightning": MSO_SHAPE.LIGHTNING_BOLT,
    "bolt": MSO_SHAPE.LIGHTNING_BOLT,
    "burst": MSO_SHAPE.EXPLOSION1,
    "explosion": MSO_SHAPE.EXPLOSION1,
    "explosion-large": MSO_SHAPE.EXPLOSION2,
    "blob": MSO_SHAPE.CLOUD,
    "cloud": MSO_SHAPE.CLOUD,
    "sun": MSO_SHAPE.SUN,
    "heart": MSO_SHAPE.HEART,
    "moon": MSO_SHAPE.MOON,
    "smiley": MSO_SHAPE.SMILEY_FACE,
    "cross": MSO_SHAPE.CROSS,
    "plus": MSO_SHAPE.CROSS,
    "pill": MSO_SHAPE.ROUNDED_RECTANGLE,
}


# ---------------------------------------------------------------------------
# Class-name detection
# ---------------------------------------------------------------------------

# Order matters: longest / most specific keys first so "arrow-right" beats "arrow"
_CLASS_KEYS = sorted(KNOWN_PRESETS.keys(), key=len, reverse=True)

_CLASS_TOKEN = re.compile(r"[a-z0-9\-]+")


def _class_tokens(cls: str) -> list[str]:
    return _CLASS_TOKEN.findall(cls.lower())


def _class_match(cls: str) -> tuple[str, int] | None:
    tokens = set(_class_tokens(cls))
    for key in _CLASS_KEYS:
        if key in tokens:
            return key, KNOWN_PRESETS[key]
    return None


# ---------------------------------------------------------------------------
# clip-path: polygon(...) parsing & classification
# ---------------------------------------------------------------------------

_POLY_RE = re.compile(r"polygon\s*\(([^)]*)\)", re.IGNORECASE)


def parse_polygon_clip_path(value: str) -> list[tuple[float, float]] | None:
    """Parse `polygon(x% y%, ...)` into a list of unit-square (0..1) coords."""
    if not value:
        return None
    m = _POLY_RE.search(value)
    if not m:
        return None
    pts: list[tuple[float, float]] = []
    for chunk in m.group(1).split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        parts = chunk.split()
        if len(parts) != 2:
            return None
        try:
            x = _to_unit(parts[0])
            y = _to_unit(parts[1])
        except ValueError:
            return None
        pts.append((x, y))
    if len(pts) < 3:
        return None
    return pts


def _to_unit(s: str) -> float:
    s = s.strip()
    if s.endswith("%"):
        return float(s[:-1]) / 100.0
    if s.endswith("px"):
        return float(s[:-2])
    return float(s)


def _polygon_centroid(pts: list[tuple[float, float]]) -> tuple[float, float]:
    cx = sum(p[0] for p in pts) / len(pts)
    cy = sum(p[1] for p in pts) / len(pts)
    return cx, cy


def _is_regular_polygon(
    pts: list[tuple[float, float]], tolerance: float = 0.12
) -> bool:
    """Vertices roughly equidistant from centroid."""
    if len(pts) < 3:
        return False
    cx, cy = _polygon_centroid(pts)
    dists = [math.hypot(p[0] - cx, p[1] - cy) for p in pts]
    mean = sum(dists) / len(dists)
    if mean <= 1e-6:
        return False
    spread = max(abs(d - mean) for d in dists) / mean
    return spread <= tolerance


def _looks_like_chevron(pts: list[tuple[float, float]]) -> bool:
    """6-vertex shape with a notch on one side and a point on the other."""
    if len(pts) != 6:
        return False
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    if max(ys) - min(ys) < 0.5 or max(xs) - min(xs) < 0.5:
        return False
    near_x0 = sum(1 for x in xs if x < 0.35)
    near_x1 = sum(1 for x in xs if x > 0.65)
    return near_x0 >= 2 and near_x1 >= 1


def _looks_like_arrow(pts: list[tuple[float, float]]) -> bool:
    """7-vertex right-pointing arrow signature."""
    if len(pts) != 7:
        return False
    xs = [p[0] for p in pts]
    if max(xs) - min(xs) < 0.5:
        return False
    return sum(1 for x in xs if x > 0.7) >= 1


def _looks_like_parallelogram(pts: list[tuple[float, float]]) -> bool:
    if len(pts) != 4:
        return False
    xs = sorted(p[0] for p in pts)
    ys = sorted(p[1] for p in pts)
    # Two distinct y bands, four distinct x positions in skewed pairs.
    if (ys[1] - ys[0]) > 0.1 or (ys[3] - ys[2]) > 0.1:
        return False
    if (ys[2] - ys[1]) < 0.4:
        return False
    return (xs[1] - xs[0]) > 0.05 and (xs[3] - xs[2]) > 0.05


def _looks_like_trapezoid(pts: list[tuple[float, float]]) -> bool:
    if len(pts) != 4:
        return False
    ys = sorted(p[1] for p in pts)
    if (ys[1] - ys[0]) > 0.1 or (ys[3] - ys[2]) > 0.1:
        return False
    if (ys[2] - ys[1]) < 0.4:
        return False
    top = sorted([p[0] for p in pts if p[1] <= ys[1] + 1e-3])
    bot = sorted([p[0] for p in pts if p[1] >= ys[2] - 1e-3])
    if len(top) != 2 or len(bot) != 2:
        return False
    top_w = top[1] - top[0]
    bot_w = bot[1] - bot[0]
    return abs(top_w - bot_w) > 0.15


def _looks_like_star(pts: list[tuple[float, float]]) -> int | None:
    """Return point-count (5/4/6/8) if alternating long/short radii pattern."""
    n = len(pts)
    if n not in (8, 10, 12, 16):
        return None
    cx, cy = _polygon_centroid(pts)
    dists = [math.hypot(p[0] - cx, p[1] - cy) for p in pts]
    mean = sum(dists) / n
    if mean <= 1e-6:
        return None
    long_idx = [i for i, d in enumerate(dists) if d > mean]
    short_idx = [i for i, d in enumerate(dists) if d <= mean]
    # roughly equal split, alternating
    if abs(len(long_idx) - len(short_idx)) > 1:
        return None
    alternating = all(
        (dists[i] > mean) != (dists[(i + 1) % n] > mean) for i in range(n)
    )
    if not alternating:
        return None
    return n // 2


def classify_polygon(pts: list[tuple[float, float]]) -> PresetMatch | None:
    """Map a normalized polygon to a PresetMatch, or None."""
    if not pts:
        return None

    star_points = _looks_like_star(pts)
    if star_points is not None:
        preset = {
            4: MSO_SHAPE.STAR_4_POINT,
            5: MSO_SHAPE.STAR_5_POINT,
            6: MSO_SHAPE.STAR_6_POINT,
            8: MSO_SHAPE.STAR_8_POINT,
        }.get(star_points)
        if preset is not None:
            return PresetMatch(
                preset=preset,
                confidence=0.9,
                reason=f"polygon star {star_points}-point",
            )

    n = len(pts)

    # Regular polygons first — they're unambiguous and would otherwise be
    # claimed by looser heuristics (a regular hexagon also has a 6-vertex
    # chevron-ish bbox spread).
    if _is_regular_polygon(pts):
        if n == 5:
            return PresetMatch(
                preset=MSO_SHAPE.PENTAGON,
                confidence=0.9,
                reason="regular pentagon",
            )
        if n == 6:
            return PresetMatch(
                preset=MSO_SHAPE.HEXAGON,
                confidence=0.9,
                reason="regular hexagon",
            )
        if n == 8:
            return PresetMatch(
                preset=MSO_SHAPE.OCTAGON,
                confidence=0.9,
                reason="regular octagon",
            )

    if n == 6 and _looks_like_chevron(pts):
        return PresetMatch(
            preset=MSO_SHAPE.CHEVRON,
            confidence=0.9,
            reason="polygon chevron signature",
        )

    if n == 7 and _looks_like_arrow(pts):
        return PresetMatch(
            preset=MSO_SHAPE.RIGHT_ARROW,
            confidence=0.85,
            reason="polygon arrow signature",
        )

    if n == 4 and _looks_like_parallelogram(pts):
        return PresetMatch(
            preset=MSO_SHAPE.PARALLELOGRAM,
            confidence=0.85,
            reason="polygon parallelogram",
        )

    if n == 4 and _looks_like_trapezoid(pts):
        return PresetMatch(
            preset=MSO_SHAPE.TRAPEZOID,
            confidence=0.85,
            reason="polygon trapezoid",
        )

    return None


# ---------------------------------------------------------------------------
# clip-path: inset(...) and circle(...) parsing
# ---------------------------------------------------------------------------

_INSET_RE = re.compile(r"^\s*inset\s*\(\s*(.*?)\s*\)\s*$", re.IGNORECASE | re.DOTALL)
_CIRCLE_RE = re.compile(r"^\s*circle\s*\(\s*(.*?)\s*\)\s*$", re.IGNORECASE | re.DOTALL)


def _parse_length(s: str, ref: float) -> float | None:
    """Parse a CSS length ('10%', '20px', '0') to absolute pixels.

    `ref` is the reference dimension used to resolve percentages.
    """
    s = s.strip().lower()
    if not s:
        return None
    if s.endswith("%"):
        try:
            return float(s[:-1]) / 100.0 * ref
        except ValueError:
            return None
    if s.endswith("px"):
        try:
            return float(s[:-2])
        except ValueError:
            return None
    try:
        return float(s)
    except ValueError:
        return None


def parse_inset_clip_path(
    value: str, bbox: BoundingBox
) -> tuple[BoundingBox, float] | None:
    """Parse `inset(top right? bottom? left? round radius?)`.

    Returns `(visible_bbox, corner_radius_px)` in absolute coordinates,
    or None if the expression is not a valid inset().
    """
    if not value:
        return None
    m = _INSET_RE.match(value)
    if not m:
        return None
    args = m.group(1).strip()
    if not args:
        return None

    radius_px = 0.0
    lower = args.lower()
    if " round " in lower:
        idx = lower.index(" round ")
        radius_part = args[idx + len(" round ") :].strip()
        args = args[:idx].strip()
        first = radius_part.split()[0] if radius_part.split() else ""
        rp = _parse_length(first, min(bbox.w, bbox.h))
        if rp is not None:
            radius_px = rp

    parts = args.split()
    if len(parts) == 1:
        t = rt = bt = lt = parts[0]
    elif len(parts) == 2:
        t, rt = parts
        bt, lt = t, rt
    elif len(parts) == 3:
        t, rt, bt = parts
        lt = rt
    elif len(parts) == 4:
        t, rt, bt, lt = parts
    else:
        return None

    top = _parse_length(t, bbox.h)
    right = _parse_length(rt, bbox.w)
    bottom = _parse_length(bt, bbox.h)
    left = _parse_length(lt, bbox.w)
    if top is None or right is None or bottom is None or left is None:
        return None

    new_w = max(0.0, bbox.w - left - right)
    new_h = max(0.0, bbox.h - top - bottom)
    if new_w <= 0 or new_h <= 0:
        return None
    return (
        BoundingBox(x=bbox.x + left, y=bbox.y + top, w=new_w, h=new_h),
        radius_px,
    )


def parse_circle_clip_path(value: str, bbox: BoundingBox) -> BoundingBox | None:
    """Parse `circle(radius? at cx? cy?)` to the absolute bbox of the visible
    inscribed circle. Returns None if the expression is not a valid circle().
    """
    if not value:
        return None
    m = _CIRCLE_RE.match(value)
    if not m:
        return None
    inner = m.group(1).strip()

    radius_arg = ""
    cx_arg: str | None = None
    cy_arg: str | None = None
    lower = inner.lower()
    if " at " in lower:
        idx = lower.index(" at ")
        radius_arg = inner[:idx].strip()
        position = inner[idx + len(" at ") :].strip().split()
        if len(position) >= 2:
            cx_arg, cy_arg = position[0], position[1]
        elif len(position) == 1:
            cx_arg = cy_arg = position[0]
    else:
        radius_arg = inner.strip()

    cx = _parse_length(cx_arg, bbox.w) if cx_arg else bbox.w / 2.0
    cy = _parse_length(cy_arg, bbox.h) if cy_arg else bbox.h / 2.0
    if cx is None or cy is None:
        return None

    radius_arg_l = radius_arg.lower()
    if radius_arg_l in ("", "closest-side"):
        radius = min(cx, bbox.w - cx, cy, bbox.h - cy)
    elif radius_arg_l == "farthest-side":
        radius = max(cx, bbox.w - cx, cy, bbox.h - cy)
    else:
        # Per CSS spec, % radius resolves against
        # sqrt(w^2 + h^2) / sqrt(2). For square boxes this equals the side
        # length, so circle(50%) of a 100x100 box is a radius-50 circle —
        # i.e., the inscribed circle, which is the common author intent.
        if radius_arg.endswith("%"):
            try:
                pct = float(radius_arg[:-1]) / 100.0
            except ValueError:
                return None
            radius = pct * math.sqrt(bbox.w * bbox.w + bbox.h * bbox.h) / math.sqrt(2)
        else:
            r = _parse_length(radius_arg, min(bbox.w, bbox.h))
            if r is None:
                return None
            radius = r

    if radius <= 0:
        return None
    return BoundingBox(
        x=bbox.x + cx - radius,
        y=bbox.y + cy - radius,
        w=2 * radius,
        h=2 * radius,
    )


def clip_path_to_preset(value: str, bbox: BoundingBox) -> PresetMatch | None:
    """Map any `clip-path` value to a PresetMatch, or None if untranslatable.

    Handles:
      - `polygon(...)` via the existing classifier
      - `inset(...)` -> RECTANGLE / ROUNDED_RECTANGLE (with bbox shrink)
      - `circle(...)` -> OVAL (with bbox shrink to inscribed square)
    """
    if not value or value.strip().lower() == "none":
        return None
    v = value.strip()

    inset = parse_inset_clip_path(v, bbox)
    if inset is not None:
        new_bbox, radius_px = inset
        if radius_px > 0:
            return PresetMatch(
                preset=MSO_SHAPE.ROUNDED_RECTANGLE,
                confidence=0.9,
                reason="clip_path inset round",
                bbox_override=new_bbox,
            )
        return PresetMatch(
            preset=MSO_SHAPE.RECTANGLE,
            confidence=0.95,
            reason="clip_path inset",
            bbox_override=new_bbox,
        )

    circle = parse_circle_clip_path(v, bbox)
    if circle is not None:
        return PresetMatch(
            preset=MSO_SHAPE.OVAL,
            confidence=0.95,
            reason="clip_path circle",
            bbox_override=circle,
        )

    pts = parse_polygon_clip_path(v)
    if pts is not None:
        return classify_polygon(pts)

    return None


# ---------------------------------------------------------------------------
# border-radius helpers
# ---------------------------------------------------------------------------

_RADIUS_RE = re.compile(r"(\d+(?:\.\d+)?)(px|%)")


def _parse_border_radius(value: str) -> tuple[float, str] | None:
    if not value:
        return None
    value = value.strip().lower()
    if value in ("", "0", "0px", "none"):
        return None
    m = _RADIUS_RE.search(value)
    if not m:
        return None
    return float(m.group(1)), m.group(2)


def _is_square(bbox: BoundingBox, tolerance: float = 0.15) -> bool:
    if bbox.w <= 0 or bbox.h <= 0:
        return False
    ratio = bbox.w / bbox.h
    return (1.0 - tolerance) <= ratio <= (1.0 + tolerance)


def _aspect(bbox: BoundingBox) -> float:
    if bbox.h <= 0:
        return 0.0
    return bbox.w / bbox.h


# ---------------------------------------------------------------------------
# Style-string extraction
# ---------------------------------------------------------------------------

_STYLE_PROP = re.compile(r"([a-z\-]+)\s*:\s*([^;]+)", re.IGNORECASE)


def _style_props(style: str) -> dict[str, str]:
    if not style:
        return {}
    out: dict[str, str] = {}
    for m in _STYLE_PROP.finditer(style):
        out[m.group(1).strip().lower()] = m.group(2).strip()
    return out


def _resolve_clip_path(el: DomElement) -> str:
    cp = (el.clip_path or "").strip()
    if cp and cp != "none":
        return cp
    # Some pipelines stash clip-path inside the inline style string.
    transform_or_style = getattr(el, "style", None)
    if isinstance(transform_or_style, str):
        props = _style_props(transform_or_style)
        return props.get("clip-path", "") or props.get("-webkit-clip-path", "")
    return ""


def _resolve_border_radius(el: DomElement) -> str:
    br = (el.border_radius or "").strip()
    if br and br != "0px":
        return br
    style = getattr(el, "style", None)
    if isinstance(style, str):
        props = _style_props(style)
        return props.get("border-radius", "")
    return ""


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def detect_preset_shape(el: DomElement) -> PresetMatch | None:
    """Detect a non-rectangular MSO preset for this element, or None."""

    # 1. clip-path: polygon(...) / inset(...) / circle(...)
    clip = _resolve_clip_path(el)
    if clip:
        match = clip_path_to_preset(clip, el.bbox)
        if match is not None:
            return match

    # 2. CSS class hints
    if el.cls:
        hit = _class_match(el.cls)
        if hit is not None:
            name, preset = hit
            confidence = 0.95 if "-" in name else 0.85
            return PresetMatch(
                preset=preset,
                confidence=confidence,
                reason=f"class hint '{name}'",
            )

    # 3. border-radius signals
    radius = _resolve_border_radius(el)
    parsed = _parse_border_radius(radius)
    if parsed is not None:
        value, unit = parsed
        bbox = el.bbox

        # 9999px (or any very large px) → pill / rounded rect
        if unit == "px" and value >= 999:
            return PresetMatch(
                preset=MSO_SHAPE.ROUNDED_RECTANGLE,
                confidence=0.9,
                reason="pill (border-radius >= 999px)",
            )

        if unit == "%":
            if value >= 50.0:
                if _is_square(bbox):
                    return PresetMatch(
                        preset=MSO_SHAPE.OVAL,
                        confidence=0.95,
                        reason="border-radius 50% square -> oval",
                    )
                if _aspect(bbox) >= 1.5 or _aspect(bbox) <= 0.66:
                    return PresetMatch(
                        preset=MSO_SHAPE.OVAL,
                        confidence=0.85,
                        reason="border-radius 50% wide -> stretched oval",
                    )

        # Large px radius relative to bbox short side → pill-like rounded rect
        if unit == "px":
            short = min(bbox.w, bbox.h) if bbox.w and bbox.h else 0
            if short and value >= short * 0.45:
                return PresetMatch(
                    preset=MSO_SHAPE.ROUNDED_RECTANGLE,
                    confidence=0.7,
                    reason="large border-radius -> rounded rect",
                )

    return None


# ---------------------------------------------------------------------------
# Emit
# ---------------------------------------------------------------------------


def emit_preset(slide, bbox: BoundingBox, match: PresetMatch):
    """Add the preset shape at bbox; return python-pptx shape."""
    from pptx.util import Emu

    from slidify.geom import px_to_emu

    x = Emu(px_to_emu(max(0.0, bbox.x)))
    y = Emu(px_to_emu(max(0.0, bbox.y)))
    w = Emu(px_to_emu(max(1.0, bbox.w)))
    h = Emu(px_to_emu(max(1.0, bbox.h)))

    shape = slide.shapes.add_shape(match.preset, x, y, w, h)
    if match.rotation_deg:
        try:
            shape.rotation = float(match.rotation_deg)
        except Exception:
            pass
    return shape
