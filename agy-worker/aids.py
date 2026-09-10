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

"""Subagents for visual aids, written from the template rather than about it.

A subagent declared in the request gets the same system instructions on every
deck: draw a good chart, stay on brand, mind the palette. It is asked to place
artwork on a ground it has never measured, so it centres the aid in the canvas,
lands on the lockup, and produces the off-template slide the whole roundtrip
exists to prevent. The instruction was not wrong. It was unaddressed.

Everything that instruction was missing is already in ``layouts.json``. Each
layout knows its text zones and their grounds, the furniture rects the plate
draws and their assets, and whether the slide is protected. Subtracting the
first two from the canvas gives the region an aid may actually occupy, in
pixels, on this plate. That number cannot be written in advance because it is
different on every layout, which is why it was never written at all.

So the specs are synthesised per layout at launch. The subagent is told the box
it is drawing into, the colour behind it, the rects it must not cross and what
occupies them, and the deck's own aid vocabulary from ``brand-contract.json``.

Two decisions worth stating.

Aid subagents are read-only and return markup. The alternative, letting them
write the slide, would put a writing agent inside a workspace whose seal is
enforced by policies on the parent, and whether those policies reach a subagent
is not something to find out from a damaged plate. Returning an ``<svg>`` the
parent places also keeps composition with the parent, which is the only party
that knows what the rest of the slide says.

Protected layouts get no subagent. The Acknowledgement of Country slides carry
Indigenous artwork, and `brand-contract.json` says not to reuse or imitate it.
An instruction saying so is a sentence a turn can reason past; no subagent
existing is not.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path

# 20px cells on the 1280x720 canvas. Fine enough that the free region is not
# rounded into uselessness, coarse enough that a text rect missing its own
# descender by three pixels does not carve the region in half.
CELL = 20

# Text sits in a rect; the space an aid may use starts outside it. One cell of
# clearance is the smallest gap that reads as deliberate at presentation size.
MARGIN = CELL

# Under this, there is no aid to draw and a subagent offering to draw one is an
# invitation to crowd the slide. Roughly a fifth of the canvas.
MIN_AREA = 1280 * 720 * 0.18
MIN_SIDE = 200


@dataclass(frozen=True)
class Region:
    """The largest rectangle on a layout that no text and no furniture claims."""

    x: int
    y: int
    w: int
    h: int

    @property
    def area(self) -> int:
        return self.w * self.h

    def usable(self) -> bool:
        return (
            self.area >= MIN_AREA and self.w >= MIN_SIDE and self.h >= MIN_SIDE
        )


def _blocked(canvas, rects, margin: int) -> list[list[bool]]:
    # The contract records the canvas as PowerPoint measured it, which is
    # floats on a template authored in inches.
    cols, rows = int(canvas[0]) // CELL, int(canvas[1]) // CELL
    grid = [[False] * cols for _ in range(rows)]
    for rect in rects:
        # A cell is blocked when the rect touches any part of it, so the near
        # edge floors and the far edge ceils. A rect that ends exactly on a
        # cell boundary must not claim the cell after it.
        x0 = max(0, math.floor((rect["x"] - margin) / CELL))
        y0 = max(0, math.floor((rect["y"] - margin) / CELL))
        x1 = min(cols, math.ceil((rect["x"] + rect["w"] + margin) / CELL))
        y1 = min(rows, math.ceil((rect["y"] + rect["h"] + margin) / CELL))
        for row in range(y0, y1):
            for col in range(x0, x1):
                grid[row][col] = True
    return grid


def free_region(canvas, rects, margin: int = MARGIN) -> Region:
    """The largest empty rectangle left after the text and the furniture.

    Largest-rectangle-in-a-histogram over the occupancy grid, row by row. The
    biggest empty box is the one an aid wants: a chart needs one region it can
    breathe in, not the several slivers a general free-space decomposition
    would hand back.
    """
    grid = _blocked(canvas, rects, margin)
    rows, cols = len(grid), len(grid[0]) if grid else 0
    heights = [0] * cols
    best = Region(0, 0, 0, 0)

    for row in range(rows):
        for col in range(cols):
            heights[col] = 0 if grid[row][col] else heights[col] + 1
        stack: list[tuple[int, int]] = []
        for col in range(cols + 1):
            height = heights[col] if col < cols else 0
            start = col
            while stack and stack[-1][1] > height:
                left, tall = stack.pop()
                found = Region(
                    left * CELL,
                    (row - tall + 1) * CELL,
                    (col - left) * CELL,
                    tall * CELL,
                )
                if found.area > best.area:
                    best = found
                start = left
            stack.append((start, height))
    return best


def region_for(canvas, layout: dict) -> tuple[Region, str]:
    """The region an aid can have, and the placeholder it cost.

    The empty space is tried first. When there is none worth drawing in, one
    body placeholder is given over to the aid and the region is recomputed,
    which is what an author does anyway: on the layout this deck reuses five
    times, four text rects cover everything but a 480x60 strip, and an aid
    there is either in a placeholder or nowhere.

    The title is never the one given up. A slide whose claim has moved into a
    picture has no claim, and the title is where the claim is.
    """
    zones = list(layout.get("zones") or [])
    furniture = list(layout.get("furniture") or [])
    empty = free_region(canvas, zones + furniture)
    if empty.usable():
        return empty, ""

    body = [zone for zone in zones if "TITLE" not in str(zone.get("role", "")).upper()]
    if not body:
        return empty, ""
    yielded = max(body, key=lambda zone: zone["w"] * zone["h"])
    kept = [zone for zone in zones if zone is not yielded]
    return free_region(canvas, kept + furniture), str(yielded.get("role", "BODY"))


def _ground(layout: dict) -> str:
    """The colour behind the aid, as the layout's own zones report it.

    Taken from the text zones rather than sampled from the plate, because
    reading the plate needs Pillow and this runs at launch on whatever
    interpreter the worker was started with. A plate whose free region is a
    different colour from every one of its text zones exists, and on those the
    subagent is told to open the plate.
    """
    grounds = [z.get("ground") for z in layout.get("zones") or [] if z.get("ground")]
    if not grounds:
        return ""
    return max(set(grounds), key=grounds.count)


def _keepouts(layout: dict) -> list[str]:
    out = []
    for piece in layout.get("furniture") or []:
        asset = Path(str(piece.get("asset", "artwork"))).name
        out.append(
            f"{int(piece['x'])},{int(piece['y'])} "
            f"{int(piece['w'])}x{int(piece['h'])} ({asset}, on "
            f"{piece.get('slides', 1)} template slides)"
        )
    return out


INSTRUCTIONS = """\
You draw one visual aid, as an inline SVG fragment, and return it as your whole
reply. You do not write files and you cannot: the slide is the parent's to
assemble, because only the parent knows what the rest of it says.

