---
name: pixelpitch-deck
description: Use when the request concerns a slide deck, presentation, PPTX, pitch, or keynote - building one from a brief, matching a customer PowerPoint template, fixing slides that render wrong or off-template, making an existing deck better or more brilliant, reviewing a deck before it ships, or precomputing a template's visual catalog. Also use when the workspace contains brief.md, authoring-plan.json, spec-out/, or slides/.
---

# Pixelpitch deck

Sticky mode. Once a deck task arrives, stay in it for the whole turn.

Deck work is a design task with a mechanical substrate. The substrate is
scripted; the judgement is yours. Never reimplement in prose what
`scripts/deck` already does, and never let `scripts/deck` conclude what only
looking can conclude.

## The gate law

**No slide is done until you have opened its rendered PNG and looked at it.**

`deck lint`, `deck check`, and the slidify report are necessary and never
sufficient. They read HTML and geometry. They cannot see a double-exposed
title, an aid the template's own designer would never have drawn, or a slide
floating with no plate under it.

Calling a slide clean without looking is a protocol failure, not a shortcut.

| Rationalization | Reality |
|---|---|
| "Lint returned `{}`, so it's clean" | Lint reads source. The defect is in the conversion. |
| "The report shows zero overflow" | Overflow is one defect class. Residue and freehand are not measurable. |
| "I authored it, I know what it looks like" | You have seen your HTML. You have not seen the PPTX. |
| "Inspecting every slide is too slow" | `deck shot` renders the whole deck in one call, no PPTX. |
| "It converted without an error" | Conversion succeeded. That is a different claim. |
| "The gates are green, that IS the evidence" | Green gates plus an unseen render is an unverified deck. |
| "`layout.json` is empty, nothing collides" | Nothing collides. The exhibit can still be the wrong one, drawn well. |

Stop and go look when you are about to write `DECK_COMPLETE`, about to call a
slide clean or verified, or about to start the next slide.

`deck shot` runs local Chromium, so the law is enforceable with no service up.
It writes one PNG per slide, a contact sheet, and `layout.json`, and it exits 1
on any of these, measured in the rendered DOM rather than read out of the HTML.

| Defect | What it means |
|---|---|
| `clipped Npx outside <element>` | Text ran past whatever actually clips it, the slide or its own SVG viewBox. |
| `Npx text is below the 18px floor` | Unreadable once converted at 96 DPI. |
| `text overlaps text by WxHpx` | Two runs share pixels. Grid and flex do not clip an overlong label, they stack it. |
| `N% of <run> is painted over by <element>` | A run is behind an opaque panel. Nothing clipped, nothing collided, and the text is not on the slide. |
| `N% of <run> is under the F:1 contrast floor against what is behind it` | The run is printed on artwork, and measured against the pixels it actually landed on it does not read. Move it or recolour it. |
| `<run> runs N% off its clear ground onto artwork it cannot be read against` | The line is too long. Most of it reads; the tail crossed onto a photograph or a plate shape. Shorten the line rather than moving the block. |
| `image failed to load: <element> src="..."` | The path is wrong, or the file is not there. On a template deck this is the plate, so the slide has no artwork under it at all. |
| `document scrolls Npx past 720` | The slide is taller than the slide. |

The two contrast findings are measured against a second screenshot taken with
every glyph hidden, so the ground is photographed rather than estimated. They
fire only where the run sits on artwork, meaning a plate PNG, a photograph, or
a canvas. Where the ground is a declared CSS colour both colours are in the
DOM, `deck lint` already judges them at source, and a second stricter opinion
here would only teach you to skim the output.

Those are geometry, not taste. Fix them before judging the design, and never by
going under a readability floor. A green `layout.json` means nothing collides;
it says nothing about whether the deck is any good. The contact sheet is for
the defects one PNG cannot show, a palette that drifts halfway through or three
slides running on the same skeleton.

If `deck shot` cannot run, the deck is not done, it is unverifiable. A machine
without a browser it can launch, or without the shared libraries Chromium
needs, cannot satisfy the gate law, and no amount of clean lint substitutes.
End that turn with `DECK_FAILED: renderer unavailable, <the error>` and hand
back the slides you wrote. This is the one place the law is most likely to be
quietly dropped, because everything else went well and the deck looks finished
from inside the HTML. It is not. A caller who is told the renders are missing
can run them; a caller who is told `DECK_COMPLETE` cannot know to.

Looking has one standing false positive, and it runs in both directions. A
slide that argues sits in the top two thirds with the bottom third open. A
slide that declares — cover, section marker, statement, closing — sits on the
floor with the top two thirds open. Both are composed correctly. The pull to
close the gap by centring is the web-design reflex, and on a projected slide it
is wrong either way: the open end of the frame is structural, and the audience
reads a centred block as floating. What you are checking is whether the slide
committed to an end. Change it only when the whitespace is uneven or
accidental, never because it is there.

