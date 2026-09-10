# Derivation method

Exact, repeatable steps. Everything runs inside the staged workspace.

## 1. Theme extraction (exact values)

`template.pptx` is a ZIP. Extract and read the theme:

```bash
mkdir -p unzipped && cd unzipped && unzip -o ../template.pptx 'ppt/theme/*' -d .
cat ppt/theme/theme1.xml
```

Parse by hand from the XML you actually read:

- `<a:clrScheme name="...">` — for each child `dk1 lt1 dk2 lt2 accent1
  accent2 accent3 accent4 accent5 accent6 hlink folHlink`:
  - `<a:srgbClr val="RRGGBB"/>` → that hex, verbatim, uppercase.
  - `<a:sysClr val="windowText" lastClr="RRGGBB"/>` → use `lastClr`.
- `<a:fontScheme>` → `<a:majorFont><a:latin typeface="..."/>` and
  `<a:minorFont><a:latin typeface="..."/>`.

Do not round, brighten, or "fix" any value. If two roles share a hex, report
both roles with the same hex and say so in `design_md`.

## 2. Visual language observation

Inspect every `refs/slide-NN.png`. For each distinct composition you find,
record:

- **Slide worlds** — the recurring background/ground treatments (e.g. full
  colour statement slides vs white content slides). Name each world and list
  which slide numbers use it.
- **Decorative devices** — curves, wave slivers, overlapping circles, masks,
  logo lockups, running headers/footers. Describe each precisely enough that
  an author can rebuild it: where it sits, what colour, roughly how large.
- **Type hierarchy** — title vs subhead vs label vs numeral treatment: case,
  weight, alignment, relative scale, where the number/label sits.
- **Photography** — subject matter, framing, masking (curves? circles?
  full-bleed?), and whether imagery carries meaning or mood.
- **Accent discipline** — where each accent appears and what job it does.
  Count how many accents appear on ONE slide; that is the template's real
  one-accent (or N-accent) rule.

## 3. Colour-to-meaning mapping

Assign every theme role a usage role:

| Usage role | Typical theme source | Job |
|---|---|---|
| `base_dark` | dk1 / dk2 | text on light, dark panels |
| `base_light` | lt1 / lt2 | page ground, text on dark |
| `brand` | accent1 (usually) | the default single accent |
| `warm_*` | accent4/5-type hues | sparing emphatic accents |
| `data_*` | remaining accents | categorical chart sequence |
| `rule` | lt2 / greys | hairlines, muted fills |
| `system` | hlink/folHlink artefacts | never use |

If observed usage contradicts this table (e.g. accent1 never appears on
slides), say so and map by what the slides actually do.

## 4. Protected material

Any slide that is culturally, legally, or semantically bound — acknowledgements,
compliance, disclaimers, attribution, Indigenous or licensed artwork — goes in
`protected` with slide numbers and the reason. The author must not reuse or
imitate it.

## 5. Quality bar

Before writing `brand-contract.json`:

- [ ] every clrScheme role accounted for in `palette`
- [ ] fonts verbatim from the font scheme
- [ ] every layout claim backed by slide numbers you inspected
- [ ] every accent claim counted on the actual slides
- [ ] protected slides listed
- [ ] `design_md` contains no colour that is not in `palette`
