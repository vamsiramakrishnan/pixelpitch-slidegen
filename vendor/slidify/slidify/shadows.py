"""CSS box-shadow parsing and translation to OOXML `a:outerShdw` / `a:innerShdw`.

CSS box-shadow has the shape:
    [inset]? <offset-x> <offset-y> <blur-radius>? <spread-radius>? <color>

Multiple shadows are comma-separated. We parse all layers and emit each as
its own `<a:outerShdw>` / `<a:innerShdw>` inside a single `<a:effectLst>`.
PowerPoint stacks them in order (first layer = topmost), matching CSS
which paints them outermost-first.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

from lxml import etree

from slidify.colors import parse_color

NS_A = "http://schemas.openxmlformats.org/drawingml/2006/main"


@dataclass
class BoxShadow:
    inset: bool
    offset_x_px: float
    offset_y_px: float
    blur_px: float
    spread_px: float
    color_hex: str
    alpha: float


_NUM_RE = r"-?\d+(?:\.\d+)?"
_PX_NUM_RE = re.compile(rf"({_NUM_RE})\s*px", re.IGNORECASE)


def _split_top_level(s: str, sep: str) -> list[str]:
    out: list[str] = []
    depth = 0
    cur: list[str] = []
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


def _parse_one_layer(layer: str) -> BoxShadow | None:
    color_match = re.search(
        r"(rgba?\([^)]+\)|#[0-9a-fA-F]{3,8}|hsla?\([^)]+\))", layer
    )
    if color_match:
        color_str = color_match.group(1)
        rest = (layer[: color_match.start()] + layer[color_match.end():]).strip()
    else:
        color_str = "rgba(0, 0, 0, 0.25)"
        rest = layer.strip()

    inset = False
    if "inset" in rest.lower():
        inset = True
        rest = re.sub(r"\binset\b", "", rest, flags=re.IGNORECASE).strip()

    nums = _PX_NUM_RE.findall(rest)
    if len(nums) < 2:
        return None
    offset_x = float(nums[0])
    offset_y = float(nums[1])
    blur = float(nums[2]) if len(nums) >= 3 else 0.0
    spread = float(nums[3]) if len(nums) >= 4 else 0.0

    col = parse_color(color_str)
    if col is None:
        return None
    rgb, alpha = col
    hex_ = f"{rgb[0]:02X}{rgb[1]:02X}{rgb[2]:02X}"

    return BoxShadow(
        inset=inset,
        offset_x_px=offset_x,
        offset_y_px=offset_y,
        blur_px=max(0.0, blur),
        spread_px=spread,
        color_hex=hex_,
        alpha=alpha,
    )


def parse_box_shadow(css_value: str | None) -> BoxShadow | None:
    """Parse the FIRST layer of a CSS box-shadow.

    Kept for callers that want a single shadow. For multi-layer shadows
    (Tailwind's `shadow-2xl` decomposes into 2 layers) prefer
    `parse_box_shadows`.
    """
    layers = parse_box_shadows(css_value)
    return layers[0] if layers else None


def parse_box_shadows(css_value: str | None) -> list[BoxShadow]:
    """Parse all comma-separated layers of a CSS box-shadow.

    Returns layers in source order (CSS paints first-listed on top).
    Skips unparseable layers rather than failing the whole list.
    """
    if not css_value:
        return []
    s = css_value.strip()
    if s.lower() == "none":
        return []
    out: list[BoxShadow] = []
    for layer in _split_top_level(s, ","):
        sh = _parse_one_layer(layer)
        if sh is not None:
            out.append(sh)
    return out


# ---------------------------------------------------------------------------
# OOXML emission
# ---------------------------------------------------------------------------


def _ensure_effect_lst(sp_pr: etree._Element) -> etree._Element:
    """Get-or-create the spPr's <a:effectLst> child."""
    existing = sp_pr.find(f"{{{NS_A}}}effectLst")
    if existing is not None:
        # Clear children to avoid duplicate effects on re-emit.
        for child in list(existing):
            existing.remove(child)
        return existing
    el = etree.SubElement(sp_pr, f"{{{NS_A}}}effectLst")
    return el


def _emu(px: float) -> int:
    return int(round(px * 9525))


def _append_shadow_to_effect_lst(
    effect_lst: etree._Element, shadow: BoxShadow
) -> None:
    """Append one outerShdw/innerShdw to an existing effectLst, in order."""
    dx, dy = shadow.offset_x_px, shadow.offset_y_px
    distance = math.hypot(dx, dy)
    if distance < 0.001:
        direction_deg = 0.0
    else:
        direction_deg = (math.degrees(math.atan2(dy, dx)) + 360.0) % 360.0

    tag = f"{{{NS_A}}}{'innerShdw' if shadow.inset else 'outerShdw'}"
    attrs: dict[str, str] = {
        "blurRad": str(_emu(shadow.blur_px)),
        "dist": str(_emu(distance)),
        "dir": str(int(round(direction_deg * 60_000))),
    }
    if not shadow.inset:
        attrs["algn"] = "ctr"
        attrs["rotWithShape"] = "0"
    shdw = etree.SubElement(effect_lst, tag, attrib=attrs)
    srgb = etree.SubElement(shdw, f"{{{NS_A}}}srgbClr", attrib={"val": shadow.color_hex})
    if shadow.alpha < 0.999:
        etree.SubElement(
            srgb,
            f"{{{NS_A}}}alpha",
            attrib={"val": str(int(round(shadow.alpha * 100_000)))},
        )


def apply_shadow(shape, shadow: BoxShadow) -> bool:
    """Attach a single native shadow effect to `shape`."""
    return apply_shadows(shape, [shadow])


def apply_shadows(shape, shadows: list[BoxShadow]) -> bool:
    """Attach one or more shadow effects to `shape`. Returns True if any
    shadow was attached.

    Each layer becomes its own `<a:outerShdw>` / `<a:innerShdw>` inside a
    single shared `<a:effectLst>`. Layers stack in CSS paint order
    (first-listed = topmost), which matches the order children appear
    inside `<a:effectLst>` for PowerPoint's renderer.
    """
    if not shadows:
        return False
    try:
        sp_pr = shape._element.spPr  # python-pptx Shape / Picture
    except AttributeError:
        try:
            sp_pr = shape._element.find(f"{{{NS_A}}}spPr")
        except Exception:
            return False
    if sp_pr is None:
        return False
    effect_lst = _ensure_effect_lst(sp_pr)
    for sh in shadows:
        _append_shadow_to_effect_lst(effect_lst, sh)
    return True


def is_translatable_shadow(css_value: str | None) -> bool:
    return parse_box_shadow(css_value) is not None


__all__ = [
    "BoxShadow",
    "apply_shadow",
    "apply_shadows",
    "is_translatable_shadow",
    "parse_box_shadow",
    "parse_box_shadows",
]
