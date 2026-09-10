# The gallery

Sixteen slides that passed every gate, plus one that fails all of them.

Do not read this directory. Query it:

```bash
python3 ../scripts/pick_exhibit.py --claim "how long the defect went undetected"
```

You get one form back, with the exemplar PNG to open, the HTML to start from,
and the specific mistake that form invites. `--role hero` narrows it, `--id`
fetches one by name, and no arguments lists all sixteen in one line each. The
metadata lives in [index.json](index.json), which the script reads so you do
not have to.

The exemplars come from four decks built end to end through this skill set: a
grid-capacity board deck, a clinical readout, a manufacturing postmortem, and a
second pass at the grid deck in a louder register. Four palettes, sixteen
exhibit forms, no repeated skeleton. Every slide was rendered, probed for
clipped and overlapping text, and looked at. Every one converts to PPTX with
zero rasterised units and fully editable text.

## The loud register

Three of the sixteen are deliberately bold: [field-declarative](field-declarative.png),
[statement-single-line](statement-single-line.png), and
[split-field-positions](split-field-positions.png). They run one ink and one
accent with no third hue, put type at display size against a saturated ground,
and use a hairline as the only depth device. Nothing in them is decorative,
which is the point. Volume in a deck comes from scale, contrast, and an empty
half of the frame, not from shadows, gradients, or cards.

They are here because the rest of the gallery is quiet, and a skill whose every
exemplar is restrained teaches restraint as the only option. Their geometry is
written up in [../references/composition.md](../references/composition.md).

## The negative control

[slop-anti-example.html](slop-anti-example.html) carries every mechanically
detectable tell in one slide, each annotated with the rule id it trips. It is
here so the gate can be proved to fire, not to be copied.

```bash
cd ..
python3 scripts/lint_slides.py examples
```

The sixteen good slides never appear in the output. The anti-example reports
every registered rule and the script exits 1. Pass a `brand-contract.json` as a
second argument and the palette and contrast gates run as well.

Both halves are held by tests rather than by this paragraph.
`scripts/test_slop.py` asserts the anti-example still trips every rule and that
no exemplar trips any, so adding a detector without extending the negative
control fails the suite instead of quietly making this page wrong.

## What the gallery cannot do

An exemplar shows a form that worked for one claim. Copying its HTML without
changing its argument produces a slide about nothing, which every gate here
will pass. And no slide is finished until someone has opened its rendered PNG,
which is `pixelpitch-deck`'s gate law.
