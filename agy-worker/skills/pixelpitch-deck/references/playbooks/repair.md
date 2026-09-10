# Playbook: repair

For: specific slides are wrong. The rest of the deck is trusted and must not
be touched.

## Steps (verbatim)

1. Read the principles index. Establish the failure class from evidence
   (render inspection, lint, report): double-exposure / residue,
   off-template aid or freehand slide, overflow or collision, wrong copy,
   broken conversion.
2. Scope: list exactly the slides to change and the slides that must NOT
   change. State both in the report.
3. Fix at the right layer:
   - residue/double-exposure → the plate is dirty or the overlay is on the
     base render; rebuild the plate / retarget the `<img>`, correct overlay
     geometry from the spec.
   - off-template aid → redraw in the template's furniture vocabulary on
     the specimen plate; never restyle the generic version.
   - freehand slide → put it on a real specimen plate first, then re-add
     content.
   - overflow/collision → correct the HTML/CSS (position, size, wrapping)
     from the spec values; never shrink below the readability floors.
   - wrong copy → rewrite per the brief; assertion titles stay assertions.
4. Re-run `deck stage`, reconvert, re-run the gate set, and personally
   inspect the repaired slides against their plates AND one untouched slide
   (to prove it wasn't disturbed).
5. Report: failure class, slides touched, evidence before/after (what you
   SAW), and the hunt-list or gate line added so this class is caught
   automatically next time (encode the lesson).

## Rules

- Repairs are surgical: one class, named slides, minimal diffs.
- A repair that regresses another slide is a failed repair; revert and redo.
