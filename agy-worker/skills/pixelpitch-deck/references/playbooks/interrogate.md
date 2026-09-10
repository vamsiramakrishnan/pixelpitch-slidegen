# Playbook: interrogate (adversarial review)

For: trying to fail the deck before the audience does. Run before delivery,
after any repair, or on demand ("review this deck").

## Steps (verbatim)

1. Read the principles index. Render the current deck to per-slide PNGs if
   not already fresh.
2. Spawn FOUR independent review panels as parallel subagents. Each sees the
   slide renders plus ONE lens and must HUNT for failures, not summarize:
   - **slop-hunter**: AI tells (eyebrows, card grids, gradient text,
     centred-everything, second accent for variety, em-dash titles).
   - **template-fidelity**: every slide on its specimen plate, artwork
     intact, aids in the template's furniture vocabulary, no freehand.
   - **message-clarity**: titles are assertions and read alone as the
     argument; one point per slide; so-whats stated; aids annotated.
   - **brand-and-legibility**: palette only from the contract; contrast
     floors; no overflow, residue, or double-exposure.
3. Each panel returns a numbered findings list with slide numbers and
   evidence (what it SAW). No findings = "PANEL PASS".
4. Deduplicate findings; classify each as FIX-NOW (visible defect),
   JUDGMENT (defensible, record it), or WONTFIX (with reason).
5. FIX-NOW items route to the repair playbook scoped to those slides.
   Re-render and re-run only the panels whose findings were fixed.
6. Report: the panels' verdicts, the classification of every finding, and
   what changed. An interrogate that found nothing must say what it looked
   at.

## Rules

- Panels are adversarial on purpose: their job is failures found now, not
  praise.
- A finding without visual evidence (slide number + what is seen) is
  discarded.
- Never let a panel edit the deck; separation between judging and fixing.
