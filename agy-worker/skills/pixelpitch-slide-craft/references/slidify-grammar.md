---
name: slide-author
description: Author HTML slide decks for the slidify pipeline. Trigger when the user asks for a deck, presentation, landing-page-quality slides, a hero/CTA/feature slide, or wants to write new HTML that will be converted to PPTX via slidify. This skill teaches the atomic-seed grammar (10 axes × atoms × typographic registers) so the produced HTML stays inside the slidify native envelope and never overflows the 1280×720 frame. Pair with the `html-to-slides` skill, which handles the conversion step.
---

# slide-author

Author single-file HTML decks that compile to **maximally-editable PPTX**
through `slidify`. This skill exists because LLM-authored slides repeatedly
hit two failure modes:

1. **Layout overflow** — content spills past 720px or clips at 1280px because
   font sizes and row heights weren't budgeted against the viewport.
2. **Accidental raster fallback** — author reaches for `mix-blend-mode`,
   `filter: blur`, `mask-image`, `backdrop-filter`, or
   `background-image: url(...)` without intending a raster/effect fixture,
   and the whole cluster rasterizes, killing editability.

Both are author-side. Both are preventable with a small grammar — the **atomic
seed**. Intentional raster is different: when the design depends on masks,
blends, rich photography, or canvas-like pixels, preserve the visual layer and
make the fixture explicit so the harvester can route it to a hybrid recipe or
raster-fidelity regression case.

## Libraries you can reach for (LLMs are good at HTML+CSS — use real libraries)

LLMs already know how to compose with these. Slidify converts them
**natively** when the slide is otherwise self-contained. Inline the CSS
they generate (or write the equivalent vanilla CSS yourself); don't pull
the runtime JS.

| Library | What's safe | What to skip |
|---|---|---|
| **Tailwind v3 / v4 utilities** | All utilities — color, spacing, type scale, gradient, shadow, ring, transform: translate/scale/rotate. `@apply` an inline class chain into a `<style>` block, or write the resolved CSS directly. | `backdrop-blur-*`, `mix-blend-*`, `filter blur-*`, `clip-path-*` (raster). |
| **shadcn/ui (static subset)** | Card, Button, Badge, Alert, Avatar (+ AvatarStack), Separator, Progress (static), Tabs body, Accordion body, Skeleton, Toast (static), HoverCard body, Tooltip body. | Dialog, Dropdown, Command, Sheet, Popover, anything that requires a portal or real interactivity (no JS runs at convert time). |
| **lucide-react / lucide icons** | Inline `<svg>` icons. Always 24×24 viewBox, stroke-2, `stroke-linecap:round`, `stroke-linejoin:round`, `fill:none`. Each icon is ≤6 path elements — well under the 200-primitive native budget. Copy the SVG straight from `lucide.dev/icons/<name>` (ISC). The reference set in `_bench/decks/llm-corpus/generate.py::LUCIDE_ICONS` covers 22 of the most-used ones. | None — every lucide icon converts. |
| **Framer Motion** | `motion.div` annotated `data-slidify-capture-gif="true"` becomes an animated GIF embedded in the slide via `slidify capture-gif`. | Animations triggered by user interaction (hover, scroll, click) — they never fire because there's no user. |
| **shadcn-style class names** | Inline-emitted via Tailwind's compile output. The matcher can ignore class names entirely; data-atom hints take precedence. | — |
| **Custom inline `<svg>`** | Always native (≤200 primitives). | `filter="url(#blur)"` defs. |

The **shadcn / Tailwind / lucide / Framer-Motion** quartet is the assumed
default vocabulary. You don't need to invent components — pick from those,
and the slide is on the fast path.

For a working pattern catalog spanning six theme registers (vercel-dark,
paper, magazine, brutalist, mono-spec, duotone) plus icon-driven dashboard
and feature-grid slides, see `_bench/decks/llm-corpus/`. Every slide there is
self-contained HTML, passes `slidify check`, and converts natively. Use it
as a reference when you're not sure which patterns land cleanly.

## Pre-flight: `slidify check` is your inner loop

Before you finalize a slide, run:

```bash
slidify check slide.html --json
```

What it tells you (≤100 ms, no Chromium):

