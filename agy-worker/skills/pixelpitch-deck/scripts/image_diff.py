#!/usr/bin/env python3
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

"""Perceptual delta between two slide images, reported by region.

Used to answer one question mechanically: did the artwork move? Comparing a
converted render against its clean plate should show change only where text
was added. Change in the artwork zones means the plate was rebuilt rather
than reused.

It localises difference; it does not judge it. A region report is a place to
look, not a verdict.

  python image_diff.py --a render.png --b clean/slide-03.png --out delta.png

Requires Pillow and numpy. Prefer `deck diff`, which resolves an interpreter
that has them.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
from PIL import Image

GRID_COLUMNS = 8
GRID_ROWS = 5


def _load(path: Path, size: tuple[int, int] | None = None) -> np.ndarray:
    image = Image.open(path).convert("RGB")
    if size is not None and image.size != size:
        image = image.resize(size, Image.Resampling.LANCZOS)
    return np.asarray(image, dtype=np.float32)


def compare(a_path: Path, b_path: Path, threshold: float) -> tuple[dict, np.ndarray]:
    a_image = Image.open(a_path).convert("RGB")
    a = np.asarray(a_image, dtype=np.float32)
    b = _load(b_path, a_image.size)
    delta = np.abs(a - b).max(axis=2)
    changed = delta > threshold

    height, width = delta.shape
    regions = []
    for row in range(GRID_ROWS):
        for column in range(GRID_COLUMNS):
            y0, y1 = row * height // GRID_ROWS, (row + 1) * height // GRID_ROWS
            x0, x1 = column * width // GRID_COLUMNS, (column + 1) * width // GRID_COLUMNS
            cell = changed[y0:y1, x0:x1]
            share = float(cell.mean())
            if share > 0.01:
                regions.append(
                    {
                        "rect": [x0, y0, x1 - x0, y1 - y0],
                        "changed_share": round(share, 4),
                        "max_delta": round(float(delta[y0:y1, x0:x1].max()), 1),
                    }
                )
    regions.sort(key=lambda item: item["changed_share"], reverse=True)

    report = {
        "size": [width, height],
        "mean_delta": round(float(delta.mean()), 3),
        "max_delta": round(float(delta.max()), 1),
        "changed_share": round(float(changed.mean()), 4),
        "regions": regions[:12],
    }
    return report, delta


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--a", required=True, help="the render under inspection")
    parser.add_argument("--b", required=True, help="the plate or base it should match")
    parser.add_argument("--out", help="write a heatmap PNG here")
    parser.add_argument("--threshold", type=float, default=12.0, help="per-channel delta counted as changed")
    args = parser.parse_args()

    report, delta = compare(Path(args.a), Path(args.b), args.threshold)
    if args.out:
        heat = np.clip(delta / max(delta.max(), 1.0) * 255.0, 0, 255).astype(np.uint8)
        Image.fromarray(heat, mode="L").save(args.out)
        report["heatmap"] = args.out
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
