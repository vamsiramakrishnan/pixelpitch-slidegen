# Brand contract schema

`brand-contract.json` is written to the workspace root. Exactly these keys,
no extras:

```json
{
  "palette": { "<usage_role>": "#RRGGBB" },
  "palette_source": { "<usage_role>": "<theme role it came from>" },
  "fonts": { "major": "<typeface>", "minor": "<typeface>" },
  "accent_rules": "<how many accents per slide, and what each is for>",
  "contrast_notes": "<measured WCAG ratios: which palette/ground pairs fall under 4.5:1, and what to do instead>",
  "layout_worlds": [
    "<named composition, which slides use it, its ground/structure>"
  ],
  "decorative_devices": [
    "<device, placement, colour, approximate scale>"
  ],
  "type_hierarchy": "<title/subhead/label/numeral treatment>",
  "photo_treatment": "<subject, framing, masking, meaning-vs-mood>",
  "visual_aids_style": "<how data/charts/timelines/big numbers should look in this brand>",
  "protected": [ { "slides": ["NN"], "reason": "..." } ],
  "dos": ["..."],
  "donts": ["..."],
  "design_md": "<complete authoring guideline as markdown, built ONLY from the above>"
}
```

Rules:

- `palette` keys are usage roles (`base_dark`, `base_light`, `brand`,
  `warm_*`, `data_*`, `rule`); every value is a verbatim theme hex.
- `palette_source` preserves provenance: which clrScheme role each usage role
  came from. Downstream enforcement audits against `palette`; provenance keeps
  the contract debuggable.
- `visual_aids_style` is required: decks die without visual aids, so the
  contract must say what an on-brand chart, timeline, or hero number looks
  like (axis/label treatment, data colour sequence from `data_*`, annotation
  accent discipline).
- `design_md` must be immediately usable as the authoring guideline: palette
  table with roles, type rules, the layout worlds with their devices, the
  visual-aids style, and do/don't lists. No colour may appear in `design_md`
  that is not in `palette`.