```json
{
  "self_contained": true,
  "external_assets": [],
  "risky_css": [
    { "property": "backdrop-filter", "value": "blur(20px)",
      "selector": ".glass",
      "reason": "PPTX has no native backdrop-filter; routes to raster fallback." }
  ],
  "atom_hints": ["comp.hero-investor", "type.big-number-gradient"],
  "warnings": []
}
```

**Treat as regeneration triggers**:

- `self_contained: false` → pull the external asset inline (data: URI for
  images, inline `<style>` for CSS, drop the script).
- `risky_css` non-empty → swap accidental risk to a native equivalent (see
  "What forces a raster" below) OR explicitly keep the visual as a
  hybrid/raster fixture when the effect is the point.
- `warnings` (iframe, missing `<!doctype html>`, …) → fix.

Add `--deep` for the full matcher pass (Chromium round-trip; ~1–3 s):

```bash
slidify check slide.html --deep --json
```

`--deep` adds `native_area_ratio`, top unmatched signatures, and the
escape-rate prediction. Use this in CI / corpus runs, not the inner loop.

CLI exit codes: **0** if clean, **2** if non-self-contained or risky CSS
present. Pipelines can gate:

```bash
slidify check slide.html --json && slidify convert slide.html out.pptx
```

## The hard contract (non-negotiable)

1. **Viewport is exactly 1280 × 720 px.** Pin it on every slide:
   ```html
   html,body { margin:0; padding:0; width:1280px; height:720px;
                font-family: Inter, sans-serif; }
   .slide   { width:1280px; height:720px; box-sizing:border-box;
              position:relative; overflow:hidden; }
   ```
2. **Single self-contained HTML.** All CSS inline in `<style>`. No external
   stylesheets, no JS, no `<link>`. Inline `<svg>` is allowed and encouraged.
3. **Multi-slide files** split on `<!DOCTYPE html>` boundaries.
4. **Title.** Mark exactly one element per slide with `data-pptx-role="title"`.
5. **No `background-image: url(...)`** — slidify won't fetch background URLs.
   Use `<img>` tags instead (those *are* fetched and re-embedded as native
   pictures), or synthesize imagery from inline SVG / gradients.
6. **No `filter`, no `mix-blend-mode`, no `backdrop-filter`, no `mask-image`,
   no `clip-path: path()` / `url(#mask)`.** Each forces a raster fallback.
   Use the atom-approved alternatives below.

`data-pptx-role` and `data-pptx-allow-overflow` are two of about a dozen
attributes that overrule the classifier when it reads a slide wrong. The full
set, with what each one costs, is [references/slidify-hints.md](slidify-hints.md);
`scripts/test_slidify_hints.py` checks it against `slidify compat` so the page
cannot outlive the converter.

## Viewport math (memorize this)

```
slide:                     1280 × 720
typical padding:               64–80
head (kicker + h1):          ~  80
footer (label + page #):     ~  50
content budget:              ≈ 510 px tall   (720 − 80 − 50 − 80 = 510)
```

Every layout fits inside that 510 px. Subtract gutters too:

| Rows in content area | Gap | Per-row max height |
|----------------------|-----|--------------------|
| 2 rows               | 18  | 246 px             |
| 3 rows               | 14  | 161 px             |
| 4 rows               | 10  | 120 px             |
| 5 rows               | 8   | ~94 px             |

If display type is taller than the row, **shrink the type, not the row**.
Display headlines have hard ceilings per row size:

| Row height | Max display size (Inter weight 800–900) |
|-----------:|------------------------------------------|
| 90 px      | 48 px                                    |
| 120 px     | 64 px                                    |
| 160 px     | 84 px                                    |
| 240 px     | 120 px                                   |
| Full bleed | 480 px (one word only)                   |

## The 10 axes — pick one atom from each per cluster

Every award-winning landing-page slide is a stack of these primitives. The
catalog of registered atoms lives at `examples/landing/atoms.html` (the
parts catalog) and `slidify/patterns/data/atoms.yaml` (the emit recipes).

