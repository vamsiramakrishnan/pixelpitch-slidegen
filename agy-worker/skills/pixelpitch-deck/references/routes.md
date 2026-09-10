# Deck routes

Choose the route from the artifact the user supplied and the fidelity they need.

## Native customer template

Use when the user provides or names a PPTX template. Inventory both layers:

- masters and layouts: placeholder geometry, theme, fonts, colors;
- live specimen slides: real pictures, crops, curves, groups, tables, lockups,
  and the template author's intended layout combinations.

Populate safe specimen slides in place and reorder their existing slide IDs.
Never rebuild an image-bearing specimen from its layout unless it is a bounded
fallback. Do not hard-code a brand name, source slide number, or template text.
Score candidates by content capacity, archetype fit, visual richness, safe
semantics, and prior usage.

Do not repurpose culturally, legally, or semantically protected specimens for
unrelated content. Examples include acknowledgement, compliance, disclaimer,
and attribution slides. Preserve their meaning or leave them unused.

## Fresh design-system deck

Use when no native PPTX is authoritative. Compose the active `DESIGN.md`, the
core slide grammar, and any relevant creative skills. Produce independent HTML
slides at 1280x720 and a structured slide manifest, then convert with Slidify.

## Repair or refinement

Use when a PPTX already exists. Inspect the emitted package and its rendered
pages before changing it. Distinguish source-authoring defects, conversion
defects, missing relationships, unreadable type, and delivery/UI defects. Keep
the user's existing content and unaffected slide relationships intact.

## Source round-trip (highest-fidelity creative route)

Use when the customer's template must be the actual construction AND the deck
needs full creative treatment. Extract each specimen's exact construction
with `deck spec`, reconstruct it as HTML (clean plate plus native overlay, or
plate plus vector aid), enrich with the narrative and visual aids, and convert
back through slidify. The `pixelpitch-template-roundtrip` skill owns this
route; its `references/source-roundtrip.md` is the method and its `examples/`
show it worked through.
