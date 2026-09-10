"""CSS gradient parsing and translation to OOXML `a:gradFill` payloads.

Handles `linear-gradient` and `radial-gradient` strings as produced by
browser computed styles. Multi-stop, with optional explicit stop positions.

Output is an `lxml.etree._Element` that callers splice into a shape's
`spPr` to replace the default solid fill. python-pptx exposes solid/no-fill
helpers but not gradient; we drop down to OOXML directly.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

from lxml import etree

from slidify.colors import parse_color

# OOXML namespace for DrawingML.
NS_A = "http://schemas.openxmlformats.org/drawingml/2006/main"


@dataclass
class GradientStop:
    color_hex: str  # 6-hex (no leading #)
    alpha: float = 1.0  # 0..1
    position: float = 0.0  # 0..1


@dataclass
class LinearGradient:
    angle_deg: float  # CSS angle, 0deg = pointing UP
    stops: list[GradientStop]


@dataclass
class RadialGradient:
    stops: list[GradientStop]
    # Center as fractions in [0..1]. Default 50%/50% (CSS center).
    cx: float = 0.5
    cy: float = 0.5
    # Shape: 'circle' or 'ellipse'.
    shape: str = "ellipse"


_LINEAR_RE = re.compile(r"linear-gradient\s*\(", re.IGNORECASE)
_RADIAL_RE = re.compile(r"radial-gradient\s*\(", re.IGNORECASE)
_ANGLE_DEG_RE = re.compile(r"^\s*(-?\d+(?:\.\d+)?)\s*deg\s*$", re.IGNORECASE)
_PERCENT_RE = re.compile(r"^\s*(-?\d+(?:\.\d+)?)\s*%\s*$")
# Matches the computed-style normalization of CSS `transparent`:
# `rgba(0, 0, 0, 0)` (with any whitespace, any color channel; only alpha=0
# matters). Chromium emits this when CSS sets `color: transparent` or
# `background: transparent`.
_COMPUTED_TRANSPARENT_RE = re.compile(
    r"^\s*rgba?\(\s*\d+\s*,\s*\d+\s*,\s*\d+\s*,\s*0(?:\.0+)?\s*\)\s*$",
    re.IGNORECASE,
)


# A computed-style gradient looks like:
#   linear-gradient(135deg, rgb(99, 102, 241) 0%, rgb(236, 72, 153) 100%)
#   linear-gradient(rgb(255, 255, 255), rgb(0, 0, 0))   (default 'to bottom')
#   radial-gradient(rgba(...) 0%, rgba(...) 60%, rgba(...) 100%)


def parse_gradient(css_value: str) -> LinearGradient | RadialGradient | None:
    """Parse a CSS gradient string. Returns None if not a recognized gradient."""
    if not css_value:
        return None
    s = css_value.strip()
    is_linear = _LINEAR_RE.search(s) is not None
    is_radial = _RADIAL_RE.search(s) is not None
    if not (is_linear or is_radial):
        return None

    # Take the *first* gradient layer; computed style may have a stack.
    inner = _extract_first_call(s)
    if inner is None:
        return None
    args = _split_top_level(inner, ",")
    if not args:
        return None

    if is_linear:
        angle = 180.0  # CSS default: 'to bottom' = 180deg
        if not _looks_like_color_stop(args[0]):
            head = args[0].strip()
            m = _ANGLE_DEG_RE.match(head)
            if m:
                angle = float(m.group(1))
            else:
                angle = _parse_to_direction(head, default=180.0)
            stops_raw = args[1:]
        else:
            stops_raw = args
        stops = _parse_stops(stops_raw)
        if len(stops) < 2:
            return None
        return LinearGradient(angle_deg=angle, stops=stops)

    # Radial: prefix may contain shape, extent, and `at <pos>`. Parse it.
    cx, cy, shape = 0.5, 0.5, "ellipse"
    if not _looks_like_color_stop(args[0]):
        head = args[0].strip()
        cx, cy, shape = _parse_radial_prefix(head)
        stops_raw = args[1:]
    else:
        stops_raw = args
    stops = _parse_stops(stops_raw)
    if len(stops) < 2:
        return None
    return RadialGradient(stops=stops, cx=cx, cy=cy, shape=shape)


_AT_CLAUSE_RE = re.compile(r"\bat\s+(.+)$", re.IGNORECASE)
_KEYWORD_POS = {
    "left": 0.0, "center": 0.5, "right": 1.0,
    "top": 0.0, "bottom": 1.0,
}


def _parse_radial_prefix(head: str) -> tuple[float, float, str]:
    """Parse 'circle at 78% 18%' or 'ellipse 1200px 800px at 78% 18%' or
    'circle at top right' → (cx, cy, shape).

    Returns the tuple (cx, cy, shape) where cx,cy ∈ [0,1] and shape is either
    'circle' or 'ellipse'. Defaults to ellipse-at-center if unparsed.
    """
    s = head.strip().lower()
    shape = "circle" if "circle" in s else "ellipse"

    m = _AT_CLAUSE_RE.search(s)
    if not m:
        return 0.5, 0.5, shape
    rest = m.group(1).strip()
    tokens = rest.split()
    if not tokens:
        return 0.5, 0.5, shape

    # Two cases: keyword pair ("top right") or two values ("78% 18%").
    cx, cy = 0.5, 0.5
    parsed: list[tuple[str, float]] = []
    for tok in tokens:
        if tok in _KEYWORD_POS:
            parsed.append(("kw", _KEYWORD_POS[tok]))
        elif tok.endswith("%"):
            try:
                parsed.append(("frac", float(tok[:-1]) / 100.0))
            except ValueError:
                continue
        elif tok.endswith("px"):
            # Bare px positions are uncommon; treat as fraction-of-1280 just
            # so we don't lose the position entirely. Caller can override.
            try:
                parsed.append(("px", float(tok[:-2])))
            except ValueError:
                continue
    # Distribute first two parsed values to cx, cy.
    if len(parsed) >= 2:
        a, b = parsed[0], parsed[1]
        if a[0] == "kw" and b[0] == "kw":
            # Keyword pair — assignment depends on which axis each keyword belongs to.
            kw_x = {"left": 0.0, "center": 0.5, "right": 1.0}
            kw_y = {"top": 0.0, "center": 0.5, "bottom": 1.0}
            tok0, tok1 = tokens[0], tokens[1]
            if tok0 in kw_x and tok1 in kw_y:
                cx, cy = kw_x[tok0], kw_y[tok1]
            elif tok0 in kw_y and tok1 in kw_x:
                cx, cy = kw_x[tok1], kw_y[tok0]
            else:
                cx, cy = a[1], b[1]
        elif a[0] == "frac" and b[0] == "frac":
            cx, cy = a[1], b[1]
        elif a[0] == "px" and b[0] == "px":
            # Approximate against a 1280×720 viewport.
            cx = max(0.0, min(1.0, a[1] / 1280.0))
            cy = max(0.0, min(1.0, b[1] / 720.0))
        else:
            cx, cy = a[1], b[1]
    elif len(parsed) == 1:
        # Single keyword like "at center" — leave centered.
        pass

    return max(0.0, min(1.0, cx)), max(0.0, min(1.0, cy)), shape


def _extract_first_call(s: str) -> str | None:
    """Pull the body of the first linear-gradient(...) or radial-gradient(...)."""
    for kind in ("linear-gradient", "radial-gradient"):
        idx = s.lower().find(kind + "(")
        if idx == -1:
            continue
        start = idx + len(kind) + 1
        depth = 1
        i = start
        while i < len(s):
            ch = s[i]
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
                if depth == 0:
                    return s[start:i]
            i += 1
    return None


def _split_top_level(s: str, sep: str) -> list[str]:
    """Split a string by `sep` only at top-level (skipping parens)."""
    out: list[str] = []
    depth = 0
    cur = []
    for ch in s:
        if ch == "(":
            depth += 1
            cur.append(ch)
        elif ch == ")":
            depth -= 1
            cur.append(ch)
        elif ch == sep and depth == 0:
            out.append("".join(cur).strip())
            cur = []
        else:
            cur.append(ch)
    if cur:
        out.append("".join(cur).strip())
    return out


def _looks_like_color_stop(s: str) -> bool:
    s = s.strip().lower()
    return s.startswith(("rgb(", "rgba(", "#", "hsl(", "hsla("))


def _parse_to_direction(head: str, default: float) -> float:
    """Translate 'to bottom', 'to right', etc. → CSS angle (deg)."""
    h = head.strip().lower()
    if not h.startswith("to "):
        return default
    direction = h[3:].strip()
    table: dict[str, float] = {
        "top": 0.0,
        "right": 90.0,
        "bottom": 180.0,
        "left": 270.0,
        "top right": 45.0,
        "right top": 45.0,
        "bottom right": 135.0,
        "right bottom": 135.0,
        "bottom left": 225.0,
        "left bottom": 225.0,
        "top left": 315.0,
        "left top": 315.0,
    }
    return table.get(direction, default)


def _parse_stops(raw: list[str]) -> list[GradientStop]:
    """Parse `["rgb(...) 0%", "rgb(...) 100%", "transparent 70%", ...]` stops.

    `transparent` is preserved as alpha=0 (with a sensible color borrowed
    from the previous stop, since alpha=0 makes color irrelevant for paint
    but matters for color *interpolation* between stops in PowerPoint).
    Dropping a transparent stop turns a gradient-fades-to-transparent into
    a solid-opacity gradient — the aurora overlays in the showcase deck were
    rendering as full-opacity blobs because of this bug.
    """
    stops: list[GradientStop] = []
    if not raw:
        return []
    last_hex = "000000"  # color to inherit when a stop is fully-transparent
    for item in raw:
        # Color may itself contain spaces (rgba(255, 0, 0, 0.5)) → split off
        # the trailing position by detecting last whitespace not inside parens.
        color_str, position = _split_stop(item)
        cl = color_str.strip().lower()
        # "transparent" reaches us either as the literal CSS keyword or as the
        # computed-style normalization "rgba(0, 0, 0, 0)" / "rgba(*, *, *, 0)".
        # We need to detect both — otherwise the aurora-style gradient
        # `linear-gradient(... transparent 70%)` silently loses its
        # fade-to-transparent stop.
        is_transparent = (
            cl == "transparent"
            or _COMPUTED_TRANSPARENT_RE.match(cl) is not None
        )
        if is_transparent:
            hex_ = last_hex
            alpha = 0.0
        else:
            col = parse_color(color_str)
            if col is None:
                continue
            rgb, alpha = col
            hex_ = f"{rgb[0]:02X}{rgb[1]:02X}{rgb[2]:02X}"
            last_hex = hex_
        pos = -1.0 if position is None else position
        stops.append(GradientStop(color_hex=hex_, alpha=alpha, position=pos))

    # Fill in unspecified positions by linear interpolation between known ones.
    _fill_positions(stops)
    # If the last user-defined stop is < 100%, extend it explicitly to 1.0.
    # PowerPoint extrapolates the last stop's color past its position, but
    # LibreOffice cuts off — we'd see a hard-edged gradient on the showcase
    # auroras whose last stop is at 70%. Emit a redundant terminal stop so
    # both renderers agree.
    if stops and stops[-1].position < 0.999:
        stops.append(
            GradientStop(
                color_hex=stops[-1].color_hex,
                alpha=stops[-1].alpha,
                position=1.0,
            )
        )
    # Symmetric guard on the *first* stop.
    if stops and stops[0].position > 0.001:
        stops.insert(
            0,
            GradientStop(
                color_hex=stops[0].color_hex,
                alpha=stops[0].alpha,
                position=0.0,
            ),
        )
    return stops


def _split_stop(item: str) -> tuple[str, float | None]:
    """Split 'rgb(255,0,0) 50%' → ('rgb(255,0,0)', 0.5)."""
    s = item.strip()
    # Walk from the right, find the last whitespace-separated token outside parens
    depth = 0
    last_space = -1
    for i in range(len(s) - 1, -1, -1):
        ch = s[i]
        if ch == ")":
            depth += 1
        elif ch == "(":
            depth -= 1
        elif ch.isspace() and depth == 0:
            last_space = i
            break
    if last_space == -1:
        return s, None
    color_part = s[:last_space].strip()
    pos_part = s[last_space:].strip()
    m = _PERCENT_RE.match(pos_part)
    if m:
        return color_part, max(0.0, min(1.0, float(m.group(1)) / 100.0))
    # Bare number? (rare)
    try:
        return color_part, max(0.0, min(1.0, float(pos_part)))
    except ValueError:
        return s, None


def _fill_positions(stops: list[GradientStop]) -> None:
    """Fill in any -1 positions using endpoints + linear interpolation."""
    if not stops:
        return
    n = len(stops)
    # Anchor endpoints if missing
    if stops[0].position < 0:
        stops[0].position = 0.0
    if stops[-1].position < 0:
        stops[-1].position = 1.0
    # Walk and fill gaps
    i = 0
    while i < n:
        if stops[i].position >= 0:
            i += 1
            continue
        # Find next anchored stop
        j = i
        while j < n and stops[j].position < 0:
            j += 1
        prev_pos = stops[i - 1].position if i > 0 else 0.0
        next_pos = stops[j].position if j < n else 1.0
        gap = j - i + 1
        for k, idx in enumerate(range(i, j), start=1):
            stops[idx].position = prev_pos + (next_pos - prev_pos) * k / gap
        i = j + 1


# ---------------------------------------------------------------------------
# OOXML emission
# ---------------------------------------------------------------------------


def to_grad_fill_xml(grad: LinearGradient | RadialGradient) -> etree._Element:
    """Build an `<a:gradFill>` element ready to splice into shape's spPr."""
    fill = etree.Element(f"{{{NS_A}}}gradFill", attrib={"flip": "none", "rotWithShape": "1"})

    stop_lst = etree.SubElement(fill, f"{{{NS_A}}}gsLst")
    for stop in grad.stops:
        gs = etree.SubElement(
            stop_lst,
            f"{{{NS_A}}}gs",
            attrib={"pos": str(int(round(max(0.0, min(1.0, stop.position)) * 100_000)))},
        )
        srgb = etree.SubElement(gs, f"{{{NS_A}}}srgbClr", attrib={"val": stop.color_hex})
        if stop.alpha < 0.999:
            etree.SubElement(
                srgb,
                f"{{{NS_A}}}alpha",
                attrib={"val": str(int(round(stop.alpha * 100_000)))},
            )

    if isinstance(grad, LinearGradient):
        # PPTX angle is in 60_000ths of a degree, measured from "right" axis,
        # rotating clockwise. CSS 0deg points UP; 90deg points RIGHT.
        # Convert: pptx_deg = (css_deg - 90) % 360
        # (CSS up = 0 → pptx -90 → pptx +270; CSS right = 90 → pptx 0)
        pptx_deg = (grad.angle_deg - 90.0) % 360.0
        etree.SubElement(
            fill,
            f"{{{NS_A}}}lin",
            attrib={"ang": str(int(round(pptx_deg * 60_000))), "scaled": "0"},
        )
    else:
        # Radial: <a:path path="circle"><a:fillToRect l/t/r/b ... /></a:path>
        # `fillToRect` defines the bounds of the focal (innermost) stop as a
        # sub-rect of the shape in 1/100,000ths. l/r are the left/right
        # insets; r and b are insets from the right/bottom (so r=80000 means
        # 80% from the right, i.e. cx ≈ 20%). For a focal point at (cx,cy)
        # we set l=cx*100k, t=cy*100k, r=(1-cx)*100k, b=(1-cy)*100k — an
        # infinitesimal focal sub-rect.
        path_kind = "circle"  # PPTX has no separate "ellipse" path; both use "circle"
        path = etree.SubElement(fill, f"{{{NS_A}}}path", attrib={"path": path_kind})
        cx_clamped = max(0.0, min(1.0, grad.cx))
        cy_clamped = max(0.0, min(1.0, grad.cy))
        etree.SubElement(
            path,
            f"{{{NS_A}}}fillToRect",
            attrib={
                "l": str(int(round(cx_clamped * 100_000))),
                "t": str(int(round(cy_clamped * 100_000))),
                "r": str(int(round((1.0 - cx_clamped) * 100_000))),
                "b": str(int(round((1.0 - cy_clamped) * 100_000))),
            },
        )
        # The default <a:tileRect/> outer ring (full-shape) is implicit;
        # not adding it lets PowerPoint default to "fill the whole shape."

    return fill