```
comp.*      composition gestures        (centered, split-50, split-60,
                                         golden, thirds, offcanvas,
                                         edge-rotated, bento)
bg.*        background fields           (mesh, conic, stripes, scanline,
                                         duotone, vignette, aurora-band,
                                         dot-lattice)
surf.*      surfaces                    (glass, hero, spotlight, aurora,
                                         tactile, recessed, sticker, polaroid)
type.*      type treatments             (gfill-2, gfill-4, stroke,
                                         stroke-thick, echo, longshadow,
                                         mixed, kinetic-baseline,
                                         marquee, dropcap)
mask.*      media masks                 (disc, arch, hex, parallelogram,
                                         triangle, diamond, rounded, mosaic)
dec.*       decorative tokens           (corner-brackets, registration,
                                         drafting-tick, asterisk,
                                         dot-leader, arrow-glyph,
                                         index-stamp, seal)
data.*      data primitives             (ring, gauge, sparkline, sparkbars,
                                         heatmap, kpi-delta, ticker,
                                         logo-cloud)
motion.*    motion implication          (echo, shutter, speed-lines,
                                         particles, marquee, kinetic)
ui.*        UI mocks                    (browser-chrome, tab-strip,
                                         search, toolbar, avatar-cluster,
                                         toast)
anno.*      annotation primitives       (leader-line, dimension,
                                         target-reticle, callout-pill)
```

Tag the cluster anchor with `data-atom="<id>"`. The slidify matcher
short-circuits to that atom's recipe — no signature inference, guaranteed
native emit, cache hit on repeat runs:

```html
<div class="tile bg-mesh" data-atom="bg.mesh">…</div>
<div class="card" data-atom="surf.glass" data-slidify-decorate="glass">…</div>
<h1 data-atom="type.gfill-4" data-pptx-role="title">…</h1>
```

`data-slidify-decorate="hero|glass|tactile|recessed|aurora|spotlight|orbit"`
adds layered native shape decorations on top of the atom. Use it freely on
cards.

## Composition rule

Atoms compose **by stacking, not by inheriting**. Pick at most one atom per
cluster anchor. A landing-page-grade slide stacks 4–8 atoms across axes:

```
1 × comp.*    +   1 × bg.*       (the frame)
1 × surf.*    +   1 × type.*     (the focal panel + headline)
1–3 × data.* / motion.* / dec.*  (the embellishments)
```

Recipe deck `examples/landing/recipes.html` shows 16 worked examples.

## Typographic registers

The font stack changes the entire register of the slide. Pick one per
deck (or per chapter); don't mix more than 2 registers on one deck.

| Register     | Display family               | Body family            | Use for |
|--------------|------------------------------|------------------------|---------|
| Editorial    | `Fraunces` italic            | `Source Serif Pro`     | Long-form, pull-quotes |
| Tech         | `Space Grotesk`              | `Inter`                | Product, API, changelog |
| Magazine     | `Bebas Neue` UPPERCASE       | `Source Serif Pro`     | Issue covers, features |
| Brutalist    | `Helvetica Neue` 900 UPPER   | `JetBrains Mono`       | Manifesto walls, zines |
| Manifesto    | `Inter` weight axis 200→900  | `Inter` 300            | Vision keynotes, posters |
| Blueprint    | `JetBrains Mono` 700         | `IBM Plex Mono`        | Technical specs, drafting |
| Luxury       | `Playfair Display` italic    | `Inter` tracked        | Brand reveals, awards |
| Warm         | `Inter` 600 + `Caveat`       | `Inter` 500            | Retros, onboarding |

Always include reasonable system fallbacks in the stack (slidify embeds the
first family it can resolve via fontconfig):
```css
font-family: "Fraunces", "Source Serif Pro", Georgia, serif;
```

## What renders natively (use these aggressively)

* `linear-gradient` / `radial-gradient` / `conic-gradient` (multi-stop OK)
* `repeating-linear-gradient` (stripes, scanlines)
* Multi-layer `box-shadow` (each layer is one native outer shadow)
* `border` + `border-radius` (ROUNDED_RECTANGLE / OVAL when radius is large)
* `-webkit-background-clip: text` over a gradient (gradient-fill text)
* `-webkit-text-stroke` (outline display type)
* Inline `<svg>` with `<rect>`, `<circle>`, `<line>`, `<polygon>`, `<path>`
  (≤200 primitives per SVG)
* `clip-path: polygon(...)` for common shapes (hex, diamond, parallelogram,
  triangle), `clip-path: circle(...)`, `clip-path: inset(...)`
