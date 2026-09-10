# Presentation craft

The positive canon. The anti-slop manifesto is a *ban list* — what to refuse.
This is the *taste target* — what to actually reach for. A slide that trips no
ban can still be forgettable; nothing in the ban list makes a slide land. This
document is how a generated deck becomes a designed one.

## Precedence (read this first)

Three documents govern a slide, in this order:

1. **Grammar** (`slide_author_grammar.md`) — physics. What survives the HTML→PPTX
   conversion as native, editable shapes. If a construct is required to convert
   (viewport contract, one `data-pptx-role="title"`, no `filter`/`text-shadow`
   on body slides), the grammar wins. Non-negotiable.
2. **Anti-slop manifesto** (`anti_slop_manifesto.md`) — bans. If a construct is
   banned there, do not use it even where the grammar permits it. Native-safe is
   not permission.
3. **This craft canon** — selection. Within what the grammar allows and the
   manifesto permits, this is how to choose. It never overrides a ban or a
   physics rule; it decides everything else.

When this canon and the manifesto seem to disagree, they are answering different
questions (what to avoid vs. what to build) and the manifesto's ban stands.

---

## Direction — read the brief before you compose

The fastest way to look generated is to skip the thinking: reach for a default
look, put the same layout on every slide, and let copy tells slip through. Do
this first, before authoring any slide.

- **Declare the design read, in one line.** *"Reading this as a <deck kind> for
  <audience>, <tone> tone, leaning toward <brand/style>."* Constraints
  (accessibility, regulated content) override aesthetics. Infer it from the
  brief; only ask if genuinely ambiguous.
- **Set three dials (1–10), reasoned from the read — not silently default:**
  - **VARIANCE** — 1 = one repeated template, 10 = every slide a distinct
    composition. Governs how much the layout varies slide to slide.
  - **DENSITY** — 1 = one number on a field, 10 = a packed grid or matrix.
    Governs how many elements a slide carries.
  - **EMPHASIS** — 1 = quiet monochrome, 10 = large accent and dramatic scale
    jumps. Governs accent use and type-scale contrast.
  Baseline **7 / 4 / 6** (varied, uncluttered, confidently emphasized). Investor
  pitch/launch leans high EMPHASIS; technical or data-heavy leans high DENSITY;
  editorial/calm leans low on both.
- **Variance is composition, not system.** High VARIANCE means each slide is a
  distinct composition — never a second typeface or a second accent. The grid,
  type scale, and the single accent stay locked deck-wide; only the layout
  changes. Don't run the same layout three slides deep, and don't let one layout
  own more than ~60% of the deck — monotony is the "templated" tell.
- **One accent, locked across the whole deck.** Never add a second accent "for
  variety"; that is the VARIANCE dial's job, done with layout. The accent always
  marks the point, never mood.
- **Watch the copy tells.** No em/en-dash in a title (`—` or `–` in a heading is
  the single strongest AI-copy signal — recast the line). Use the `…` glyph,
  never `...`.

---

## 1 · The deck is an argument, not a gallery

A deck exists to move an audience from where they are to a decision or belief.
It is a structured argument that happens to be visual. Design serves the
argument; it is never the point.

- **One governing message (the spine).** State in one sentence what the whole
  deck proves. If you cannot, the deck has no spine and no amount of styling
  fixes it. Every slide is a load-bearing step toward the spine or it is cut.
- **The pyramid principle.** Lead with the answer, then support it. Structure
  descends from the single governing message to a few grouped supporting claims
  to the evidence under each. The audience should never wait for the point.
- **Every slide earns its place.** Before authoring a slide, answer: *what does
  the audience know or believe after this slide that they didn't before?* If the
  answer is "the same thing the last slide said, restyled," merge or cut it.
- **Cut to the load-bearing.** A tighter deck is a better deck. Fewer, stronger
  slides beat many thin ones. Detail that supports but doesn't advance the
  argument goes to speaker notes, not onto the slide.

## 2 · Assertion titles — the single highest-leverage move

The title is the most-read text on any slide. Waste it on a topic label and the
slide starts dead.

- **Title = the takeaway, as a sentence.** Write the conclusion, not the
  subject. `Churn fell 40% after onboarding shipped`, never `Churn`. `We should
  enter Europe next, not APAC`, never `Market expansion`. The title carries the
  point; the body proves it.
- **The headline test.** A reader who reads *only the titles*, top to bottom,
  should get the entire argument — spine, supporting claims, and conclusion — and
  nothing else. Read your titles in sequence: do they tell the story alone? If
  they read as a table of contents (`Overview` · `The Problem` · `Our Solution`
  · `Pricing`), you have topic labels, not assertions. Rewrite every one.
- **One title, one claim.** No "and". A title with two ideas is two slides.
- **Titles are specific and falsifiable.** `Revenue grew` is weak; `Revenue grew
  3× while headcount held flat` is a claim someone could argue with — which means
  it says something.

## 3 · One message per slide, and the "so what"

- **One idea per slide.** A slide makes exactly one point. If you are tempted to
  add a second, that is the next slide.
- **Answer "so what?" out loud.** For every slide, the audience silently asks
  "so what?" Answer it *on the slide*, usually as the title or a single emphatic
  line — not left implied under a chart. A chart with no stated implication is
  homework you handed the audience.
- **Concrete beats abstract.** Prefer the specific number, the named example, the
  real quote over the category noun. `41 support tickets a week` lands; `improved
  operational efficiency` evaporates.
- **Numbers need a referent.** A number alone is trivia. `$2.4M` means nothing;
  `$2.4M — 3× last year, and more than our top two competitors combined` is an
  argument. Always anchor a figure to a comparison, a baseline, or a stake.

## 4 · Visual aids must carry the argument