def apply_gradient_fill(
    shape,
    grad: LinearGradient | RadialGradient,
    *,
    densify: bool = True,
) -> bool:
    """Replace a shape's fill with a native gradient. Returns True on success.

    When ``densify`` is True (the default) and the gradient has only its
    endpoint stops, OKLCH-interpolated mid-stops are inserted so the
    renderer's piecewise-linear sRGB interpolation approximates a
    perceptually uniform gradient. This eliminates the "muddy grey
    midpoint" between two saturated colors (e.g. indigo → pink).
    Densification is skipped when the gradient already has 4+ stops.
    """
    if densify and len(grad.stops) <= 3:
        try:
            grad = with_oklch_density(grad, n=5)
        except Exception:
            pass  # Fall back to original stops if OKLCH math fails.
    try:
        sp_pr = shape.fill._xPr  # python-pptx internal — works for shapes/textboxes
    except Exception:
        try:
            sp_pr = shape._element.spPr
        except Exception:
            return False
    if sp_pr is None:
        return False
    # Strip any existing fill children
    for tag_local in (
        "solidFill",
        "gradFill",
        "blipFill",
        "pattFill",
        "noFill",
        "grpFill",
    ):
        for existing in sp_pr.findall(f"{{{NS_A}}}{tag_local}"):
            sp_pr.remove(existing)
    sp_pr.insert(0, to_grad_fill_xml(grad))
    return True


