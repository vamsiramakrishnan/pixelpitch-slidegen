# Agent quickstart

A 60-second tour for an LLM agent that has never used slidify before.

## Step 1 — verify the environment

```bash
slidify doctor --json
```

Look at `checks[].ok`. Any required check failing means: install the missing
binary or run inside the slidify Docker image (see `slidify guide binary`).

## Step 2 — discover the surface

```bash
slidify manifest                        # brief: list of commands
slidify manifest convert                # detailed: convert spec, schema
slidify guide                           # list of long-form guides
slidify guide authoring                 # how to write good HTML
```

## Step 3 — convert

```bash
# From a file
slidify convert deck.html out.pptx --json

# From stdin (no temp files needed)
cat slide.html | slidify convert - out.pptx --json

# Fast preview (no oracle, no LLM)
slidify convert deck.html out.pptx --no-oracle --no-tier3 --json
```

## Step 4 — interpret the result

In `--json` mode the response is a `ConversionResult`:

* `native_area_ratio`           — 0..1, **higher is better** (more editable).
* `editability_passed`          — bool. False means shapes were silently dropped.
* `editability_failing_slides`  — list of slide indices to inspect.
* `fidelity_reports[].passed`   — per-slide SSIM/OCR check.
* `unmatched_signatures`        — Tier-0 candidates (run `slidify harvest`).
* `_next`                       — suggested follow-up commands.
* `_remediation` (on error)     — actionable fix suggestions.

## Step 5 — extract a single field without jq

```bash
slidify field out.json native_area_ratio        # → 0.873
slidify field out.json fidelity_reports.0.ssim  # → 0.961
```

## Step 6 — iterate

```bash
slidify guide authoring --section "What forces a raster"
slidify guide authoring --grep "decorate"
```

If a slide's `native_area_ratio` is low, the most common causes:

1. `background-image: url(...)` → swap for inline gradient or SVG.
2. `filter: blur()` on visible elements → drop the blur.
3. `<canvas>` → render to inline SVG instead.
4. Custom `@font-face` → use Inter.

## Recipe: a robust agent loop

```
1. slidify doctor --json                       # confirm env
2. (write slide.html using `slidify guide authoring`)
3. slidify convert slide.html out.pptx --json --report-json report.json
4. if .editability_passed == false → re-emit failing slides
5. if .native_area_ratio < 0.85   → read report, swap raster offenders, retry
```