## The box

Your artwork occupies {w}x{h} px at ({x}, {y}) on a 1280x720 slide. Draw a
single `<svg width="{w}" height="{h}" viewBox="0 0 {w} {h}">` with its own
coordinate space starting at 0,0. The parent positions it. Nothing you draw may
leave the viewBox.

This is the largest region on layout `{layout}` that the template's own text
placeholders and artwork do not already claim. It is not a suggestion about
composition; outside it you are drawing over the template.
{yielded_note}

The ground behind you is {ground}. {ground_note}

## What is already on this plate

{keepouts}

These are the template's, on every slide that uses this layout. The parent
places you clear of them, so you have nothing to avoid inside your own box, but
they tell you the visual weight the slide already carries: an aid that competes
with the lockup loses.

## This deck's aid vocabulary

{vocabulary}

Palette, and nothing outside it:

{palette}

Type is {major} for anything bold and {minor} otherwise.

## Rules that are checked

- Inline SVG only. No `<img>`, no `background-image: url(...)`, no external
  fonts, no JavaScript, no `<style>` outside the fragment.
- No `filter`, `mix-blend-mode`, `backdrop-filter`, `mask-image`, or
  `clip-path: path()`. Each one forces the whole slide to rasterise, which
  costs every piece of editable text on it.
