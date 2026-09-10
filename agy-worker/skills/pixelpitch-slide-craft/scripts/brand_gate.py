# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Deterministic brand-contract enforcement for authored slide HTML.

The palette is never hardcoded here: it is the contract the harness derived
from the customer template (``app.brand_deriver`` → ``brand-contract.json``).
This module is the delivery gate that consumes it:

* ``lint_palette``  — every colour in the HTML must be on the derived palette
  (within a small tolerance for renderer rounding);
* ``lint_contrast`` — same-element text/background pairs must meet a WCAG AA
  floor for large display text;
* ``lint_slide``    — both, as revision-ready messages for ``revise_slides``.

Deliberately conservative: a false positive costs a wasted revision pass.
Perceptual judgements the pixels alone cannot settle stay in the vision
critique; this gate only proves what is mechanically provable.
"""

from __future__ import annotations

import re

# #RGB and #RRGGBB (the grammar's native-safe colour forms).
_HEX = re.compile(r"#[0-9a-fA-F]{3}\b|#[0-9a-fA-F]{6}\b")
_RULE_BLOCK = re.compile(r"\{[^{}]*\}")

# WCAG AA for large text (>= 24px bold / 18.66px+): 3.0:1. Slide titles and
# body copy are display-scale, so this is the defensible deterministic floor.
MIN_CONTRAST_RATIO = 3.0

# RGB euclidean distance a colour may sit from a palette entry and still count
# as that entry (renderer rounding, not a new colour).
PALETTE_TOLERANCE = 48.0


def _norm_hex(value: str) -> str | None:
    value = value.strip()
    if not re.fullmatch(r"#[0-9a-fA-F]{3}|#[0-9a-fA-F]{6}", value):
        return None
    if len(value) == 4:
        value = "#" + "".join(ch * 2 for ch in value[1:])
    return value.upper()


def _rgb(hex_value: str) -> tuple[int, int, int]:
    return tuple(int(hex_value[i : i + 2], 16) for i in (1, 3, 5))  # type: ignore[return-value]


def _distance(a: tuple[int, int, int], b: tuple[int, int, int]) -> float:
    return sum((x - y) ** 2 for x, y in zip(a, b)) ** 0.5


def _nearest_distance(color: str, palette: list[str]) -> float:
    rgb = _rgb(color)
    return min((_distance(rgb, _rgb(p)) for p in palette), default=float("inf"))


def lint_palette(html: str, palette: list[str]) -> list[str]:
    """Flag colours that are not on the derived palette."""
    allowed = [_norm_hex(p) for p in palette if _norm_hex(p)]
    if not allowed:
        return []  # no contract -> gate disabled, not silently failed
    offenders: dict[str, int] = {}
    for match in _HEX.finditer(html or ""):
        color = _norm_hex(match.group(0))
        if color and color not in allowed:
            if _nearest_distance(color, allowed) > PALETTE_TOLERANCE:
                offenders[color] = offenders.get(color, 0) + 1
    if not offenders:
        return []
    listing = ", ".join(f"{c}×{n}" if n > 1 else c for c, n in sorted(offenders.items()))
    return [
        "BRAND (off-palette colour): "
        f"{listing} is not in the derived brand palette. Use only the "
        "contract colours; pick the nearest usage role."
    ]


def _luminance(hex_value: str) -> float:
    def channel(value: int) -> float:
        ratio = value / 255.0
        return ratio / 12.92 if ratio <= 0.04045 else ((ratio + 0.055) / 1.055) ** 2.4

    r, g, b = _rgb(hex_value)
    return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)


def contrast_ratio(foreground: str, background: str) -> float:
    """WCAG contrast ratio between two hex colours."""
    lum_a = _luminance(_norm_hex(foreground) or "#000000")
    lum_b = _luminance(_norm_hex(background) or "#FFFFFF")
    lighter, darker = max(lum_a, lum_b), min(lum_a, lum_b)
    return (lighter + 0.05) / (darker + 0.05)


def _decl(block: str, prop: str) -> str | None:
    m = re.search(rf"(?<![\w-]){re.escape(prop)}\s*:\s*([^;}}]+)", block, re.I)
    if not m:
        return None
    value = m.group(1).strip()
    return _norm_hex(value) if value.startswith("#") else None


def lint_contrast(html: str) -> list[str]:
    """Flag same-rule #colour text on #colour backgrounds below the AA floor.

    Only provable pairs: declarations in the same CSS rule or inline style.
    Inherited/background-image cases are the vision critique's job.
    """
    blocks = [m.group() for m in _RULE_BLOCK.finditer(html or "")]
    blocks += [f"{{{m.group(1)}}}" for m in re.finditer(r'style="([^"]*)"', html or "", re.I)]
    failures: list[str] = []
    for block in blocks:
        fg, bg = _decl(block, "color"), _decl(block, "background")
        if not fg or not bg or fg == bg:
            continue
        if fg == "#FFFFFF" and "FFFFFF" in bg:
            continue
        ratio = contrast_ratio(fg, bg)
        if ratio < MIN_CONTRAST_RATIO:
            failures.append(
                f"BRAND (contrast {ratio:.1f}:1): {fg} text on {bg} is below "
                f"the {MIN_CONTRAST_RATIO:.0f}:1 floor. Darken the text or "
                "lighten the ground."
            )
    return failures


def lint_slide(html: str, palette: list[str]) -> list[str]:
    """Full brand gate for one slide: palette + contrast."""
    return [*lint_palette(html, palette), *lint_contrast(html)]


def lint_slides(slides: list[dict], palette: list[str]) -> dict[int, list[str]]:
    """Brand-gate violations per slide index, mirroring ``slop.lint_slides``."""
    found: dict[int, list[str]] = {}
    for i, slide in enumerate(slides):
        issues = lint_slide(slide.get("html", ""), palette)
        if issues:
            found[i] = issues
    return found
