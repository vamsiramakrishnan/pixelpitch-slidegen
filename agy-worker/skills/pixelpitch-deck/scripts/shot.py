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

"""Rasterise slides locally with the Chromium slidify already depends on.

``deck shot`` posts to a renderer service. When that service is not reachable
the gate law ("no slide is done until you have opened its rendered PNG") is
unenforceable, which is the one failure mode the whole skill is built to
prevent. This is the offline path, and it is the default one: the browser is
already installed, so a network round trip buys nothing.

Also writes a contact sheet, because deck-level defects (three slides in a
row with the same skeleton, a palette that drifts halfway through) are
invisible one PNG at a time.

Usage:
  shot.py <slides.json|index.json|slides_dir> --out DIR [--no-sheet]
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

_CRAFT = Path(__file__).resolve().parents[2] / "pixelpitch-slide-craft" / "scripts"
sys.path.insert(0, str(_CRAFT))

from slides_io import load_slides  # noqa: E402

SHEET_COLUMNS = 3
SHEET_TILE_WIDTH = 480

# Anything past this many pixels outside the 1280x720 box is a real clip, not
# a descender or a rounding artefact.
BLEED_TOLERANCE = 2

# Text a viewer cannot read once the deck is projected. Mirrors the floors in
# pixelpitch-slide-craft/SKILL.md, which are prose there and a gate here.
FLOOR_PX = 18

# WCAG AA, and the size at which its large-text allowance applies.
CONTRAST_FLOOR = 4.5
CONTRAST_FLOOR_LARGE = 3.0
LARGE_PX = 24.0
LARGE_BOLD_PX = 18.66

# How much of a run may fall under the floor before it is called unreadable,
# and the smaller share at which a run that mostly reads is judged to have run
# off its clear ground. Past the first threshold the block is on the wrong
# ground and wants moving or recolouring; between the two it is the line length
# that is wrong, and the fix is a shorter line, not a different colour.
CONTRAST_FRACTION = 0.25
CROSSING_FRACTION = 0.02

# The ground is photographed, not estimated. Every statistical way of reading
# it out of the finished pixels was tried first and none survives: a mean or a
# median over a window wide enough to span two glyphs reports semibold body
# copy as its own background, because at x-height the ink really is half the
# row. Hiding the text costs one more screenshot and is exact.
_HIDE_TEXT = """
  * { color: transparent !important; -webkit-text-fill-color: transparent !important;
      text-shadow: none !important; text-decoration-color: transparent !important; }
  svg text, svg tspan { fill: transparent !important; stroke: none !important; }
