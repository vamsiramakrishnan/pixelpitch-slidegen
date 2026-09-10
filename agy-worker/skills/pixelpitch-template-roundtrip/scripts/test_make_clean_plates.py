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

"""A plate that still carries ink is the one defect the author cannot see.

They open the base render, see the template's copy, know it will be painted
out, and author over the top. The residue then lands under their own line and
reads as a font bug rather than as a bad plate. Twenty-one of the twenty-eight
slides in the first template through this pipeline shipped that way, so these
are the measured failures, not hypotheticals.
"""

from __future__ import annotations

import numpy as np
import pytest
from make_clean_plates import make_plate
from PIL import Image

CANVAS = (1280.0, 720.0)
SCALE = 2
BLUE = (25, 113, 237)
WHITE = (255, 255, 255)
RECT = (48.0, 100.0, 500.0, 50.0)


def ground(kind: str = "flat") -> np.ndarray:
    height, width = int(CANVAS[1]) * SCALE, int(CANVAS[0]) * SCALE
    if kind == "flat":
        return np.full((height, width, 3), BLUE, dtype=np.uint8)
    # A photograph is the case that breaks the obvious ground model, where you
    # interpolate across the span and read everything that differs as ink. It
    # varies over the whole range slowly and only slightly from pixel to pixel,
    # which is the difference the median is there to exploit.
    rng = np.random.default_rng(0)
    coarse = rng.integers(0, 255, (height // 96, width // 96, 3), dtype=np.uint8)
    slow = np.asarray(
        Image.fromarray(coarse).resize((width, height), Image.Resampling.BICUBIC),
        dtype=np.float32,
    )
    fast = rng.normal(0, 8, (height, width, 3)).astype(np.float32)
    return np.clip(slow + fast, 0, 255).astype(np.uint8)


def ink(image: np.ndarray, top: float, bottom: float) -> None:
    """Glyph-width bars across a canvas-space band, sparse the way text is."""
    rows = slice(int(top * SCALE), int(bottom * SCALE))
    for x in range(60 * SCALE, 520 * SCALE, 14 * SCALE):
        image[rows, x : x + 4 * SCALE] = WHITE


def spread(plate: np.ndarray, top: float, bottom: float) -> float:
    """How much the band varies, per channel. Flat ground scores zero."""
    band = plate[int(top * SCALE) : int(bottom * SCALE), 60 * SCALE : 520 * SCALE]
    return float(band.reshape(-1, 3).std(axis=0).max())


@pytest.fixture
def plate_of(tmp_path):
    def build(image: np.ndarray, rects=(RECT,), **options):
        path = tmp_path / "base.png"
        Image.fromarray(image).save(path)
        plate, _, residue = make_plate(
            path, list(rects), CANVAS, **({"grow": 4, "reach": 16, "blur": 0.0} | options)
        )
        return np.asarray(plate, dtype=np.int16), residue

    return build


def test_ink_below_the_declared_rect_is_painted_out(plate_of):
    """The frame is where PowerPoint said the text goes, not where it landed."""
    image = ground()
    ink(image, 100, 160)
    plate, _ = plate_of(image)
    assert spread(plate, 151, 162) < 1.0


def test_a_line_that_wrapped_clear_of_the_frame_is_left_alone(plate_of):
    """Out of scope on purpose. Crossing blank ground can eat a hairline."""
    image = ground()
    ink(image, 100, 148)
    ink(image, 162, 172)
    plate, _ = plate_of(image)
    assert spread(plate, 162, 172) > 1.0


def test_a_photographic_ground_does_not_start_a_runaway_walk(plate_of):
    """Every rect would grow to the cap if ground were read as a flat ramp."""
    image = ground("photo")
    walked, residue = plate_of(image)
    unwalked, _ = plate_of(image, reach=0)
    assert not residue
    assert np.array_equal(walked, unwalked)


def test_grow_is_canvas_pixels_not_render_pixels(plate_of):
    """The two agree at 1x and nowhere else, which is why this went unnoticed."""
    image = ground()
    ink(image, 100, 150)
    ink(image, 154, 158)
    plate, _ = plate_of(image, grow=8, reach=0)
    assert spread(plate, 153, 159) < 1.0


def test_ink_past_the_cap_is_reported_rather_than_chased(plate_of):
    """Bounded, because artwork the measure misreads must not be eaten whole."""
    image = ground()
    ink(image, 100, 260)
    plate, residue = plate_of(image, reach=12)
    assert residue == [{"rect": list(RECT), "reached_limit_px": 12}]
    assert spread(plate, 200, 240) > 1.0


def test_a_rect_with_nothing_under_it_leaves_the_artwork_alone(plate_of):
    plate, residue = plate_of(ground())
    assert not residue
    assert np.array_equal(plate, ground().astype(np.int16))

