# Seam-patched decks

Some templates are admitted as a **prepared bundle**: the outer process has
already extracted, validated, and hash-pinned a set of immutable HTML
baselines. On that route you never author a whole document. You return typed
replacements for the seams a baseline declares, and the outer process
validates every patch against the pinned digest before applying it.

This exists so template fidelity is a property of the system rather than a
property of your care. Geometry, typography, clean-plate artwork, and
protected furniture outside the seams cannot drift, because you never touch
them.

## Recognising the route

The workspace holds `authoring-plan.json`, `manifest.json`, and `baselines/`.
`authoring-plan.json` lists the admitted slide types, each with an `id`, a
`baseline_path`, and its declared `pp:seam` regions with their allowed tags
and styles.

Compiled types carry `source_slide`. Read that page's resolved
`spec-out/spec/slide-NN.json` to identify each `shape-<id>` seam. The BODY seam
accepts original charts, tables, diagrams, and narrative layouts inside the
source content box; title, subtitle, and footnote seams retain the original
type and position. `size_px` already accounts for the original PowerPoint
canvas size. Do not convert `size_pt` with a blanket 4/3 multiplier.

Run `deck patch --list` first. It prints the same types plus each one's
`baseline_sha256`, computed from the baseline on disk, which is the digest
your patches must carry.

## What you produce

One file, `patches.json`, in the workspace root:

```json
{"slides": [
  {"slide_index": 0,
   "slide_type_id": "<id from authoring-plan.json>",
   "baseline_sha256": "<that type's digest, copied verbatim>",
   "title": "<the assertion title>",
   "role": "hero|body|section|closing",
   "replacements": [
     {"seam_id": "<declared seam id>", "html_fragment": "<allowed fragment>"}
   ]}
]}
```

Rules the validator enforces, so getting them wrong costs a whole turn:

- exactly the requested number of slides, with contiguous `slide_index`
  values starting at 0 and no duplicates;
- exactly one replacement for every seam the chosen type declares, no more
  and no fewer;
- `baseline_sha256` matching the plan, which is how a stale plan is caught;
- every fragment inside its seam's tag and style allowlist.

`deck patch --check` runs that validator locally against the same code the
outer process uses. Run it before you finish. A rejection you find yourself
costs seconds; one the outer process finds costs the whole turn.

## Boundaries

Do not edit a baseline document. Do not render, upload, call the renderer, or
write a PPTX on this route — the outer process converts the admitted HTML
after validation.

Content and visual aids may be fully original *inside* the seams, in the
template's own furniture vocabulary. The seam is a fidelity boundary, not a
creativity budget.

Finish with exactly one line: `PATCHES_COMPLETE` or `PATCHES_FAILED: <reason>`.