"""

# Containment is relative to whatever actually clips, not to the slide. An
# SVG label that runs past its own viewBox is cut off exactly like a paragraph
# that runs past .slide, and the first version of this probe measured only the
# latter. Both decks under test shipped the SVG variant past a green gate.
_PROBE = """() => {
  const bad = [];
  const seen = new Set();
  const say = (key, message) => {
    if (seen.has(key)) return;
    seen.add(key);
    bad.push(message);
  };
  const name = (el) => el.tagName.toLowerCase()
    + (typeof el.getAttribute('class') === 'string' && el.getAttribute('class')
       ? '.' + el.getAttribute('class').trim().split(/\\s+/).join('.') : '');

  const clipperOf = (el) => {
    for (let p = el.parentElement; p; p = p.parentElement) {
      if (p.tagName.toLowerCase() === 'svg') return p;
      const s = getComputedStyle(p);
      if (s.overflow !== 'visible' || s.overflowX !== 'visible' || s.overflowY !== 'visible') return p;
    }
    return document.documentElement;
  };

  for (const el of document.querySelectorAll('body *')) {
    const style = getComputedStyle(el);
    if (style.display === 'none' || style.visibility === 'hidden') continue;
    const box = el.getBoundingClientRect();
    if (box.width === 0 && box.height === 0) continue;
    const text = (el.textContent || '').trim().replace(/\\s+/g, ' ').slice(0, 48);
    const tag = name(el);

    const clipper = clipperOf(el);
    const bounds = clipper === document.documentElement
      ? {left: 0, top: 0, right: 1280, bottom: 720}
      : clipper.getBoundingClientRect();
    const over = Math.max(box.right - bounds.right, box.bottom - bounds.bottom,
                          bounds.left - box.left, bounds.top - box.top);
    if (over > TOL) {
      const where = clipper === document.documentElement ? 'the slide' : name(clipper);
      say('clip:' + tag,
          `clipped ${Math.round(over)}px outside ${where}: ${tag}`
          + (text ? ` "${text}"` : ''));
    }

    const size = parseFloat(style.fontSize);
    const leaf = !Array.from(el.children).some(c => (c.textContent || '').trim());
    if (leaf && text && size && size < FLOOR) {
      say('tiny:' + tag, `${size}px text is below the ${FLOOR}px floor: ${tag} "${text}"`);
    }
  }

  // Two text runs sharing pixels. Grid and flex do not clip an overlong label,
  // they let it sit on top of its neighbour, which reads as a typo in the
  // render and is invisible to every containment check above.
  // SVG text counts, and counts most: a chart label landing on its neighbour
  // is the commonest way a hand-placed exhibit goes wrong. Compare text to
  // text only, so a label legitimately sitting on a bar is not a finding.
  const leaves = Array.from(document.querySelectorAll('body *')).filter((el) => {
    if (!(el.textContent || '').trim()) return false;
    if (Array.from(el.children).some(c => (c.textContent || '').trim())) return false;
    const svg = !!el.closest('svg');
    if (svg && el.tagName.toLowerCase() !== 'text') return false;
    const s = getComputedStyle(el);
    return s.display !== 'none' && s.visibility !== 'hidden';
  });
  // Absolutely positioned runs are placed by hand against a plate, where boxes
  // routinely overlap and the glyphs do not. Their real failure is being
  // buried, which the occlusion pass below measures instead.
  const runs = leaves.filter(
    el => !!el.closest('svg') || getComputedStyle(el).position !== 'absolute');
  for (let i = 0; i < runs.length; i++) {
    for (let j = i + 1; j < runs.length; j++) {
      if (runs[i].contains(runs[j]) || runs[j].contains(runs[i])) continue;
      const a = runs[i].getBoundingClientRect();
      const b = runs[j].getBoundingClientRect();
      const w = Math.min(a.right, b.right) - Math.max(a.left, b.left);
      const h = Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top);
      if (w > TOL && h > TOL) {
        say('overlap:' + name(runs[i]) + '|' + name(runs[j]),
            `text overlaps text by ${Math.round(w)}x${Math.round(h)}px: `
            + `${name(runs[i])} "${(runs[i].textContent||'').trim().slice(0,28)}" over `
            + `${name(runs[j])} "${(runs[j].textContent||'').trim().slice(0,28)}"`);
      }
    }
  }

  // A run buried under an opaque panel. The overlap check above compares text
  // to text, so it cannot see this, and every containment check passes because
  // the run is exactly where it was asked to go. It is simply not visible.
  // Absolute positioning is how it happens: a zone pinned at a fixed top, a
  // headline above it that wrapped to one more line than planned, and the
  // subhead in between disappears under the zone's background.
  // Which element in `hit`'s line of ancestry actually lays down paint, up to
  // the point where that ancestry rejoins the run's. The hit element is often
  // a bare label whose parent panel carries the background, and naming the
  // parent is what tells an author where to look.
  const paintsOver = (hit, run) => {
    for (let p = hit; p && !p.contains(run); p = p.parentElement) {
      const s = getComputedStyle(p);
      const rgba = (s.backgroundColor.match(/[\\d.]+/g) || []).map(Number);
      if (s.backgroundImage !== 'none'
          || ['img', 'svg', 'canvas', 'video'].includes(p.tagName.toLowerCase())
          || (rgba.length && (rgba.length < 4 || rgba[3] > 0.5))) return p;
    }
    return null;
  };

  for (const run of leaves) {
    // Glyph rects, not the element box. A box has leading and trailing space
    // in it that nothing is drawn on, and sampling those invents findings.
    const range = document.createRange();
    range.selectNodeContents(run);
    const lines = Array.from(range.getClientRects()).filter(r => r.width > 1 && r.height > 1);
    let sampled = 0;
    let buried = 0;
    let culprit = null;
    for (const line of lines) {
      const y = line.top + line.height / 2;
      for (let i = 1; i <= 5; i++) {
        const x = line.left + (line.width * i) / 6;
        if (x < 0 || x > 1280 || y < 0 || y > 720) continue;
        // The whole stack, topmost first. Asking only for the top element
        // answers a different question: SVG text is hit on its glyphs alone,
        // so a sample landing between two letters reports the ground behind
        // the chart as if it were painted over the label.
        const stack = document.elementsFromPoint(x, y);
        const depth = stack.indexOf(run);
        if (depth < 0) continue;
        sampled++;
        let over = null;
        for (const el of stack.slice(0, depth)) if ((over = paintsOver(el, run))) break;
        if (over) { buried++; culprit = culprit || over; }
      }
    }
    if (sampled && buried / sampled > 0.25) {
      say('buried:' + name(run),
          `${Math.round((100 * buried) / sampled)}% of ${name(run)} is painted over `
          + `by ${name(culprit)}: "${(run.textContent || '').trim().slice(0, 28)}"`);
    }
  }

  // An image that did not load. The element is present, sized and laid out, so
  // every check above passes over it, and what the slide actually shows is a
  // broken-image glyph on bare background. On a template deck this is the whole
  // plate, meaning the artwork the deck is supposed to sit on is simply absent,
  // and the converter, finding no image, rasterises the slide instead.
  for (const img of document.querySelectorAll('img')) {
    if (img.complete && img.naturalWidth > 0) continue;
    const src = (img.getAttribute('src') || '').slice(0, 80) || '(no src)';
    say('broken:' + src, `image failed to load: ${name(img)} src="${src}"`);
  }

  const doc = document.documentElement;
  if (doc.scrollHeight - 720 > TOL) bad.push(`document scrolls ${doc.scrollHeight - 720}px past 720`);
  if (doc.scrollWidth - 1280 > TOL) bad.push(`document scrolls ${doc.scrollWidth - 1280}px past 1280`);
  return bad;
}""".replace("TOL", str(BLEED_TOLERANCE)).replace("FLOOR", str(FLOOR_PX))


# Contrast against a ground the DOM cannot read. Every check above asks the
# browser where things are; this one asks the screenshot what a viewer sees.
# A headline that starts on flat brand blue and runs onto the photograph beside
# it is exactly where PowerPoint's own text sat, is clipped by nothing, collides
# with nothing, and is painted over by nothing, because the photograph is inside
# the plate. There is no element to find. There are only pixels.
#
# Only runs standing on artwork are measured. Where the ground is a declared
# CSS colour, `deck lint` already has the two colours and judges them at source,
# and running a second, stricter opinion over the same pair here is how a gate
# stops being read: it fired on twelve of the sixteen gated gallery slides, on
# their editorial muted tone, every one of them correct as drawn. A plate is
# the opposite case. The ground is a PNG, no source gate can see it, and this
# is the only place the question can be asked at all.
_RUNS = """() => {
  const name = (el) => el.tagName.toLowerCase()
    + (typeof el.getAttribute('class') === 'string' && el.getAttribute('class')
       ? '.' + el.getAttribute('class').trim().split(/\\s+/).join('.') : '');
  // Whether an opaque colour is the slide's ground or a panel sitting on it.
  // Body, section.slide and a full-bleed div are all the same thing to a
  // reader, so the distinction cannot be which element declares it, only how
  // much of the slide it covers.
  const isGround = (el) => {
    const r = el.getBoundingClientRect();
    return r.width >= 1280 * 0.98 && r.height >= 720 * 0.98;
  };

  // Down through what is behind the run to the first thing that paints. An
  // image or a background-image means pixels, which no source gate can read.
  // An opaque colour means the DOM knew the answer, and whether that is worth
  // measuring here depends on what is wearing it. The slide's own ground is
  // `deck lint`'s to judge, and a second stricter opinion over the same pair is
  // how a gate stops being read: it fired on twelve of the sixteen gated
  // gallery slides, on their editorial muted tone, every one of them correct as
  // drawn. A panel the author dropped on that ground is the opposite case. Its
  // colour and the run's colour are declared in two rules on two elements, so
  // lint never sees them as a pair, and until this measured them a run at
  // 1.91:1 on a brand-blue panel passed both gates without a word.
  const onArtwork = (run, x, y) => {
    const stack = document.elementsFromPoint(x, y);
    const depth = stack.indexOf(run);
    if (depth < 0) return false;
    for (const el of stack.slice(depth + 1)) {
      const s = getComputedStyle(el);
      if (s.backgroundImage !== 'none'
          || ['img', 'canvas', 'video'].includes(el.tagName.toLowerCase())) return true;
      const rgba = (s.backgroundColor.match(/[\\d.]+/g) || []).map(Number);
      if (rgba.length && (rgba.length < 4 || rgba[3] > 0.95)) return !isGround(el);
    }
    return false;
  };

  const out = [];
  for (const el of document.querySelectorAll('body *')) {
    const text = (el.textContent || '').trim();
    if (!text) continue;
    if (Array.from(el.children).some(c => (c.textContent || '').trim())) continue;
    const svg = !!el.closest('svg');
    if (svg && el.tagName.toLowerCase() !== 'text') continue;
    const s = getComputedStyle(el);
    if (s.display === 'none' || s.visibility === 'hidden') continue;
    // SVG text is painted by `fill`, not by `color`. Reading `color` here gets
    // whatever the document inherited, which on a light deck is the dark body
    // ink, and every white label on a dark shape is then measured as dark on
    // dark and reported at 100% under the floor. The reverse hides real defects
    // just as quietly, so take the property that actually paints the glyph.
    const paint = svg ? s.fill : s.color;
    const rgba = (paint.match(/[\\d.]+/g) || []).map(Number);
    if (rgba.length < 3 || (rgba.length > 3 && rgba[3] < 0.9)) continue;
    const range = document.createRange();
    range.selectNodeContents(el);
    const rects = Array.from(range.getClientRects()).filter(r => r.width > 1 && r.height > 1);
    if (!rects.length) continue;
    const artwork = rects.some(r => {
      const y = r.top + r.height / 2;
      for (let i = 1; i <= 5; i++) {
        const x = r.left + (r.width * i) / 6;
        if (x >= 0 && x <= 1280 && y >= 0 && y <= 720 && onArtwork(el, x, y)) return true;
      }
      return false;
    });
    if (!artwork) continue;
    const size = parseFloat(s.fontSize) || 0;
    const lines = rects.map(r => ({left: r.left, top: r.top, right: r.right, bottom: r.bottom}));
    out.push({
      name: name(el), text: text.slice(0, 28), color: rgba.slice(0, 3), size, lines,
      large: size >= LARGE || (size >= LARGE_BOLD && parseInt(s.fontWeight, 10) >= 700),
    });
  }
  return out;
}""".replace("LARGE_BOLD", str(LARGE_BOLD_PX)).replace("LARGE", str(LARGE_PX))


def _luminance(rgb):
    import numpy as np

    channel = np.asarray(rgb, dtype=np.float64) / 255.0
    linear = np.where(channel <= 0.04045, channel / 12.92, ((channel + 0.055) / 1.055) ** 2.4)
    return linear @ np.array([0.2126, 0.7152, 0.0722])


def contrast_defects(ground: bytes, runs: list[dict]) -> list[str]:
    """Every run against the slide it is actually printed on."""
    import io

    import numpy as np
    from PIL import Image

    image = np.asarray(Image.open(io.BytesIO(ground)).convert("RGB"), dtype=np.float32)
    height, width, _ = image.shape
    found: list[str] = []
    for run in runs:
        floor = CONTRAST_FLOOR_LARGE if run["large"] else CONTRAST_FLOOR
        ink = float(_luminance(run["color"]))
        under = total = 0
        for line in run["lines"]:
            x0, x1 = max(0, int(line["left"])), min(width, int(math.ceil(line["right"])))
            y0, y1 = max(0, int(line["top"])), min(height, int(math.ceil(line["bottom"])))
            if x1 - x0 < 2 or y1 - y0 < 2:
                continue
            lum = _luminance(image[y0:y1, x0:x1])
            ratio = (np.maximum(lum, ink) + 0.05) / (np.minimum(lum, ink) + 0.05)
            under += int((ratio < floor).sum())
            total += ratio.size
        if not total:
            continue
        share = under / total
        if share > CONTRAST_FRACTION:
            found.append(
                f"{round(100 * share)}% of {run['name']} is under the "
                f"{floor}:1 contrast floor against what is behind it: \"{run['text']}\""
            )
        elif share > CROSSING_FRACTION:
            found.append(
                f"{run['name']} runs {round(100 * share)}% off its clear ground onto "
                f"artwork it cannot be read against: \"{run['text']}\""
            )
    return found


def capture(slides: list[dict], outdir: Path) -> tuple[list[Path], dict[str, list[str]]]:
    """Screenshot every slide and, in the same pass, measure what escaped it."""
    from playwright.sync_api import FloatRect, sync_playwright

    clip: FloatRect = {"x": 0, "y": 0, "width": 1280, "height": 720}
    outdir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    defects: dict[str, list[str]] = {}
    with sync_playwright() as play:
        browser = play.chromium.launch(args=["--allow-file-access-from-files", "--disable-web-security"])
        page = browser.new_page(viewport={"width": 1280, "height": 720})
        for index, slide in enumerate(slides, start=1):
            slide_path = slide.get("path") or slide.get("file")
            if slide_path and Path(slide_path).is_file():
                page.goto(f"file://{Path(slide_path).resolve()}", wait_until="load")
            else:
                page.set_content(slide.get("html") or "", wait_until="load")
            page.wait_for_timeout(120)
            path = outdir / f"slide-{index:02d}.png"
            page.screenshot(path=str(path), clip=clip)
            written.append(path)
            found = page.evaluate(_PROBE)
            runs = page.evaluate(_RUNS)
            if runs:
                hidden = page.add_style_tag(content=_HIDE_TEXT)
                found += contrast_defects(page.screenshot(clip=clip), runs)
                hidden.evaluate("node => node.remove()")
            if found:
                defects[str(index)] = found
        browser.close()
    return written, defects


def contact_sheet(shots: list[Path], path: Path) -> Path | None:
    try:
        from PIL import Image
    except ImportError:
        return None
    tile_h = SHEET_TILE_WIDTH * 720 // 1280
    rows = -(-len(shots) // SHEET_COLUMNS)
    sheet = Image.new("RGB", (SHEET_COLUMNS * SHEET_TILE_WIDTH, rows * tile_h), (255, 255, 255))
    for index, shot in enumerate(shots):
        row, column = divmod(index, SHEET_COLUMNS)
        tile = Image.open(shot).convert("RGB").resize((SHEET_TILE_WIDTH, tile_h))
        sheet.paste(tile, (column * SHEET_TILE_WIDTH, row * tile_h))
    sheet.save(path)
    return path


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("slides")
    parser.add_argument("--out", required=True)
    parser.add_argument("--no-sheet", action="store_true")
    args = parser.parse_args(argv[1:])

    slides = load_slides(Path(args.slides))
    outdir = Path(args.out)
    shots, defects = capture(slides, outdir)
    for shot in shots:
        print(shot)
    if not args.no_sheet:
        sheet = contact_sheet(shots, outdir / "contact-sheet.png")
        if sheet:
            print(sheet)

    (outdir / "layout.json").write_text(
        json.dumps(defects, indent=2, sort_keys=True), encoding="utf-8"
    )
    if defects:
        print("\nlayout defects, measured in the browser:", file=sys.stderr)
        for index in sorted(defects, key=int):
            for issue in defects[index]:
                print(f"  slide {index}: {issue}", file=sys.stderr)
        print(
            "\nThese are geometry, not taste. Fix them before you judge the "
            "design, and never by going under a readability floor.",
            file=sys.stderr,
        )
        return 1

    print(
        f"\n{len(shots)} PNGs in {outdir}, no slide clipped. Open them. A shot "
        "you did not look at is not evidence.",
        file=sys.stderr,
    )
    return 0 if shots else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
