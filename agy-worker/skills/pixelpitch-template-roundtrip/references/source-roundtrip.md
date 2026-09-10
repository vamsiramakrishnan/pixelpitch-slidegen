# Source round-trip: PPTX → exact HTML → enrich → native PPTX

The highest-fidelity creative route. The customer's actual template supplies
the construction; the harness reconstructs it as HTML exactly, then enriches
it with the new narrative and visual aids; slidify converts back to a fully
native, editable PPTX. Fidelity from extraction, editability from HTML→native
conversion, creativity in between.

## Pipeline

1. **Extract** (mechanical):
   `scripts/extract_slide_spec.py --template template.pptx --outdir spec-out`
   produces, per slide:
   - `spec/slide-NN.json` — the full shape tree in 1280×720 px space:
     geometry, fills (solid/gradient/scheme), strokes, rotation, placeholder
     roles, text runs with exact font/size/weight/colour/align, group trees,
     and picture blobs saved to `spec/media/`;
   - `base/slide-NN.png` — a pixel-exact 1280-wide render of the slide.
2. **Reconstruct** (judgement, yours): author `slides/slide-NN.html` per
   specimen, 1280×720 pinned per the slidify contract:
   - **Mode A — clean plate + native overlay** (artwork-heavy slides): the
     `clean/slide-NN.png` you produced (base render with every text region
     painted out — see the protocol below) becomes the full-bleed `<img>`
     layer; every text element sits above it as real HTML at the extracted
     coordinates with the extracted run styles. NEVER overlay text onto the
     raw `base/` render — it still contains the template's placeholder copy
     and will double-expose.
   - **Mode B — plate plus vector aid** (data/text slides): STILL embeds the
     specimen's clean plate (even a white specimen carries the template's
     furniture: footer, hairline rules, margins). A slide with NO plate is a
     freehand invention and is prohibited. On the plate, rebuild only what
     must be native (text, the aid) from `spec/slide-NN.json` geometry and
     run styles.
   Every slide embeds a plate. "Mode A vs B" only decides how much is
   vectorised on top of it.
3. **Enrich**: replace copy with the assertion-title narrative; add visual
   aids (chart / timeline / stat / proportion) in the derived contract's
   palette and `visual_aids_style`. THE AID MUST BE BUILT FROM THE TEMPLATE'S
   OWN FURNITURE VOCABULARY, observed on the specimens — e.g. this template's
   data style is hairline bottom-border rules with labels left and bold
   right-aligned numerals, its type scale from the spec, its blue-then-green
   accent logic — NOT generic AI chart furniture (rounded card grids,
   "Insight:" strips, pill chips). Ask: could the template's own designer
   have drawn this aid? If not, redraw it.
   Never touch protected material (see the contract).
4. **Convert back**: assemble `deck.html` (one file, one `.slide` section per
   slide per the slidify grammar) and convert locally so relative media paths
   resolve:
   `renderer/.venv/bin/slidify convert deck.html deck.pptx --no-oracle --no-tier3 --report-json slidify-report.json`
5. **Gate**: `scripts/lint_slides.py slides/slides.json brand-contract.json`
   must print `{}`; `slidify-report.json` must show `native_area_ratio`
   healthy and zero overflow; rasterise (`soffice` → `pdftoppm`) and compare
   each result against `base/slide-NN.png` — the artwork, header/footer and
   lockups must be indistinguishable at a glance, with the new narrative and
   aids the only visible change.
6. **Deliver**: `deck.pptx`, `slides/`, `preview/*.png`, `report.json`.

## Honest boundaries

- Effects HTML cannot express (3-D, SmartArt, OLE embeds, some masks) stay
  pixel-exact via Mode A's image layer — they become pictures, which is what
  they already were visually. Text and visual aids stay native.
- Scheme colours resolve through the extracted theme values in
  `brand-contract.json`; never guess a hex.
- Extraction gives you facts (geometry, styles, blobs); interpretation of the
  composition — what is artwork vs. furniture vs. content — is your judgement,
  backed by the `refs/` renders and the contract's layout worlds.

## Clean plates, correct HTML/CSS, and the iterative visual check

The base render of a specimen contains its placeholder copy ("Headline
90pt", counters, footers). Overlaying new text on that base DOUBLE-EXPOSES:
both texts print, both garble. This protocol prevents it — as harness work,
not special code.

### 1. Make a clean plate per slide (agent-executed)

From `spec/slide-NN.json`, take every text-bearing shape's rectangle
(`x/y/w/h` in 1280x720 px space, recursing into groups). Produce
`clean/slide-NN.png` from `base/slide-NN.png` by filling each text rect
(grown a few px) with the sampled ground colour — sample just OUTSIDE the
rect so the fill matches its panel (white card, blue field, photo edge).
Any tool you like (a PIL one-liner you write yourself is fine); the result
is artwork-only: curves, photos, lockups, fills, ZERO text. Keep plates in
the workspace; they are ordinary artifacts, not code.

### 2. Overlay correct HTML and CSS (not approximate)

Every text element is native overlay on the clean plate, and its HTML+CSS
must match the extracted facts, not eyeball guesses:
- position: absolute, `left/top/width` from the spec rectangle, in the same
  1280x720 px space as the extraction;
- `font-family`, `font-size` (pt values from the spec converted to px),
  `font-weight`, `line-height`, `color`, and alignment from the run styles
  in the spec (`text[].runs[]`);
- the template's furniture (footer tagline, page furniture you keep) is
  re-created as overlay text with the same fidelity — it was painted out of
  the plate, so it must be re-added to exist.

### 3. Iterate until the image says clean (multimodal self-check)

After every conversion, rasterise and LOOK at each slide — open the rendered
PNG and compare it against BOTH `clean/slide-NN.png` (artwork must be
identical) and `base/slide-NN.png` (text must sit in the same zones with the
same visual weight). Hunt specifically for:
- a slide with no plate under it — freehand invention, rebuild it on its
  specimen plate;
- an added aid that does not look like the template's own designer drew it
  (generic card grids, "Insight:" strips, pill chips) — redraw in the
  template's furniture vocabulary;
- any residual placeholder glyphs ("Headline", "90pt", "Roboto", digits like
  "01", "Click to add") — double-exposure;
- baselines that interleave or overprint — the overlay is mis-positioned;
- text spilling outside its extracted rect — the CSS size or wrapping is
  wrong.

When you find any, CORRECT THE HTML AND CSS (position, size, weight, colour
— the spec has the true values) and re-convert. Do not conclude on gates
alone; the image is the gate. Only a render you have personally inspected
and found free of residual text and collisions may be reported clean —
dishonest `residual_text_notes: None` is a protocol failure.
