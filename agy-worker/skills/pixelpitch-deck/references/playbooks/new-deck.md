# Playbook: new-deck

For: a new deck from a customer template, brief in hand.

## Steps (verbatim)

1. Read the principles index, `brief.md`, and the workspace state.
2. Write the room down in one line before planning anything: who is in it, how
   long the speaker has, and whether the deck is presented live or read alone.
   The brief usually will not say, so derive it and state the derivation as an
   assumption in the reply. It is not ceremony. A twenty-minute board review is
   twelve slides that a speaker talks over; the same content sent as a
   leave-behind is six slides that have to argue without them. Every later
   decision, slide count, words per slide, whether the argument lives on the
   slide or in `data-pptx-notes`, follows from that line, and a deck built
   without it defaults to the density of whatever the model saw last.
3. If no `brand-contract.json` exists: run the `pixelpitch-brand-derivation`
   skill (stage, theme tokens, specimen inspection, contract) and verify its
   required keys.
4. Settle the register before the content, on rendered evidence. The contract
   fixes the palette and the type; it does not fix how the deck carries
   itself, and that decision is cheapest now and most expensive after twelve
   slides exist. Author three title slides on the same brand contract and the
   same real headline from the brief, differing in composition and register,
   not in paint: `style/a.html`, `b.html`, `c.html`. Render them, look, and
   pick one. Write the pick and the two rejections into the reply by id, so
   the reader can answer "go with b" in two characters and the file is still
   there next turn.
   - Make one restrained and one adventurous. A third that differs only in
     accent colour is one candidate rendered three times.
   - Every candidate is a real first slide of this deck. Never render the
     reason a candidate exists onto it: no option letters, no style names, no
     "safe" or "bold", no line from the brief that was an instruction rather
     than content. Chrome is the deck's own title, section, date, author, and
     page number. `placeholder_text` in the slide lint catches the common
     phrasings, and it is a floor, not the standard.
5. If no `spec-out/` exists: run `extract_slide_spec.py` on the template
   (spec JSON, media, base renders).
6. Produce clean plates for candidate specimens per the source-roundtrip
   protocol.
7. Plan the deck: spine, assertion titles (headline test), per-slide aid
   choice from the brief's data. Choose specimens per slide. Run
   `deck pick --claim "<what this slide says>"` once per slide and write the
   chosen form into the plan, so the deck's variety is decided before any HTML
   exists rather than discovered on the contact sheet.
8. Author slides per the source-roundtrip route (plate + spec-exact
   HTML/CSS overlay + aid in template vocabulary), convert, gate, and
   iterate visually until every render you inspect is clean.

   For the aid itself, check your subagent list for an `aid-slide-NN` whose
   description names the layout you are on. There is one per distinct free
   region the template leaves, built from `layouts.json` at launch, and it
   already knows the box, the ground colour behind it, and the furniture rects
   it must stay clear of. Send it the claim; it returns an inline `<svg>` you
   place. Drawing the aid yourself means deciding that geometry by eye, which
   is how artwork ends up over the lockup.
9. Run `deck stage --slides slides --out deck.html`, then convert that
   same file and confirm it yields the slide count and picture count the
   directory did. Deliver: deck.pptx, deck.html, slides/, preview/,
   report.json with per-slide records (specimen, plate, aid, inspection
   verdict).

For the opener and closer, run the arena playbook instead of a single
authoring pass — first drafts of load-bearing slides are not good enough.

## Rules

- Protected material (contract `protected` list) is never reused or imitated.
- Every gate is mechanical PLUS visual; neither alone concludes a slide.
