---
name: pixelpitch-brand-derivation
description: Use when a deck must be authored on a customer's own brand and no brand-contract.json or DESIGN.md exists for it yet - the workspace has a template.pptx or rendered specimen pages in refs/ but nothing states the palette, fonts, layout worlds, type hierarchy, photo treatment, or protected motifs. Also use when an authoring step needs an exact theme hex and would otherwise guess one.
---

# Pixelpitch brand derivation

Turn a customer template into a brand contract by reading the artefacts
themselves. Nothing is invented: every colour comes from the package's theme
XML, every layout statement comes from looking at the template's own rendered
slides. The contract downstream code enforces (palette, contrast, one-accent)
and authors against (design_md) is therefore evidence, not taste.

You have the full harness — shell, file tools, subagents, and as many steps as
the derivation needs. Use them.

## Workflow

0. **Stage (if not already done).** The caller normally stages the workspace
   before this turn. If `template.pptx` or `refs/` is missing, run
   `scripts/stage_template.sh <template_gs_uri> <workspace>` yourself — it is
   mechanical I/O (fetch, convert, rasterise), not understanding.
1. **Read the theme (exact values).** `template.pptx` is a ZIP. Unzip it in the
   workspace and read `ppt/theme/theme1.xml`:
   - `<a:clrScheme>`: for each of `dk1 lt1 dk2 lt2 accent1..accent6 hlink
     folHlink`, take `<a:srgbClr val="...">`, or `<a:sysClr lastClr="...">`
     when the entry uses a system colour. Report hex verbatim.
   - `<a:fontScheme>`: `majorFont` and `minorFont` `<a:latin typeface="...">`.
   Follow [references/method.md](references/method.md) for exact commands and
   parsing rules.
2. **Read the visual language.** Inspect EVERY image in `refs/` (rendered
   specimen slides). Spawn subagents to review them in parallel if that is
   faster. Characterise the recurring system — distinct slide worlds,
   decorative devices, type hierarchy, how photography is framed, where each
   accent colour is actually used. Checklist in
   [references/method.md](references/method.md).
3. **Map colour to meaning.** Every observed colour must map to a theme role so
   the palette carries usage semantics (base / brand accent / warm accents /
   categorical data sequence / rules), never decoration.
4. **Measure text on every ground the template actually uses.** Compute the
   WCAG ratio for each palette colour against each recurring ground, and record
   in `contrast_notes` which pairs fall under 4.5:1 and what to do instead. Do
   the arithmetic; do not eyeball it. A palette accent and the ground the plate
   really paints are often a few points apart and land either side of the
   floor, and the rendered slide sits on the plate's pixel rather than on the
   theme's hex. Nothing downstream re-derives this: `deck lint` can only judge a
   colour pair declared in one CSS rule, and `deck shot` only measures runs
   standing on artwork, so a ground inherited from an ancestor is measured by
   no gate at all. Where a colour fails only at small sizes, say so — raising to
   24px reaches the large-text floor of 3.0 and is the fix, lowering contrast
   never is.
5. **Record exclusions.** If any slide or motif is culturally or legally bound
   (e.g. Acknowledgement of Country, Indigenous artwork, compliance or
   attribution pages), record it under `protected` with the reason. Protected
   material is never reused or imitated.
6. **Emit the contract.** Write `brand-contract.json` in the workspace root,
   exactly per [references/contract-schema.md](references/contract-schema.md),
   including `design_md` — a complete authoring guideline built ONLY from the
   values derived above. Start from
   [assets/contract-template.json](assets/contract-template.json); a filled
   one, annotated with what makes each field useful downstream, is in
   [examples/README.md](examples/README.md). End the turn with exactly
   `WROTE brand-contract.json`.

## Non-negotiables

- Every hex in `palette` comes from `theme1.xml`, verbatim. No approximations,
  no "close enough", no invented colours.
- Statements about layout/language must be backed by at least one rendered
  specimen you actually inspected; cite slide numbers in `design_md` where it
  helps the author.
- If the theme and the rendered slides disagree (theme says one thing, slides
  do another), report what the slides do and note the discrepancy in
  `design_md`.
- Never output prose outside the JSON file plus the final one-line
  confirmation.
