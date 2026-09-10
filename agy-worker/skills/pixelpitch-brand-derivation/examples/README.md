# A derived contract, filled in

[brand-contract.json](brand-contract.json) is a complete contract for a
fictional template, `northwind-master.pptx`. It is the shape
[../references/contract-schema.md](../references/contract-schema.md)
specifies, with every field carrying the kind of content that makes it useful
downstream rather than the kind that makes it look filled.

The empty skeleton to start from is
[../assets/contract-template.json](../assets/contract-template.json). The
`palette_source` values there are the theme roles you will be reading out of
`ppt/theme/theme1.xml`; add or drop roles to match what the template actually
declares.

Four things to copy from the worked example.

**Every usage role names a job, not a colour.** `brand`, `data_1`, `rule`.
`palette_source` keeps the provenance next to it, so a hex that later looks
wrong can be traced back to the clrScheme entry it came from.

**Statements cite the specimens they came from.** "Ledger (03-05, 07-08,
15-17)" is checkable. "Clean, modern grid" is not, and it is the sentence a
model writes when it has not looked.

**`visual_aids_style` is specific enough to draw from.** It names the
baseline colour, the label side, the series order, and what the accent is
reserved for. A contract that says "charts should be clean and on-brand" has
not done the job, and the deck that follows it will grow generic chart
furniture.

**`design_md` introduces no new colour.** Every hex in it appears in
`palette`. That is mechanically checkable and worth checking before you
finish:

```bash
python3 - <<'PY'
import json, re
c = json.load(open("brand-contract.json"))
allowed = {v.upper() for v in c["palette"].values()}
found = {h.upper() for h in re.findall(r"#[0-9A-Fa-f]{6}", c["design_md"])}
print(sorted(found - allowed) or "clean")
PY
```

The `protected` entry is the other thing to imitate. Slide 02 is an
Acknowledgement of Country, so it is recorded with its reason and never
reused, imitated, restyled, or offered as a layout reference.