* `border-radius: 50%` for discs, `border-radius: 100px 100px 14px 14px` for
  arch shapes
* Per-`<span>` font-weight / font-style / color / font-family changes

## What forces a raster (avoid)

| Property/feature                          | Why                                           |
|-------------------------------------------|-----------------------------------------------|
| `filter: blur(...)` / `hue-rotate(...)`   | No PPTX equivalent, whole cluster rasters     |
| `mix-blend-mode: ...`                     | OOXML blend grammar can't reproduce           |
| `backdrop-filter: blur(...)`              | Frosted glass needs live blur, baked as raster|
| `mask-image: linear-gradient(...)`        | No native mask grammar                        |
| `clip-path: path(...)` / `url(#mask)`     | Raster fallback                               |
| `background-image: url(...)`              | URL not fetched (use `<img>` instead)         |
| `<canvas>`                                | Pixels captured by screenshot pass            |
| `text-shadow`                             | **Unsupported, dropped silently** — use a stack of offset spans |
| `transform: rotate(N)` for arbitrary N    | Native for orthogonal angles only             |

## The mechanical alternatives

| Want                    | Use instead                                                 |
|-------------------------|-------------------------------------------------------------|
| Frosted glass           | `surf.glass` decoration: rim highlight + 1px border + inset glow shadow |
| Photo-fill text         | Gradient-fill text via `bg-clip-text` over a multi-stop linear-gradient |
| Long-shadow display     | 8-deep span stack with per-step `transform: translate()` + decreasing opacity (atom: `type.longshadow`) |
| Motion blur / smear     | Echo stack: same word stamped 4× with offset + opacity ramp (atom: `type.echo`) |
| Backdrop blur card      | `surf.glass` (already simulates depth via shadow stack)     |
| Photo behind text       | `<img>` tag (native picture) + a low-alpha solid overlay   |
| Custom blob mask        | `clip-path: polygon(...)` approximation, OR pick the closest atom (`mask.arch`, `mask.hex`) |

## Your authoring loop

```bash
# 1. Sketch HTML referencing data-atom IDs.
# 2. Convert + read the report:
slidify convert deck.html out.pptx \
    --no-oracle --no-tier3 \
    --report-json report.json --quiet

# 3. Read three fields. ANY of these failing means revise the HTML:
slidify field report.json native_area_ratio          # target: ≥ 0.95
slidify field report.json overflow_elements          # target: []   ← every entry is an authoring bug
slidify field report.json pattern_hits               # confirm atom-* recipes fired
```

`--no-oracle` is not an optimisation, it is what makes the number above
trustworthy. The fidelity oracle needs LibreOffice. Without it every slide
comes back `passed=False` with no failing regions, which the oracle reads as
"rasterise the whole thing", so a perfectly native deck reports
`native_area_ratio 0.0` and still exits 0. A zero here is far more often a
missing binary than a bad slide. Before believing one, re-run with
`--no-oracle` and compare, or count `pptx.shapes.PICTURE` in the output
directly. Turn the oracle back on only when `soffice` is on PATH.

When `overflow_elements` is non-empty, each entry tells you exactly what
overflowed and — when an atom is implicated — what to do about it:

```json
{
  "slide_index": 3,
  "axis": "bottom",
  "overflow_px": 135.0,
  "data_atom": "type.dropcap",
  "stable_selector": "body > div:nth-child(1) > div:nth-child(2) > div:nth-child(10)",
  "sample_text": "There is a moment in every render…",
  "hint": "atom `type.dropcap`: lower the ::first-letter font-size or widen the body's container."
}
```