# Helper for callers: detect whether a CSS value is a gradient at all.
def is_gradient(css_value: str | None) -> bool:
    if not css_value:
        return False
    return _LINEAR_RE.search(css_value) is not None or _RADIAL_RE.search(css_value) is not None


def with_oklch_density(
    grad: LinearGradient | RadialGradient,
    n: int = 5,
) -> LinearGradient | RadialGradient:
    """Return a copy of ``grad`` whose stops have been densified in OKLCH.

    Inserts ``n`` perceptually-uniform stops between each adjacent pair so
    the renderer's piecewise-linear sRGB interpolation approximates an
    OKLCH-space sweep. Original stops, angle/center/shape are preserved.
    """
    # Local import keeps gradients.py free of an import-time dep on oklch
    # (oklch.py imports GradientStop from this module).
    from slidify.oklch import densify_stops

    new_stops = densify_stops(list(grad.stops), n_intermediate=n)
    if isinstance(grad, LinearGradient):
        return LinearGradient(angle_deg=grad.angle_deg, stops=new_stops)
    return RadialGradient(stops=new_stops, cx=grad.cx, cy=grad.cy, shape=grad.shape)


# Expose math constant in case callers want to know.
__all__ = [
    "GradientStop",
    "LinearGradient",
    "RadialGradient",
    "apply_gradient_fill",
    "is_gradient",
    "math",
    "parse_gradient",
    "to_grad_fill_xml",
    "with_oklch_density",
]
