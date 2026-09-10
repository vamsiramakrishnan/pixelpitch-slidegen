"""Coordinate math: pixels (browser) ↔ EMU (PPTX)."""

from __future__ import annotations

import re

EMU_PER_INCH = 914_400
DPI = 96
EMU_PER_PX = EMU_PER_INCH // DPI  # 9525

# Default 16:9 slide @ 96 DPI: 1280 × 720 px → 13.33 × 7.5 in
SLIDE_W_PX = 1280
SLIDE_H_PX = 720
SLIDE_W_EMU = SLIDE_W_PX * EMU_PER_PX  # 12_192_000
SLIDE_H_EMU = SLIDE_H_PX * EMU_PER_PX  # 6_858_000


def px_to_emu(px: float) -> int:
    """Convert pixels to EMU (English Metric Units)."""
    return int(round(px * EMU_PER_PX))


_PX_RE = re.compile(r"(-?\d+(?:\.\d+)?)\s*px", re.IGNORECASE)
_NUM_RE = re.compile(r"-?\d+(?:\.\d+)?")


def parse_px(css_value: str | None) -> float:
    """Parse '14.5px' → 14.5. Returns 0.0 for non-px or empty values."""
    if not css_value:
        return 0.0
    m = _PX_RE.search(css_value)
    if m:
        return float(m.group(1))
    # Bare number? (rare in computed style, but tolerate)
    m2 = _NUM_RE.fullmatch(css_value.strip())
    if m2:
        return float(m2.group(0))
    return 0.0


def parse_pt(css_value: str | None) -> float:
    """Parse a font-size in 'px' and convert to pt (1pt = 1/72in, 1px = 1/96in)."""
    px = parse_px(css_value)
    return px * 72.0 / DPI


def clamp_int(v: float, lo: int, hi: int) -> int:
    return max(lo, min(hi, int(round(v))))
