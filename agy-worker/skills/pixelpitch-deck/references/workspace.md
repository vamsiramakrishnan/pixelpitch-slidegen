# The workspace and what you hand back

Every deck turn runs in one staged directory. It is the only place you write.
The outer process stages the inputs, then reads exactly one deliverable back
out. Nothing else you leave behind is collected.

## Inputs you may find

| File | Meaning |
|---|---|
| `brief.md` | Topic, audience, goal, slide count, any chosen expressions. |
| `brand-contract.json` | The derived brand: palette, fonts, `design_md`. Authoritative for every hex. |
| `brand-guideline.md` | The brand's authoring guideline, when the deck is on a Pixelpitch brand rather than a customer template. |
| `style.json` | The chosen visual style, when the user picked one. |
| `authoring-plan.json` + `baselines/` + `manifest.json` | The seam route. See the roundtrip skill's seam reference. |
| `template.pptx`, `spec-out/`, `clean/` | The roundtrip route's source material. |
| `catalog.json` + `catalog/` | Precomputed expressions for this template, to draw from. |

Which of these exist tells you which route you are on. Read
[routes.md](routes.md) if it is ambiguous.

## Deliverable A: authored slides

Write one standalone HTML document per slide:

```
slides/01-revenue-tripled.html
slides/02-...
slides/index.json
```

`index.json` is the running order and nothing more:

```json
{"slides": [
  {"file": "01-revenue-tripled.html",
   "title": "Revenue tripled in two quarters",
   "role": "hero|body|section|closing"}
]}
```

Write each slide file as soon as it is good. The outer process watches
`slides/` and shows the user a preview per file, so a slide that lands early
is a slide the user sees early. Rewriting a file is fine; the last write wins.

Exactly the number of slides the brief asks for. `index.json` decides the
order, so filenames are for you, not for the pipeline.

## Deliverable B: seam patches

On the seam route you write `patches.json` instead, and no HTML at all. The
contract is in the roundtrip skill's
`references/seams.md`. Validate it with `deck patch --check` before you
finish.

## Boundaries

Do not upload, publish, or call the renderer's `/render` endpoint to deliver
the deck — `deck render` is for *your* inspection loop. The outer process
converts and delivers what you leave in the workspace.

`deck shot` and `deck render` are how you look at your own work. Use them
freely; that is the point of them.
