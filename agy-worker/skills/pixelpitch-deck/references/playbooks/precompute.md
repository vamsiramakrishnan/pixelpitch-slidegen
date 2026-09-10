# Playbook: precompute (template expression catalog)

For: making the end user's first seconds instant. Ahead of any brief, read
the template once and precompute a browsable catalog of visual expressions —
visual aids, slide narrative patterns, statement treatments — as PNG
previews ON the template's own plates. Online, the catalog is shown
immediately (pick a direction while the real deck generates); the chosen
entries also seed authoring and arena turns as proven exemplars.

## Steps (verbatim)

When `source-extraction.json` is present, the renderer has already resolved
the source OOXML styles and rendered every page. `clean/` comes from removing
slide text in OOXML, not painting over photographs. `baselines/` and
`slide-types.json` are compiled from the original text-box geometry. Do not
re-extract, repaint, rewrite, or replace these files. Derive the brand contract,
mark protected source pages, and create catalog examples inside the compiled
seams. Steps 1 and 8 are already supplied on this route. Unsupported source
layouts are listed with reasons in `slide-types.json`; do not invent substitutes.

1. Read the principles index. Confirm the staged inputs: `template.pptx`,
   `brand-contract.json`, `spec-out/` (spec, media, base), `clean/` plates.
   If missing, run the earlier steps of the new-deck playbook first
   (derivation, extraction, plates) — they are prerequisites, not options.
2. Build the LAYOUT CONTRACT and read its verdict before authoring anything:
   `deck layouts --spec spec-out --clean clean --out layouts.json
   --contract brand-contract.json`. It exits non-zero when plates lost the
   template's artwork. That is the run stopping, not a warning: fix the
   plates and rebuild. `--allow-dead-plates` exists for inspecting the
   damage, never for proceeding past it.
3. Enumerate the EXPRESSION MATRIX from the contract (not from imagination):
   - layout worlds from `layout_worlds` (e.g. blue statement, white content)
   - aid archetypes per `visual_aids_style` (ledger timeline, growth ledger,
     hero stat, proportion bar, era statement, comparison, quote/impact —
     whichever the template's own vocabulary supports)
   - narrative treatments (assertion-led, data-led)
   Target ~3 entries per archetype across worlds (18-24 slides). Every entry
   is a real slide: clean plate + spec-exact overlay + the aid in template
   furniture vocabulary + a representative assertion title (invented content
   is fine and labeled as sample).
4. Author all entries as `catalog/src/<id>.html`, convert each to a single
   slide (slidify, --no-oracle --no-tier3), rasterise to `catalog/<id>.png`.
   Parallelise with subagents; one entry per subagent. Every entry takes its
   geometry and its ground colour from that layout's `zones`, and names each
   piece of `furniture` by its `asset` path. Do not retype a value the
   contract already holds.
5. PASS 2 — gate every entry: lint (palette/contrast), slidify report, and
   personal visual inspection vs its plate (principle 5). Fix failures;
   re-render. Entries still failing after two attempts are dropped, not kept.
6. PASS 3 — polish keepers: title assertion strength, one accent, whitespace.
7. Emit `catalog.json`: per entry — id, world, archetype, narrative
   treatment, png path, specimen used, palette tokens, one-line description
   written FOR THE END USER ("Growth story as the template's ledger rows").
   Upload the catalog directory to GCS under the template key for reuse.
8. Emit the PREPARED BUNDLE (below). Without it the template can never be
   used for a real deck, so it is not an optional extra. With compiled source
   baselines, preserve the supplied bundle and produce only the catalog,
   brand contract, and inspected previews.
9. Verify the bundle with `deck check --workspace .` and clear every finding
   it reports under `bundle`. A baseline no slide type registers cannot be
   patched, so authoring silently falls back to freehand.
10. Report: matrix considered, entries kept/dropped per pass, the layout
    contract's archetypes and dead-plate count, and the GCS location. End
    with CATALOG_DONE or CATALOG_FAILED: <reason>.

## The layout contract

`spec-out/` holds the geometry and `clean/` holds the plates, and until this
step nothing relates them. What reaches an authoring turn is a palette and
some prose, so it reinvents the numbers. One template through this pipeline
declared its body title at y=56.0 and its body copy at y=199.3; the deck built
from it used 36 and 172, on slides whose plates came from that very layout.

`layouts.json` is the join. Per template slide it records the archetype, the
plate and its SHA-256, whether the plate is still alive, the `zones` a run may
place text in with the ground colour measured off the plate under each, and
the `furniture` the template repeats with the media file behind it. It is the
answer to every geometry question authoring would otherwise guess at, and
`deck check` refuses any slide whose plate the contract does not pin.

Three things it stops, all of which shipped:

- A plate that died in painting. The painter may touch text rects and nothing
  else, so everything else must survive byte for byte. Twenty of twenty-eight
  plates came out a single flat colour on one run, logo and motif gone, and
  every gate passed them because each still had an `<img>`.
- A plate somebody typed. `navy-statement.png`, `blue-statement.png` and
  `white-content.png` were flat rectangles an authoring turn invented mid-run
  and wrote into `clean/` beside the real ones. Pinning by hash is what tells
  the template's artwork from a rectangle with a plausible name.
- Furniture rendered as a sentence. A lockup is a real PICTURE bound to a real
  media file. Twelve slides of one deck typed its wording out as text instead,
  because nothing ever pointed at the asset.

Furniture is measured inside its own rect rather than against the whole
canvas. A lockup is 2.3% of a slide, so losing it cannot move a global number
and leaves the plate exactly as unusable.

## The prepared bundle

The catalog is what the user browses. The prepared bundle is what the next
deck is actually built from: a set of immutable, hash-pinned baselines whose
only mutable regions are declared seams. Authoring against it is why template
fidelity survives a creative turn.

Write, in the workspace root:

- `baselines/<slide-type>.html` — a complete 1280x720 document reconstructed
  from a real template layout, obeying every roundtrip rule. Mark each mutable
  region with paired `<!-- pp:seam:<id>:start -->` and
  `<!-- pp:seam:<id>:end -->` markers, and nothing else mutable.
- `specimens/<slide-type>.png` — that baseline rendered.
- `slide-types.json` — `{"slide_types": [{"id", "archetype", "baseline_path",
  "preview_path", "seams": [{"id", "kind": "content|visual_aid",
  "allowed_tags": [...], "allowed_style_properties": [...]}]}]}`.

Draw the allowlists tightly. Every tag and property you allow is one a later
turn can use to drift off-template; every one you omit is a fidelity
guarantee. Seams that wrap the whole slide body are the failure case — they
re-admit freehand authoring under a different name.

The `pixelpitch-template-roundtrip` skill's `references/seams.md` is the
consuming side of this contract. Read it before you design the seams: what a
patch may say is exactly what your allowlists decide.

## Rules

- The catalog teaches the template's real vocabulary — every entry follows
  all roundtrip rules (plate, spec-exact overlay, furniture vocabulary).
- Sample content is clearly sample (dates/figures plausible, marked "sample"
  in the manifest); it demonstrates form, not facts.
- The catalog is additive: re-running adds entries; it never mutates
  brand-contract or spec-out.
- Nothing writes into `clean/`. It holds the template's own artwork and every
  plate in it is hash-pinned; a file added there is an invented ground by
  definition. Catalog output goes to `catalog/`, baselines to `baselines/`.
