# Troubleshooting

## `slidify doctor` shows missing binaries

| Missing                | Install (Debian/Ubuntu)                          |
|------------------------|--------------------------------------------------|
| LibreOffice            | `apt-get install -y libreoffice-impress`         |
| Tesseract OCR          | `apt-get install -y tesseract-ocr`               |
| poppler-utils          | `apt-get install -y poppler-utils`               |
| Inter font             | `apt-get install -y fonts-inter`                 |
| Chromium (Playwright)  | `playwright install chromium --with-deps`        |

Or use the prebuilt image: `docker run --rm slidify:latest doctor`.

## `editability_passed=false`

The emitter's intended shape count for a slide differed from what survived
to disk. Causes, in order of frequency:

1. **A unit's bbox collapsed to 0×0** during promotion. Inspect the slide
   HTML for elements with `display:none` or `visibility:hidden` that are
   still in the DOM tree.
2. **Hint-driven skip went too far**. Review `data-pptx-skip="true"`
   markers — they remove the element AND its descendants.
3. **A native shape's geometry was unrepresentable** (e.g., a path with
   a degenerate curve). The emitter falls back to raster but the count
   bookkeeping flagged it. Safe to ignore if SSIM passes.

## `native_area_ratio` low (<0.6)

Most common offenders, ranked:

1. `background-image: url(...)`              — inline as data URI or use gradient
2. `filter: blur(...)` on visible content    — drop the filter
3. `<canvas>`                                — replace with inline SVG
4. `transform: rotate(N)` for arbitrary N    — use 0/90/180/270
5. SVG `<filter>` / mask                     — use simpler shapes
6. `@font-face` with custom font             — use Inter

Run `slidify compat --level raster` to see every property that forces a raster.

## LLM tier 3 is doing nothing

```bash
slidify doctor --json | jq '.checks[] | select(.name=="LLM backend")'
```

If `ok: false`, set one of:

* `ANTHROPIC_API_KEY=...`               (anthropic backend)
* `GEMINI_API_KEY=...`                  (gemini-aistudio backend)
* `GOOGLE_CLOUD_PROJECT=... GOOGLE_CLOUD_LOCATION=...`  (Vertex backends)

Or pass `--no-tier3` to silence the warning when running offline.

## "no slides produced from source"

The HTML had no `<!DOCTYPE html>` boundaries and contained no slide content
the splitter could detect. Either:

* Add `<!DOCTYPE html>` between slides in a multi-slide file, OR
* Pass a directory of per-slide files (`slide-01.html`, `slide-02.html`, …).

## Conversion is slow (>5s per slide)

* Add `--render-concurrency 8` (only helps if you have CPU cores).
* Add `--no-oracle` for previews.
* Add `--no-tier3` if the LLM is the bottleneck (check `llm_calls` in the report).
* On huge decks, add `--low-memory`.

## Output looks blurry / fonts substituted

Check that the deck face is **Inter** (slidify embeds it by default). If you
disabled with `--no-embed-fonts`, the destination machine must have Inter
installed.
