"""Compare OCR settings on real HTML and exported PPTX renders, without cloud I/O.

Run with the renderer interpreter and PYTHONPATH pointing to vendor/slidify.
The output directory contains both renders and the words each OCR mode reads.
"""

from __future__ import annotations

import argparse
import asyncio
import io
import json
from pathlib import Path

from PIL import Image, ImageOps, ImageStat
import pytesseract
from slidify.oracle import _normalize_words, compute_ocr_recall, render_pptx_to_pngs
from slidify.renderer import Renderer


async def check(slides: Path, pptx: Path, out: Path, compare_modes: bool = False):
    out.mkdir(parents=True, exist_ok=True)
    candidates = await render_pptx_to_pngs(pptx)
    files = sorted(slides.glob("*.html"))
    if len(files) != len(candidates):
        raise ValueError("Source and exported slide counts differ")
    results = []
    async with Renderer() as renderer:
        for index, (source, candidate) in enumerate(zip(files, candidates), 1):
            rendered = await renderer.render(source.read_text())
            if rendered.degraded:
                raise ValueError(rendered.reason)
            gt = rendered.ground_truth_png
            (out / f"source-{index:02d}.png").write_bytes(gt)
            (out / f"pptx-{index:02d}.png").write_bytes(candidate)
            modes = []
            for psm in ((3, 6, 11) if compare_modes else ()):
                for scale in (1, 2):
                    words = []
                    for data in (gt, candidate):
                        im = Image.open(io.BytesIO(data)).convert("RGB")
                        if scale != 1:
                            im = im.resize((im.width * scale, im.height * scale))
                        text = pytesseract.image_to_string(im, config=f"--psm {psm}")
                        words.append(_normalize_words(text))
                    expected, actual = words
                    modes.append({
                        "psm": psm, "scale": scale,
                        "recall": len(expected & actual) / len(expected) if expected else None,
                        "expected": sorted(expected), "actual": sorted(actual),
                        "missing": sorted(expected - actual),
                    })
            recall, expected, actual = compute_ocr_recall(gt, candidate, elements=rendered.elements)
            results.append({"slide": index, "text_region_recall": recall, "modes": modes})
            regions = []
            for element in rendered.elements:
                if not element.text or not element.text.strip():
                    continue
                box = element.bbox
                crop = (max(0, box.x - 12), max(0, box.y - 12),
                        min(1280, box.x + box.w + 12), min(720, box.y + box.h + 12))
                texts = []
                for data in (gt, candidate):
                    im = Image.open(io.BytesIO(data)).convert("RGB").crop(crop)
                    im = ImageOps.grayscale(im)
                    if ImageStat.Stat(im).median[0] < 128:
                        im = ImageOps.invert(im)
                    im = ImageOps.autocontrast(im)
                    im = im.resize((im.width * 3, im.height * 3))
                    texts.append(pytesseract.image_to_string(im, config="--psm 6"))
                regions.append({"expected": element.text, "source": texts[0], "candidate": texts[1]})
            results[-1]["regions"] = regions
            print(json.dumps({"slide": index, "text_region_recall": recall}), flush=True)
            print(json.dumps({"slide": index, "modes": [
                {k: m[k] for k in ("psm", "scale", "recall", "missing")}
                for m in modes
            ]}), flush=True)
    (out / "ocr.json").write_text(json.dumps(results, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--slides", type=Path, required=True)
    parser.add_argument("--pptx", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--compare-modes", action="store_true", help="Also compare legacy whole-page OCR modes")
    args = parser.parse_args()
    asyncio.run(check(args.slides, args.pptx, args.out, args.compare_modes))


if __name__ == "__main__":
    main()
