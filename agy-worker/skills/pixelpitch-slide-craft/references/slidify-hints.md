# slidify hints

The attributes that let you overrule slidify's classifier. Everything here is
real today and verified against `slidify compat` by
`scripts/test_slidify_hints.py`, so this page cannot quietly outlive the
converter.

Reach for one only when the default is wrong. A hint on every element is a
deck that has stopped trusting its own CSS.

| Attribute | Put it on | What it does |
|---|---|---|
| `data-pptx-role="title"` | Exactly one element per slide | Names the title. The template's title placeholder, the outline, and the reading order all key off it. |
| `data-pptx-notes="…"` | The slide root | Plain text into `slide.notes_slide`. The argument that did not fit on the slide goes here. `deck stage` reads the same attribute for the presenter pane, so notes are written once. |
| `data-pptx-skip` | Any element | Drop it from the PPTX entirely. For on-screen chrome — a nav rail, a build hint — that has no business in the exported file. |
| `data-pptx-rasterize` | A cluster anchor | Force this subtree to a picture. For a composition you know PPTX primitives will approximate badly, when a faithful image beats a wrong shape. |
| `data-slidify-allow-raster="true"` | A cluster anchor | Consent, not instruction. Says "a raster here is the intended outcome", which stops the checker reporting it as lost editability. |
| `data-pptx-allow-overflow="true"` | The overflowing element | Consent again. Says the bleed past the frame is the design. Echo trails and aurora bands need it; a title that ran long does not. |
| `data-slidify-decorate="hero\|glass\|tactile\|recessed\|aurora"` | A surface | Emit a layered decoration as a native shape stack rather than one flat fill. |
| `data-atom="<id>"` | A cluster anchor | Route the cluster to a catalogue recipe instead of general classification. `deck pick` and `references/slidify-grammar.md` carry the ids. |
| `data-slidify-deck` | The element wrapping all slides | Marks a single-file deck for the splitter, which then normalises the wrapper away per slide. `deck stage` sets it; you should not need to. |

## Two consents, and why they are not the same as the force

`data-pptx-rasterize` changes the output. `data-slidify-allow-raster` and
`data-pptx-allow-overflow` change only the verdict. Using a consent to quiet a
complaint you have not understood is how a deck ships with a defect and a green
report, which is precisely the failure the gate law exists to catch. Write the
consent when you can say in one line why the raster or the bleed is correct.

## When every slide comes back as a picture

Converting locally and getting zero native shapes usually means the machine,
not the deck. slidify's fidelity oracle renders the PPTX back to images and
compares them to the browser; that comparison needs `libreoffice`, `pdftoppm`,
and `tesseract`. With any of them missing the oracle cannot render, scores every
slide at SSIM 0.000, and "auto-corrects" each one to a full-page raster. The
conversion succeeds. The report is green. Every slide is a picture.

Run `slidify doctor` before believing a rasterisation result, and use
`slidify convert --no-oracle` when the toolchain is incomplete. A comparison
between two conversions must have the oracle in the same state on both sides,
or it measures the oracle rather than the decks.

## Not real yet

The compatibility matrix lists these as planned. They parse as ordinary
attributes and do nothing, which is worse than an error because the deck still
builds.

- `data-pptx-transition` — slide transitions.
- `data-pptx-toggle` — emitting both states of a disclosure.
- `data-pptx-chart` — Chart.js and ECharts as native PPTX charts. A chart today
  is either SVG you author yourself, which converts natively, or a picture.
- `data-pptx-record` — a WebGL canvas captured to an animated GIF.
- `data-fragment` — reveal.js fragment stacks.

Run `slidify compat --level planned` for the current list rather than trusting
this one.
