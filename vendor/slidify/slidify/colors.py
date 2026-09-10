"""CSS color parsing for PPTX RGBColor / fill values."""

from __future__ import annotations

import re

from pptx.dml.color import RGBColor

_HEX_RE = re.compile(r"^#?([0-9a-fA-F]{3}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$")
_RGB_RE = re.compile(
    r"rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*(?:,\s*([\d.]+)\s*)?\)",
    re.IGNORECASE,
)


def parse_color(value: str | None) -> tuple[RGBColor, float] | None:
    """Parse a CSS color → (RGBColor, alpha). Returns None for transparent / invalid."""
    if not value:
        return None
    s = value.strip()
    if s.lower() in ("transparent", "none", ""):
        return None

    m = _RGB_RE.match(s)
    if m:
        r, g, b = int(m.group(1)), int(m.group(2)), int(m.group(3))
        a = float(m.group(4)) if m.group(4) is not None else 1.0
        if a <= 0:
            return None
        return RGBColor(r, g, b), a

    h = _HEX_RE.match(s)
    if h:
        digits = h.group(1)
        if len(digits) == 3:
            r = int(digits[0] * 2, 16)
            g = int(digits[1] * 2, 16)
            b = int(digits[2] * 2, 16)
            return RGBColor(r, g, b), 1.0
        if len(digits) == 6:
            r = int(digits[0:2], 16)
            g = int(digits[2:4], 16)
            b = int(digits[4:6], 16)
            return RGBColor(r, g, b), 1.0
        if len(digits) == 8:
            r = int(digits[0:2], 16)
            g = int(digits[2:4], 16)
            b = int(digits[4:6], 16)
            a = int(digits[6:8], 16) / 255.0
            if a <= 0:
                return None
            return RGBColor(r, g, b), a

    return None
