# Anti-slop manifesto

Vendored verbatim from the `impeccable` skill (Absolute bans + The AI slop
test). Do not edit the rules below to resolve conflicts — add precedence here
instead, so the vendored text stays diffable against upstream.

## Precedence: capability bounds, taste selects

The slidify authoring grammar and this manifesto answer DIFFERENT questions and
will appear to contradict each other. They do not.

* The grammar is a CAPABILITY statement: what survives the HTML to PPTX
  conversion as native, editable shapes. It defines the feasible set. Its
  phrase "use these aggressively" means "these are available natively" — it is
  not a design recommendation.
* This manifesto is a TASTE statement: what to choose from within that set.

When they collide, resolve in this order:

1. If a construct is banned here, DO NOT USE IT — even where the grammar lists
   it under "renders natively". Specifically: gradient-filled text
   (`background-clip: text`), `repeating-linear-gradient` stripe backgrounds,
   and decorative multi-layer `box-shadow` are native-safe but are still slop.
   Native-safe is not permission.
2. If a construct is required for the deck to convert (viewport contract, one
   `data-pptx-role="title"`, no `background-image: url(...)`, no `filter` /
   `mix-blend-mode` / `text-shadow`), the grammar wins. Those are physics.
3. Otherwise pick the option that is native-safe AND not on the ban list.

Note on the kicker: the grammar's viewport budget mentions "head (kicker +
h1)". That is an allowance for vertical space, NOT an instruction to put a
small uppercase tracked eyebrow on every slide. An eyebrow on every slide is
the single most saturated tell in the list below. Budget the space, then
usually do something else with it.

### Absolute bans

Match-and-refuse. If you're about to write any of these, rewrite the element with different structure.

- **Side-stripe borders.** `border-left` or `border-right` greater than 1px as a colored accent on cards, list items, callouts, or alerts. Never intentional. Rewrite with full borders, background tints, leading numbers/icons, or nothing.
- **Gradient text.** `background-clip: text` combined with a gradient background. Decorative, never meaningful. Use a single solid color. Emphasis via weight or size.
- **Glassmorphism as default.** Blurs and glass cards used decoratively. Rare and purposeful, or nothing.
- **The hero-metric template.** Big number, small label, supporting stats, gradient accent. SaaS cliché.
- **Identical card grids.** Same-sized cards with icon + heading + text, repeated endlessly.
- **Tiny uppercase tracked eyebrow above every section.** The 2023-era kicker (small all-caps text with wide tracking, "ABOUT" "PROCESS" "PRICING" above each heading) is now the saturated AI scaffold; it appears on 55-95% of generations regardless of brief, which is the definition of a tell. One named kicker as a deliberate brand system is voice; an eyebrow on every section is AI grammar. Choose a different cadence.
- **Numbered section markers as default scaffolding (01 / 02 / 03).** Putting `01 · About / 02 · Process / 03 · Pricing` above every section is the eyebrow trope one tier deeper: reach for it because "landing pages do this" and you're scaffolding by reflex. Numbers earn their place when the section actually IS a sequence (a real 3-step process, an ordered flow, a typed timeline) and the order carries information the reader needs. One deliberate numbered sequence on one page is voice; numbered eyebrows on every section across the site is AI grammar.
- **Text that overflows its container.** Long heading words plus large clamp scales plus narrow grids cause headline overflow on tablet/mobile. Test the heading copy at every breakpoint; if it overflows, reduce the clamp max or rewrite the copy. The viewport is part of the design.

**Codex-specific defects** (your most-frequent giveaways; refuse-and-rewrite):