## Routes

Read the principles index first, every turn:
[references/principles.md](references/principles.md). Then match the ask to
one playbook and copy its steps in verbatim as todos. If none fits, compose
one inline and say so.

| The ask | Playbook |
|---|---|
| New deck from a customer template | [new-deck](references/playbooks/new-deck.md) |
| Specific slides are wrong | [repair](references/playbooks/repair.md) |
| "Make it brilliant" / load-bearing slides are first-draft average | [arena](references/playbooks/arena.md) |
| Final lift, no structural change | [polish](references/playbooks/polish.md) |
| Try to fail the deck before the audience does | [interrogate](references/playbooks/interrogate.md) |
| Build a template's expression catalog ahead of any brief | [precompute](references/playbooks/precompute.md) |

Choosing between a customer template, a fresh design-system deck, and a
repair: [references/routes.md](references/routes.md). Delivery and structural
gates: [references/quality-gates.md](references/quality-gates.md). The
workspace you write into and the one deliverable read back out:
[references/workspace.md](references/workspace.md), shown end to end for a
real turn in [examples/finished-turn.md](examples/finished-turn.md).

## Library skills

Route to these as steps fire. Read their SKILL.md when you get there, not
before.

- `pixelpitch-slide-craft` — the slidify grammar, the anti-slop bans, the craft
  canon, and a gallery of sixteen gated slides. Consult before authoring any
  slide HTML, and start with `deck pick --claim "..."` rather than with the
  references, which are six hundred lines you mostly do not need.
- `pixelpitch-template-roundtrip` — clean plates, spec-exact overlays, and the
  seam-patch contract for building on a customer template.
- `pixelpitch-brand-derivation` — deriving `brand-contract.json` from a
  template that has none.

Any other skill in the catalog may contribute. Explicit user choices outrank
every playbook step.

## Quick reference

`scripts/deck <command>` — run `scripts/deck --help` or
`scripts/deck <command> --help` for flags.

| Command | Does |
|---|---|
| `deck pick` | Which slide form carries this claim, with a rendered exemplar and its trap. Run it before authoring. |
| `deck spec` | Extract a PPTX into per-slide geometry JSON, media, and base renders. |
| `deck plates` | Paint text regions out of base renders to produce artwork-only clean plates. |
| `deck layouts` | Join the template's geometry to its plates, pin every plate by hash, and refuse plates that lost the template's artwork. |
| `deck lint` | Anti-slop, palette, and contrast gates over the slides. Reads source. Exits 1 with JSON defects. |
| `deck shot` | Render every slide to PNG, probe the rendered DOM, write a contact sheet. The iteration loop. |
| `deck stage` | Fold the slides into one `deck.html` that presents and converts. The half of the deliverable a human stands in front of. |
| `deck render` | Build the real PPTX plus `render.json` and preview PNGs. |
| `deck diff` | Perceptual delta between a converted render and its base or plate. |
| `deck patch` | Apply and validate seam replacements against admitted baselines. |
| `deck check` | Run every mechanical gate and print one verdict. |

`deck check` printing `mechanical: pass` means the substrate is sound. It
prints the visual inspection you still owe. That line is not decoration.

### The deck is not the slides

`deck shot` renders the slides. `deck render` converts them. Those are two
artifacts and they fail in different places, so a gate on the first says
nothing about the second. Two ways the second one goes wrong on its own, both
of which have shipped past a fully green first one:

**The assets do not travel.** A slide crosses to the renderer as a string, so
every relative `src` in it resolves against nothing. Local Chromium reads the
plate off disk and the shot is perfect; the converter sees a broken image, and
what arrives is the text on bare background with the template gone. `deck
render` now inlines every local asset as a data URI and refuses to send a slide
that names a file it cannot read, so this fails loudly at the boundary instead
of quietly in the PPTX.

**Everything rasterises.** Check `native_area_ratio` in `render.json` on every
render. At `1.0` the deck is editable PowerPoint. At `0.0` it is twenty
pictures, the conversion exited clean, and no gate that reads HTML can tell.
The usual cause is not the slides: slidify verifies its native output against a
LibreOffice render, and where LibreOffice is missing every slide scores zero
and it rasters rather than emit something it could not check. Read the
renderer's slidify invocation before you touch a line of HTML.

The general rule under both. When a check cannot run, decide what its absence
means before you let it degrade. Skipping an oracle costs you the verification;
running one that always fails costs you the deck.

## Finishing

End the turn with exactly one line: `DECK_COMPLETE` or `DECK_FAILED: <reason>`.

A `DECK_FAILED` carrying evidence is a better outcome than a `DECK_COMPLETE`
carrying residue. When a defect class shows up in inspection, add it to the
relevant playbook's hunt list or to `deck lint` in the same turn.
