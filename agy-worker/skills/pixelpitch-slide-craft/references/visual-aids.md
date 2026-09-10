# Visual aids

A visual aid is the fastest path to one claim, not decoration. If a visual does
not make the point faster or more convincingly than words, it is chart-junk:
remove it and use type and space. The full canon is [craft.md](craft.md).

## Choose the form from the claim

`../scripts/pick_exhibit.py --claim "<what the slide says>"`. It returns the
form, a rendered exemplar built through this skill, and what that form is wrong
for. This page used to carry a claim-to-chart table; the picker replaced it,
because a table of six chart names cannot show you a finished slide and cannot
warn you about the trap each form sets.

Two rules the picker will not tell you. A pie beyond three slices is always
worse than a bar. A dual-axis chart is always two charts pretending to be one.

## Make the visual carry the point

- Annotate the insight directly — a label, an arrow, or one highlighted element.
  If the takeaway is not marked in the chart, the chart is not finished.
- One accent equals the point: draw everything else muted and reserve the brand
  accent for the one mark that is the argument. Color is meaning.
- Maximize data-ink (Tufte): delete gridlines, boxes, 3D, drop shadows, redundant
  legends, axis clutter. Every non-data mark competes with the point.
- Table for values the audience must read; chart for a shape they must see. Never
  swap them.
- When no visual earns its place, do not force one. A single strong sentence set
  large in generous space is a legitimate, often superior, composition.

## Editorial chart grammar

Every chart carries four tiers, authored as argument not geometry:
- a **title** that states the takeaway (the conclusion, not the topic), with
  exactly one accent-colored **keyword** inside it;
- an italic **caption** naming the exhibit and its units or population
  ("Consumer GenAI use, USA — share of adults");
- when sourced, one small italic **source** line.
Keep it to ≤4 series and ≤6 categories, and let the axis top land on a clean
round number rather than a raw maximum.

## Native-conversion note

Prefer native-convertible constructs (inline SVG, CSS grid/flex tables, gradient
fills) per the slide grammar so charts and diagrams stay editable in PowerPoint.
Readability floors and the 1280×720 viewport contract still apply.
