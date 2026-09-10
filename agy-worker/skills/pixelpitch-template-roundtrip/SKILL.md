---
name: pixelpitch-template-roundtrip
description: Use when a customer's own PowerPoint template must be the actual construction of a deck - a .pptx or Google Slides deck was supplied or named, the workspace holds template.pptx or spec-out/ or baselines/ or clean/, the deck must be indistinguishable from the template's own slides, or a render shows double-exposed placeholder text, a slide with no artwork under it, or an aid that does not look like the template's designer drew it.
---

# Pixelpitch template roundtrip

The highest-fidelity route. The customer's template supplies the
construction; you reconstruct it as HTML exactly, enrich it with the new
narrative, and slidify converts back to a native editable PPTX. Fidelity from
extraction, editability from conversion, creativity in between.

## The two rules everything else serves

**Every slide sits on a real specimen's clean plate.** A slide with no plate
is a freehand invention. Even a white specimen carries the template's
furniture: footer, hairline rules, margins.

**Never overlay text onto a `base/` render.** The base still contains the
template's own placeholder copy. Overlaying double-exposes: both texts print,
both garble. Overlay onto `clean/`, always.

`clean/` is read-only. Every plate in it is hash-pinned in `layouts.json`, so
a file written there is an invented ground by definition, whatever it is
named. One deck shipped three slides on `navy-statement.png`,
`blue-statement.png` and `white-content.png` — flat rectangles an authoring
turn typed mid-run — and every gate passed them, because each had an `<img>`
and that was the whole question being asked.

## Pipeline

`pixelpitch-deck`'s `scripts/deck` runs the mechanical steps. The judgement
between them is yours.

| Step | How |
|---|---|
| Extract geometry, media, base renders | `deck spec --template template.pptx --out spec-out/` |
| Paint text regions out to make plates | `deck plates --spec spec-out/ --out clean/` |
| Join geometry to plates, pin both | `deck layouts --spec spec-out/ --clean clean/ --out layouts.json` |
| Reconstruct, enrich | You. [references/source-roundtrip.md](references/source-roundtrip.md) |
| Convert and gate | `deck render`, `deck lint`, `deck diff` |
| Inspect | You. Open the PNG. |

`scripts/extract_slide_spec.py`, `scripts/make_clean_plates.py`,
`scripts/layout_contract.py` and `scripts/build_from_source.py` are the
underlying tools if you need them directly; `deck` is the supported surface
and resolves an interpreter that has their dependencies.

`layouts.json` is where every geometry question is already answered. Per
template slide it gives the archetype, the plate and its hash, whether the
plate survived painting, the `zones` text may go in with the ground colour
measured off the plate under each, and the `furniture` the template repeats
with the media file behind it. Read the layout for the slide you are building
before you write a coordinate. One deck put its title at y=36 and its body at
y=172 on a layout that declares 56.0 and 199.3, and typed the words of the
logo lockup as a sentence rather than pointing at the asset.

`deck spec` needs `soffice` for the base renders. Without it you get geometry
and no plates, which is not the roundtrip route — it says so and you should
believe it.

## Worked artifacts

Both routes are shown end to end, with real coordinates and a real digest, in
[examples/README.md](examples/README.md). The Mode A shell is
[assets/overlay-base.css](assets/overlay-base.css). Open the pair for your
route before authoring the first slide; the mistakes this skill exists to
prevent are all visible in them.

## Which mode per slide

Both modes embed the plate. The mode only decides how much is vectorised on
top of it.

- **Mode A, clean plate plus native overlay.** Artwork-heavy slides. The
  plate is a full-bleed `<img>`; every text element is real HTML above it at
  the extracted coordinates with the extracted run styles.
- **Mode B, plate plus vector aid.** Data and text slides. Same plate, but
  rebuild the text and the aid natively from `spec/slide-NN.json` geometry
  and run styles.

Effects HTML cannot express (3-D, SmartArt, OLE embeds, some masks) stay
pixel-exact inside Mode A's image layer. They were already pictures visually.

## Aids must be in the template's vocabulary

Read the specimens and copy how *this* template draws data. If its data style
is hairline bottom-border rules with left labels and bold right-aligned
numerals, that is your bar chart. Generic AI chart furniture — rounded card
grids, "Insight:" strips, pill chips — is off-template even when it is
pretty.

Ask of every aid: could this template's own designer have drawn it? If not,
redraw it.

Scheme colours resolve through the extracted theme values in
`brand-contract.json`. Never guess a hex.

## Seam-patched decks

When the workspace has `authoring-plan.json` and immutable `baselines/`, you
do not author whole documents. You return typed replacements for declared
seams and the outer process validates and applies them:
[references/seams.md](references/seams.md). A plan, its baseline, and the
`patches.json` that satisfies it are in
[examples/](examples/README.md#the-seam-route).

## What to hunt for in every render

Open the PNG. Compare against `clean/slide-NN.png` (artwork must be
identical) and `base/slide-NN.png` (text in the same zones, same visual
weight). Look specifically for:

- residual placeholder glyphs — "Headline", "90pt", "Roboto", "01", "Click to
  add" — meaning double-exposure;
- a slide with no plate under it;
- an aid in generic vocabulary rather than the template's;
- baselines interleaving or overprinting, meaning the overlay is mispositioned;
- text spilling outside its extracted rect, meaning the CSS size or wrapping
  is wrong.

The spec holds the true values. When you find one of these, correct the
position, size, weight, or colour from the spec and re-convert. Reporting
`residual_text_notes: none` on a render you did not open is a protocol
failure.