- Nothing below 18px. Labels a room cannot read are decoration.
- One accent. A chart in four hues is four claims, and a slide makes one.
- Every mark carries information. No shadows, no gradients used as texture, no
  rounded card behind a number.

{donts}

## What you are drawing

Your prompt is the claim the aid has to carry, in the parent's words. Lead with
the shape that carries it: a change shows two states, a share shows the whole,
a sequence shows the order. Annotate the mark that matters and leave the others
unlabelled.

If the prompt gives you no claim, ask for one rather than drawing something
plausible. An aid about nothing passes every gate here and says nothing in the
room, and that is the one failure none of the rules above can catch.
"""


def _bullets(items) -> str:
    return "\n".join(f"- {item}" for item in items) if items else "- (none)"


YIELDED_NOTE = """
This layout had no empty space worth drawing in, so the box is the template's
`{yielded}` placeholder, given over to you. Whatever text would have gone there
is not going anywhere else on this slide, so the aid has to carry that part of
the argument on its own. If it cannot, say so and draw nothing.
"""


def aid_spec(layout: dict, brand: dict, region: Region, yielded: str = "") -> dict:
    """One subagent, told where it is drawing and what the deck draws like."""
    palette = {
        name: value
        for name, value in (brand.get("palette") or {}).items()
        if isinstance(value, str) and value.startswith("#")
    }
    fonts = brand.get("fonts") or {}
    ground = _ground(layout)
    slide = layout.get("slide")
    return {
        "name": f"aid-slide-{slide:02d}" if isinstance(slide, int) else "aid",
        "description": (
            f"Draws the visual aid for a slide built on layout "
            f"{layout.get('layout')!r} ({layout.get('archetype')}). Knows the "
            f"{region.w}x{region.h} region that layout leaves free at "
            f"({region.x}, {region.y}) and this deck's derived palette. Give "
            "it the claim the aid must carry; it returns an inline SVG "
            "fragment."
        )[:500],
        "system_instructions": INSTRUCTIONS.format(
            x=region.x,
            y=region.y,
            w=region.w,
            h=region.h,
            layout=layout.get("layout", "?"),
            yielded_note=YIELDED_NOTE.format(yielded=yielded) if yielded else "",
            ground=ground or "not recorded",
            ground_note=(
                "Choose contrast against it."
                if ground
                else f"Open {layout.get('plate')} and look before you pick a colour."
            ),
            keepouts=_bullets(_keepouts(layout)),
            vocabulary=brand.get("visual_aids_style")
            or "Not derived for this deck. Stay plain: hairlines, one accent.",
            palette=_bullets(f"`{value}` {name}" for name, value in palette.items()),
            major=fonts.get("major", "the deck's bold face"),
            minor=fonts.get("minor", "the deck's text face"),
            donts=_bullets(brand.get("donts") or []),
        ),
    }


def build_aid_subagents(workspace, limit: int = 6) -> list[dict]:
    """Aid subagents for a workspace, one per distinct free region.

    Deduplicated by region and archetype rather than emitted per slide: a deck
    reuses eleven grounds across twenty-eight layouts, and twenty-eight
    subagents that differ in nothing but a slide number is a menu no turn reads
    to the end.
    """
    root = Path(workspace)
    try:
        contract = json.loads((root / "layouts.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    try:
        brand = json.loads((root / "brand-contract.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        brand = {}

    canvas = tuple(contract.get("canvas") or (1280, 720))
    specs: list[dict] = []
    seen: set[tuple] = set()
    for layout in contract.get("layouts") or []:
        if layout.get("protected") or not layout.get("live", True):
            continue
        region, yielded = region_for(canvas, layout)
        if not region.usable():
            continue
        key = (layout.get("archetype"), region.x, region.y, region.w, region.h)
        if key in seen:
            continue
        seen.add(key)
        specs.append(aid_spec(layout, brand, region, yielded))
    specs.sort(key=lambda spec: spec["name"])
    return specs[:limit]