- **`border: 1px solid X` + `box-shadow: 0 Npx Mpx ...` with M ≥ 16px** on the same element. The "ghost-card" pattern: 1px border plus soft wide drop shadow on buttons and cards. Don't pair them. Pick one (a single solid border at the brand color, OR a defined shadow at no more than 8px blur), never both as decoration.
- **`border-radius: 32px+` on cards / sections / inputs.** You over-round. Cards top out at 12–16px; full-pill is fine for tags/buttons. Picking 24/28/32/40px on a card is the codex tell; no brand wants "insanely rounded".
- **Hand-drawn / sketchy SVG illustrations.** Class names like `loose-sketch`, `*-sketch`, `doodle`, `wavy`; `feTurbulence` / `feDisplacementMap` "paper grain" filters; 5-to-30 path crude scenes meant to depict a tangible subject (an otter, a table-and-fork, an album cover). All of these read as amateurish, not whimsical. If you can't render the scene with real assets, ship no illustration. Don't attempt sketchy SVG as a fallback.
- **`repeating-linear-gradient(...)` stripe backgrounds.** Diagonal stripes in `body:before` or section backgrounds are pure codex decoration. Don't.
- **Meta-criticism copy.** Naming a concept then layering an ironic modifier, or staging a strawman to "correct" it. Make the specific claim instead.

### The AI slop test

If someone could look at this interface and say "AI made that" without doubt, it's failed. Cross-register failures are the absolute bans above. Register-specific failures live in each reference.

**Category-reflex check.** Run at two altitudes; the second one catches what the first one misses.

- **First-order:** if someone could guess the theme + palette from the category alone, it's the first training-data reflex. Rework the scene sentence and color strategy until the answer isn't obvious from the domain.
- **Second-order:** if someone could guess the aesthetic family from category-plus-anti-references ("AI workflow tool that's not SaaS-cream → editorial-typographic", "fintech that's not navy-and-gold → terminal-native dark mode"), it's the trap one tier deeper. The first reflex was avoided; the second wasn't. Rework until both answers are not obvious.

## What is mechanised, and what is not

Local addition, below the vendored text. Twelve of the bans above are
detectors in `../scripts/slop.py`, each carrying a fixture that must trip it:
`side_stripe`, `gradient_text`, `glassmorphism`, `over_rounded`, `ghost_card`,
`stripe_background`, `sketchy_svg`, `eyebrow`, `numbered_scaffold`,
`card_grid`, `hero_metric`, `placeholder_text`. Run them with
`scripts/lint_slides.py`, and see them all fail at once in
`../examples/slop-anti-example.html`.

`placeholder_text` covers a wider family than PowerPoint's own filler. It also
refuses the authoring process showing through: a filename, a spec key, a
reference page, and the brief echoed back as copy. When a turn is asked for one
restrained candidate and one adventurous one, the words it was handed are the
first thing it reaches for as a headline, and the audience is shown the
instructions instead of the deck. Style names, option letters, and the reason a
candidate exists belong in the message to the reader. The slide carries the
deck's own title, section, date, author, and page number, and nothing else that
was not written for the audience.

One detector has no ban above it, because the tell is in the copy rather than
the CSS. `punchline_title` reads the title element and refuses the constructions
a speaker delivers rather than a title carries: the reversal ("It's not X. It's
Y."), the withhold ("Here's what nobody tells you"), the verdict ("X is dead",
"changes everything"), and the gerund flourish ("Unlocking", "Rethinking"). A
title orients, like a chapter heading. It says what the slide is about and
leaves the point for the person standing next to it. A deck whose titles all
land punchlines has taken the speaker's job and reads, correctly, as written by
a machine that has never presented anything.

Two more read the whole deck, because they cannot be seen one slide at a time.
`layout monotony` fires on three consecutive slides built the same way;
`single skeleton` fires when one skeleton owns more than sixty per cent of a
deck of four or more. They report under the key `"deck"`. The corresponding
positive is the contact sheet `deck shot` writes: twelve slides at once is the
only way to see a palette drifting or a rhythm flattening.

Nothing here catches the two failures that matter most. Overflow is geometry,
so it is measured in the rendered DOM by `deck shot`, not read out of the CSS.
And the category-reflex check above is a judgement no detector can make: a
slide can pass every rule on this page and still be the slide anyone would have
guessed from the brief.
