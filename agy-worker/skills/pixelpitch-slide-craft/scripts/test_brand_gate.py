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

"""Brand gate tests. The palette is always a derived contract, never a
constant — fixtures use a stand-in palette shaped like a real contract."""

from __future__ import annotations

from brand_gate import (
    MIN_CONTRAST_RATIO,
    contrast_ratio,
    lint_contrast,
    lint_palette,
    lint_slide,
    lint_slides,
)

# Stand-in derived contract (shape of a real brand-contract palette).
PALETTE = [
    "#0E0D26",
    "#1971ED",
    "#66C5FF",
    "#F3EA5D",
    "#FF6720",
    "#00B35F",
    "#DEDEDF",
    "#FFFFFF",
]


def test_on_palette_slide_is_clean():
    html = (
        "<style>.t{color:#1971ED;background:#FFFFFF}</style>"
        '<div style="border-bottom:1px solid #DEDEDF">x</div>'
    )
    assert lint_slide(html, PALETTE) == []


def test_off_palette_colour_is_flagged():
    html = '<div style="color:#7C3AED">purple is not in the contract</div>'
    issues = lint_palette(html, PALETTE)
    assert len(issues) == 1 and "#7C3AED" in issues[0]


def test_near_palette_rounding_is_tolerated():
    # One bit of channel drift: renderer rounding, not a new colour.
    html = '<div style="color:#1971EE">x</div>'
    assert lint_palette(html, PALETTE) == []


def test_short_hex_normalised_and_checked():
    assert lint_palette('<i style="color:#7c3">x</i>', PALETTE) != []


def test_no_palette_disables_palette_gate_not_contrast():
    html = '<div style="color:#777;background:#888">mud</div>'
    assert lint_palette(html, []) == []
    assert lint_contrast(html) != []


def test_wcag_ratio_known_values():
    # Black on white is exactly 21:1; brand blue on white comfortably passes.
    assert abs(contrast_ratio("#000000", "#FFFFFF") - 21.0) < 0.01
    assert contrast_ratio("#1971ED", "#FFFFFF") > MIN_CONTRAST_RATIO
    # Yellow text on a white ground is below the display-text floor.
    assert contrast_ratio("#F3EA5D", "#FFFFFF") < MIN_CONTRAST_RATIO


def test_low_contrast_pair_is_flagged():
    html = '<div style="color:#F3EA5D;background:#FFFFFF">yellow</div>'
    issues = lint_contrast(html)
    assert len(issues) == 1 and "contrast" in issues[0].lower()


def test_css_rule_block_contrast_is_flagged():
    html = "<style>p{color:#DEDEDF;background:#FFFFFF}</style>"
    assert lint_contrast(html) != []


def test_same_colour_pair_is_ignored():
    # Degenerate, but not a contrast failure the gate can reason about.
    html = '<div style="color:#1971ED;background:#1971ED">x</div>'
    assert lint_contrast(html) == []


def test_lint_slides_indexes_by_slide():
    slides = [
        {"html": "<p>clean</p>"},
        {"html": '<p style="color:#7C3AED">off</p>'},
    ]
    found = lint_slides(slides, PALETTE)
    assert 0 not in found and 1 in found