A visual aid is not decoration. It is the fastest path to the point for that
particular claim. If a visual does not make the point faster or more convincingly
than words, it is chart-junk — remove it and use type and space instead.

- **Pick the chart from the claim, not the data:**
  | The claim is about… | Use | Avoid |
  |---|---|---|
  | Comparing values across categories | Horizontal/vertical **bar** | pie, radar |
  | Change over time / a trend | **Line** (or area if one series) | many-series spaghetti |
  | Part-to-whole, few parts | **Stacked bar**, or a single big % | pie beyond 2–3 slices |
  | Relationship / correlation | **Scatter** | dual-axis charts |
  | Looking up exact values | **Table** (right-align numbers) | a chart |
  | Structure, flow, sequence | **Diagram** (boxes + arrows) | a bullet list of steps |
- **Annotate the insight, on the chart.** The single point the chart proves gets
  a direct label, an arrow, or one highlighted element — not a legend the eye has
  to decode. If the takeaway isn't marked *in* the chart, the chart isn't done.
- **One accent = the point.** Draw everything else in a muted/neutral tone and
  reserve the brand accent for the one bar, line, cell, or number that is the
  argument. Color is meaning, not mood. If everything is emphasized, nothing is.
- **Maximize data-ink (Tufte).** Delete gridlines, boxes, drop shadows, 3D,
  redundant legends, and axis clutter. Every mark that isn't data or a label the
  reader needs is noise competing with the point.
- **A table is for values you must read; a chart is for a shape you must see.**
  Never render a table when the claim is a trend, and never render a chart when
  the audience needs the exact figures.
- **Give every chart the editorial grammar.** A title that states the takeaway
  with exactly one accent-colored keyword inside it; an italic caption naming
  the exhibit and its units or population ("Consumer GenAI use, USA — share of
  adults"); and, when the data is sourced, one small italic source line. Keep it
  to ≤4 series and ≤6 categories, and let the axis top land on a clean round
  number rather than a raw maximum.
- **When no visual earns its place, don't force one.** A single strong sentence
  set large in generous space is a legitimate — often superior — "visual." Silence
  is a composition. Do not manufacture an icon grid to fill the frame.

## 5 · Genuine design craft (positive moves, to complement the ban list)

The manifesto tells you what not to do. These are the moves that make a slide
look considered. Reach for them deliberately.

- **One focal point.** Decide what the eye hits first, make it unambiguously the
  largest/boldest/most-saturated thing, and demote everything else. A slide with
  two co-equal focal points has none.
- **Contrast is hierarchy.** Rank matters by *dramatic* difference in size and
  weight, not timid steps. Title 3–4× the body, not 1.3×. A hierarchy the eye
  can't feel isn't one. Vary weight and size before you add a third color.
- **Grid and alignment.** Everything aligns to a shared structure — a left
  margin, a baseline, a column edge. Optical alignment beats mathematical when
  they differ. Ragged, arbitrary placement is the loudest amateur tell there is.
- **Whitespace is structure, not leftover.** Space groups related things and
  separates unrelated ones (proximity = relationship). Generous margins signal
  confidence; a crammed slide signals the author couldn't decide what to cut. If
  in doubt, remove an element before shrinking the margins.
- **Restraint.** One or two typefaces, one accent color, a small set of sizes,
  consistent spacing. A coherent system applied calmly reads as design; novelty
  per slide reads as noise. The brand's real palette and type personality, never
  generic defaults.
- **Typographic micro-rules.** Tabular (monospaced) figures for data and any
  aligned numbers, so columns and axes don't shimmer. Reserve underline for real
  links — emphasize with weight or accent, never underline inert text. Bold for
  emphasis, italic for citations and captions. The `…` glyph, never three
  periods. Loosen tracking on the rare uppercase label and keep it short.
- **Consistency across the deck.** The same grid, type scale, accent, and spacing
  on every body slide. Rhythm is what makes a set of slides feel like one deck.
  Break the system only where the narrative genuinely turns (a section divider, a
  climactic number) — and then break it hard, on purpose.

## 6 · Communication tactics (moving a real audience)

- **Calibrate to the audience.** An engineer, a CFO, and a board want different
  evidence for the same claim. State the claim the way *this* audience weighs
  proof; lead with what they care about.
- **Setup → tension → resolution.** The strongest sequences establish a stake,
  introduce a gap or conflict, then resolve it. `Here's the goal → here's what's
  blocking it → here's the move`. Flat "topic, topic, topic" ordering has no pull.
- **Signpost at the turns.** Section dividers should say where the argument now
  goes and why — an assertion, not a one-word label. Earn each divider; don't
  scaffold sections by reflex (see the manifesto's ban on numbered furniture).
- **One memorable thing per slide.** A number, an image, a phrase the audience
  could repeat afterward. A slide with nothing quotable is a slide they forget.
- **Say the specific claim.** No meta-commentary, no naming-a-concept-then-
  ironizing-it, no strawman-to-correct. Make the actual assertion and support it.

## 7 · The brilliance bar

Before a slide is done, it must pass all of these. Any "no" is a revision.

- **Point of view.** Does the slide take a position, or merely present
  information neutrally? Brilliant decks argue; mediocre decks report.
- **The title test.** Read only this title in the run of titles — does the
  argument still hold and advance? Is it an assertion, not a label?
- **The so-what test.** Is the implication stated, not left as homework?
- **The visual test.** Does every visual carry the argument faster than words
  would, with the insight annotated and one clear accent? If not, is the
  type-and-space alternative stronger?
- **The focal test.** Is there exactly one thing the eye hits first, and is it
  the point?
- **The studio test.** Would a partner at a top design studio put their name on
  this slide? If a viewer could say "AI made that" without hesitation, it fails —
  rework the structure, not just the styling.
