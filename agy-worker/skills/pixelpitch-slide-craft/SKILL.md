---
name: pixelpitch-slide-craft
description: Use before writing or revising any slide HTML, CSS, title, or visual aid - when choosing a layout, picking a chart or diagram for a claim, writing deck copy or assertion titles, deciding whether a decorative treatment is allowed, or diagnosing why a slide looks generic, AI-generated, cramped, or unconvincing. Also use when slidify conversion drops text, rasterises a slide that should stay editable, or reports unreadable point sizes.
---

# Pixelpitch slide craft

## Start here, before any reference

You have a claim and no slide. Ask the gallery which form carries it:

```bash
python3 scripts/pick_exhibit.py --claim "38 of 214 circuits are over their rating"
```

One form comes back, with the rendered exemplar to open, the HTML to start
from, the claims it is wrong for, and the specific mistake it invites. That is
usually the whole answer, and it costs a few lines instead of six hundred.
Sixteen forms cover heroes, statements, charts, diagrams, matrices, ledgers,
comparisons and tables, in a quiet register and a loud one. Run it with no
arguments to see them all.

Read a reference below only when the picker did not settle the question.

## Precedence

1. **Grammar** decides what converts. A slide that violates it is not a
   design choice, it is a broken artifact.
2. **Anti-slop** decides what is refused. It overrides the grammar wherever
   they disagree about decoration: the grammar says what is possible, this
   says what is banned.
3. **Craft** selects within what remains. It never overrides a ban or a
   conversion rule. This is where quality comes from.

| Question | Reference |
|---|---|
| Will slidify convert this natively? What primitives are safe? | [references/slidify-grammar.md](references/slidify-grammar.md) |
| Is this treatment banned? Why does this look AI-made? | [references/anti-slop.md](references/anti-slop.md) |
| How do I make this land? Argument, assertion titles, focal point. | [references/craft.md](references/craft.md) |
| Layout, grid, rhythm, type scale across the deck. | [references/composition.md](references/composition.md) |
| Copy, titles, the so-what, the headline test. | [references/communication.md](references/communication.md) |
| Which chart or diagram for this claim, and how to annotate it. | [references/visual-aids.md](references/visual-aids.md) |

## Start from these, not from a blank file

| Need | Open |
|---|---|
| The form that carries your claim, with its exemplar and its trap. | `scripts/pick_exhibit.py --claim "..."` |
| The 1280x720 shell and the type scale, ready to paste into `<style>`. | [assets/slide-base.css](assets/slide-base.css) |
| How the gallery is organised, and how to prove the lint fires. | [examples/README.md](examples/README.md) |

## The output contract

Every authored slide is one standalone HTML document.

- Exactly 1280x720, pinned on `html`, `body`, and `.slide`.
- Exactly one element carries `data-pptx-role="title"`.
- All CSS in one inline `<style>`. No `<link>`, no JavaScript.
- Inline `<svg>` is encouraged. `background-image: url(...)` never is.
- On body slides, never `filter`, `mix-blend-mode`, `backdrop-filter`,
  `mask-image`, `text-shadow`, or `clip-path: path()/url()`.
- Content height budget is about 510px. When display type will not fit its
  row, shrink the type, not the row.

## Readability floors

Converted at 96 DPI, 1px becomes 0.75pt. Below these floors text is
unreadable in PowerPoint regardless of how it looks in the browser.

| Element | Minimum |
|---|---|
| Titles | 40px / 30pt |
| Body copy | 24px / 18pt |
| Labels, captions, footers | 18px / 13.5pt |

Overflow is never solved by going under a floor. Shorten the copy or simplify
the layout.

## Slide roles

A deck is hybrid. `hero`, `section`, and `closing` slides may be image-led and
atmospheric — full-bleed inline `<svg>`, multi-stop gradients, gradient-filled
text, dramatic type. They carry the deck's visual weight, and slidify
rasterises only those units.

`body` slides stay strictly native and information-first, because their text
must remain editable in PowerPoint. Use native-safe primitives only.

Never let a body slide become a raster. Never let a hero slide read as a
bullet list.

## Gate

`scripts/lint_slides.py <slides> [brand-contract.json]` is the mechanical half.
It prints `{}` and exits 0 when clean, or a per-slide defect map and exits 1.
`pixelpitch-deck`'s `deck lint` wraps it with the rest of the gate set.

`<slides>` is whatever you already have. `scripts/slides_io.py` is the one
reader for all four shapes, so nothing here needs converting first.

| On disk | Accepted |
|---|---|
| A directory of `*.html` | yes, sorted by filename |
| The workspace's `slides/index.json` of `{"file": ...}` entries | yes, paths resolve against the index |
| `{"slides": [{"html": ...}]}` | yes |
| `[{"html": ...}]` | yes |

Passing the lint is not passing the craft. The lint reads source. It catches
known tells, it cannot tell you the slide makes no argument, and it cannot see
geometry. Text that clips or collides is caught one layer up, by
`pixelpitch-deck`'s `deck shot`, which measures the rendered DOM.