Read the `hint` first. The pipeline ships an atom-keyed hint table for the
common authoring bugs (each row in `data-atom`); when no atom is matched,
the hint falls back to a per-axis viewport-math reminder ("right edge
crossed by N px — trim the line, lower font-size, or wrap; viewport width
is 1280 px"). Use the `data_atom` and `stable_selector` to find the
offending element, then either:

* shrink the type (per the size table above),
* shrink the row,
* split the slide,
* or — only when the bleed is genuinely intentional — tag the element
  with `data-pptx-allow-overflow="true"`.

**Do not** ignore overflow even if the slide visually "looks ok" in the
PNG — slidify also emits `<a:normAutofit/>` as a runtime safety net, but
the rendered PNG layout is what authors rely on, and PowerPoint's autofit
is heuristic.

## Pipeline-side rules the compiler enforces for you

The atomic seed is a two-sided contract: authors stay inside this grammar,
and the pipeline plays by the same rules so well-formed atoms never trip
spurious warnings. Three rules are enforced automatically — you don't
have to remember them, but knowing they exist explains the report:

1. **Allow-overflow inheritance for overflow-by-design atoms.** Tagging a
   cluster anchor with `data-atom="type.echo"`, `type.longshadow`,
   `type.marquee`, `motion.echo`, `motion.marquee`, or
   `motion.speed-lines` automatically grants every descendant the
   equivalent of `data-pptx-allow-overflow="true"`. Echo trails, ghost
   spans, and marquee tapes are *defined* by their bleed; you don't need
   to mark each leaf span individually. The detector ignores them and the
   shapes still emit natively.
2. **Native lines count as editable.** SVG `<line>`, `<path>`, and
   `<polyline>` primitives emit as PowerPoint LINE / FREEFORM connectors
   and are individually selectable in PowerPoint. The post-emit
   editability round-trip credits them, so anatomy-style decks dense in
   blueprint annotations no longer flag false drift.
3. **Atom-keyed authoring hints.** When the detector *does* report an
   overflow, it walks up the ancestor chain to find the nearest
   `data-atom` and attaches a one-line, fix-it hint to the report row
   and the CLI summary. You see the action, not just the location.

## Reference corpus

These files are canonical examples. Read them when authoring a new deck:

| File                            | What it demonstrates |
|---------------------------------|----------------------|
| `examples/landing/probe.html`   | Constraint envelope: which type/mask/bg primitives survive |
| `examples/landing/atoms.html`   | Parts catalog: every atom labeled with its `data-atom` id |
| `examples/landing/fonts.html`   | Eight typographic registers, same headline |
| `examples/landing/recipes.html` | 16 award-winning compositions, manifest-tagged with `data-atom-uses` |

## Anti-patterns the overflow detector specifically catches

* Display headline > 64 px in a 90 px row.
* Drop-cap with a 100 px first-letter inside a 100 px content area.
* Rotated edge caption set with `transform: rotate(-90deg)` and no
  `transform-origin` — common 200 px overflow on the right edge.
* Eight tiles in a 4-column grid where each tile's content extends past
  the cell because the cell uses `min-height` instead of `height`.
* Multi-line marquee text overflowing horizontally because of `nowrap`
  inside an oversized `font-size`.

If the overflow detector flags any of these, **fix the HTML, don't dismiss
the warning.** That's the whole point of the compile-time signal.

## HyperFrames-aware authoring (animated / timeline slides)

When a slide needs animated builds, layered media, or a true timeline,
register `window.__hf` per the contract in
`packages/hyperframes-types/src/engine-types.ts` at the repository root:

```js
window.__hf = {
  duration: 8.0,                   // seconds
  seek(t) { /* drive your timeline to time t */ },
  media: [/* HfMediaElement[] */],
  transitions: [/* HfTransitionMeta[] */],
};
```

Slidify rasters or natively-emits the slide at `t = 0` (the first
keyframe). The same artifact is also renderable to MP4 by the upstream
HyperFrames engine — the page contract is what makes both targets work
from one HTML.

For timing across multiple slides, use the relative-timing syntax
`data-start="prev_slide + 2"` — see `packages/hyperframes-types/src/html-parser.ts`
for the resolver. Sibling skill: `hyperframes` (full video / animation
authoring).

## Authoring inside the pixelpitch web app

When this skill runs inside `apps/web/` (the pixelpitch web shell), the
slide HTML you emit is auto-previewed in a sandboxed iframe via
`apps/web/src/runtime/srcdoc.ts`. The iframe vendors React 18 + Babel
(`@pixelpitch/sidecar`), so JSX inside `<script type="text/babel">`
works. Tokens edited in the live tweaks panel patch in via
`apps/web/src/runtime/tweaks-bridge.ts` without remount.

The PPTX export tool call invokes `slidify convert <input> <output>
--json` — see the sibling skill `html-to-slides`. For deck-quality
checklists and 10 magazine layouts, also see the sibling skill
`guizang-ppt`.
