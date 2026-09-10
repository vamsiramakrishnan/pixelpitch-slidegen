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

"""Paint the text out of base renders to produce artwork-only clean plates.

A base render carries the template's own placeholder copy. Overlaying new
text on it double-exposes. This removes every text-bearing rectangle the
extraction found, filling it by interpolating the ground colour sampled
immediately outside the rectangle on the same scanline, so panels, gradients
and photographs keep their local colour instead of being flattened.

The declared rectangle is not where the glyphs are. Autofit, descenders and a
line that wrapped one further than the designer planned all put ink outside
it, and painting the rectangle alone leaves a band of that ink behind for the
next author to type straight over. So each vertical edge is walked outward
until the ink stops, and anything still inky at the limit is named in the
report.

Mechanical only. Whether a plate is actually clean is decided by looking at
it.

  renderer/.venv/bin/python make_clean_plates.py --spec spec-out --out clean

Requires Pillow and numpy. Prefer `deck plates`, which resolves an
interpreter that has them.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

import numpy as np
from numpy.lib.stride_tricks import sliding_window_view
from PIL import Image, ImageFilter


def _text_rects(shape: dict) -> list[tuple[float, float, float, float]]:
    """Every text-bearing rectangle in a shape tree, in canvas px."""
    rects: list[tuple[float, float, float, float]] = []
    for child in shape.get("children") or []:
        rects.extend(_text_rects(child))
    runs = [
        run
        for paragraph in (shape.get("text") or [])
        for run in (paragraph.get("runs") or [])
    ]
    if not any((run.get("text") or "").strip() for run in runs):
        return rects
    box = [shape.get(key) for key in ("x", "y", "w", "h")]
    if any(value is None for value in box):
        return rects
    x, y, w, h = (float(value) for value in box)  # type: ignore[arg-type]
    if w <= 0 or h <= 0:
        return rects
    rects.append((x, y, w, h))
    return rects


def _base_for(base_dir: Path, slide_no: int) -> Path | None:
    """pdftoppm zero-pads to the page count, so accept any width."""
    for candidate in sorted(base_dir.glob("slide*.png")):
        digits = re.findall(r"(\d+)", candidate.stem)
        if digits and int(digits[-1]) == slide_no:
            return candidate
    return None


# A pixel counts as ink when it departs this far from the ground beneath it.
# Every foreground/background pair in a brand contract clears it by a wide
# margin, because a pair that did not would be unreadable on the slide.
INK_DELTA = 40.0

# And a row counts as inky at this fraction of its width. Residue rows measure
# in the tenths and photographic noise measures under one percent, so the
# threshold sits in open space between them.
INK_ROW_FRACTION = 0.02

# Canvas px of quiet that ends the walk. A gap inside a glyph is a pixel or
# two of antialiasing. This deliberately will not cross the leading between
# two lines: text that wrapped clear of its frame is left for the eye, because
# a walk that can jump a blank band can also jump onto a hairline rule and
# paint the artwork out.
QUIET_RUN = 2

# Ground is estimated by a horizontal median this wide, in canvas px. It has
# to be wider than the thickest glyph stroke, so the median steps over the
# letterforms, and narrower than the smallest thing the artwork draws, so it
# does not step over that too.
GROUND_WINDOW = 25


def _row_ink(image: np.ndarray, y: int, x0: int, x1: int, window: int) -> float:
    """Fraction of row `y` in [x0,x1) that is glyph rather than ground.

    Ground is the row's own horizontal median. Interpolating between the
    pixels either side of the span, which is what the fill does, reads a
    photograph or a panel edge as solid ink and cannot be used to decide
    anything. A median can: a stroke is narrow enough for the window to see
    past it, and a picture is not.
    """
    pad = window // 2
    a, b = max(0, x0 - pad), min(image.shape[1], x1 + pad)
    strip = image[y, a:b]
    edged = np.pad(strip, ((pad, pad), (0, 0)), mode="edge")
    ground = np.median(sliding_window_view(edged, window, axis=0), axis=-1)
    ink = np.abs(strip - ground).max(axis=1) > INK_DELTA
    return float(ink[x0 - a : x1 - a].mean())


def _extend(image: np.ndarray, y: int, x0: int, x1: int, step: int,
            limit: int, window: int, quiet_run: int) -> int:
    """Rows past `y` (walking in direction `step`) that still carry ink.

    A declared text frame is where PowerPoint said the text goes, not where it
    landed. Autofit, a descender and a wrapped line all put glyphs outside it,
    and those survive the paint as the residue an author then types over. This
    walks out from the edge until the ink stops, so the paint covers what is
    actually there.
    """
    height = image.shape[0]
    grown = 0
    quiet = 0
    for distance in range(1, limit + 1):
        row = y + step * distance
        if row < 0 or row >= height:
            break
        if _row_ink(image, row, x0, x1, window) > INK_ROW_FRACTION:
            grown = distance
            quiet = 0
            continue
        quiet += 1
        if quiet >= quiet_run:
            break
    return grown


def _fill_horizontal(image: np.ndarray, mask: np.ndarray) -> np.ndarray:
    """Interpolate each masked run from its unmasked neighbours on that row."""
    out = image.copy()
    height, width = mask.shape
    unfilled_rows: list[int] = []
    for y in range(height):
        row = mask[y]
        if not row.any():
            continue
        edges = np.flatnonzero(
            np.diff(np.concatenate(([0], row.astype(np.int8), [0])))
        )
        for start, end in zip(edges[0::2], edges[1::2]):
            left = image[y, start - 1] if start > 0 else None
            right = image[y, end] if end < width else None
            if left is None and right is None:
                unfilled_rows.append(y)
                continue
            if left is None:
                out[y, start:end] = right
                continue
            if right is None:
                out[y, start:end] = left
                continue
            ramp = np.linspace(0.0, 1.0, end - start, dtype=np.float32)[:, None]
            out[y, start:end] = left * (1.0 - ramp) + right * ramp

    if unfilled_rows:
        clean_rows = np.flatnonzero(~mask.any(axis=1))
        fallback = (
            image[~mask].reshape(-1, image.shape[2]).mean(axis=0)
            if (~mask).any()
            else np.zeros(image.shape[2], dtype=np.float32)
        )
        for y in sorted(set(unfilled_rows)):
            if clean_rows.size == 0:
                out[y, :] = fallback
                continue
            above = clean_rows[clean_rows < y]
            below = clean_rows[clean_rows > y]
            if above.size and below.size:
                top, bottom = int(above[-1]), int(below[0])
                weight = (y - top) / float(bottom - top)
                out[y, :] = out[top] * (1.0 - weight) + out[bottom] * weight
            else:
                source = int(above[-1]) if above.size else int(below[0])
                out[y, :] = out[source]
    return out


def make_plate(
    base_path: Path, rects: list[tuple[float, float, float, float]],
    canvas: tuple[float, float], grow: int, reach: int, blur: float,
) -> tuple[Image.Image, int, list[dict]]:
    source = Image.open(base_path).convert("RGB")
    width, height = source.size
    scale_x = width / float(canvas[0] or width)
    scale_y = height / float(canvas[1] or height)
    image = np.asarray(source, dtype=np.float32)
    window = max(3, int(round(GROUND_WINDOW * scale_x)) | 1)
    quiet_run = max(1, int(round(QUIET_RUN * scale_y)))

    mask = np.zeros((height, width), dtype=bool)
    painted = 0
    residue: list[dict] = []
    for x, y, w, h in rects:
        pad_x, pad_y = int(round(grow * scale_x)), int(round(grow * scale_y))
        x0 = max(0, int(x * scale_x) - pad_x)
        y0 = max(0, int(y * scale_y) - pad_y)
        x1 = min(width, int((x + w) * scale_x) + pad_x)
        y1 = min(height, int((y + h) * scale_y) + pad_y)
        if x1 <= x0 or y1 <= y0:
            continue
        # Text overruns its frame vertically, from autofit and from wrapping.
        # Sideways it has the frame's full width to wrap inside, so the two
        # axes are not the same problem and only this one is worth walking.
        limit = max(0, int(round(reach * scale_y)))
        up = _extend(image, y0, x0, x1, -1, limit, window, quiet_run)
        down = _extend(image, y1 - 1, x0, x1, 1, limit, window, quiet_run)
        if up >= limit > 0 or down >= limit > 0:
            residue.append({"rect": [x, y, w, h], "reached_limit_px": reach})
        y0, y1 = max(0, y0 - up), min(height, y1 + down)
        mask[y0:y1, x0:x1] = True
        painted += 1

    if not painted:
        return source, 0, residue

    filled = _fill_horizontal(image, mask)
    plate = Image.fromarray(np.clip(np.rint(filled), 0, 255).astype(np.uint8))
    if blur > 0:
        softened = np.asarray(
            plate.filter(ImageFilter.GaussianBlur(radius=int(round(blur)))), dtype=np.float32
        )
        blended = np.where(mask[:, :, None], softened, np.asarray(plate, np.float32))
        plate = Image.fromarray(np.clip(np.rint(blended), 0, 255).astype(np.uint8))
    return plate, painted, residue


def run(spec_root: Path, outdir: Path, grow: int, reach: int, blur: float) -> dict:
    spec_dir = spec_root / "spec" if (spec_root / "spec").is_dir() else spec_root
    base_dir = spec_root / "base"
    if not base_dir.is_dir():
        raise SystemExit(f"no base renders under {base_dir}")
    outdir.mkdir(parents=True, exist_ok=True)

    report: list[dict] = []
    for spec_path in sorted(spec_dir.glob("slide-*.json")):
        record = json.loads(spec_path.read_text(encoding="utf-8"))
        slide_no = int(record.get("slide") or re.findall(r"(\d+)", spec_path.stem)[-1])
        base_path = _base_for(base_dir, slide_no)
        if base_path is None:
            report.append({"slide": slide_no, "error": "no base render"})
            continue
        rects = [
            rect
            for shape in (record.get("shapes") or [])
            for rect in _text_rects(shape)
        ]
        raw_canvas = list(record.get("canvas") or (1280, 720))
        canvas = (float(raw_canvas[0]), float(raw_canvas[1]))
        plate, painted, residue = make_plate(
            base_path, rects, canvas, grow, reach, blur
        )
        plate_path = outdir / f"slide-{slide_no:02d}.png"
        plate.save(plate_path)
        report.append(
            {
                "slide": slide_no,
                "plate": str(plate_path),
                "text_rects_painted": painted,
                "base": str(base_path),
                "ink_past_reach": residue,
            }
        )
    return {"plates": report, "outdir": str(outdir)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--spec", required=True, help="spec-out/ (holds spec/ and base/)")
    parser.add_argument("--out", required=True, help="clean/ plate directory")
    parser.add_argument("--grow", type=int, default=4,
                        help="canvas px to grow each text rect before measuring")
    parser.add_argument("--reach", type=int, default=16,
                        help="canvas px the ink walk may add per vertical edge")
    parser.add_argument("--blur", type=float, default=2.0, help="px blend blur, 0 disables")
    args = parser.parse_args()
    result = run(Path(args.spec), Path(args.out), args.grow, args.reach, args.blur)
    print(json.dumps(result, indent=2))
    plates = [item for item in result["plates"] if "plate" in item]
    unpainted = [item["slide"] for item in plates if not item["text_rects_painted"]]
    if unpainted:
        print(
            "note: no text rects found on slides "
            + ", ".join(str(n) for n in unpainted)
            + " - open those plates before trusting them",
        )
    crowded = [item["slide"] for item in plates if item["ink_past_reach"]]
    if crowded:
        print(
            "note: ink still reads past --reach on slides "
            + ", ".join(str(n) for n in crowded)
            + " - either the text overruns further than "
            f"{args.reach}px, or the rect sits on a photograph, where the "
            "measure cannot tell glyphs from the picture. Open those plates.",
        )
    return 0 if plates else 1


if __name__ == "__main__":
    raise SystemExit(main())
