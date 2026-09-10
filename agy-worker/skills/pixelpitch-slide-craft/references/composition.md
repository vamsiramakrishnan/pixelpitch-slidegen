# Skill and slide composition

The full creative catalog remains discoverable. Progressive disclosure is a
latency and context technique, not a capability restriction.

## Slide geometry

Four decisions place everything on a slide. Make them explicitly, because the
defaults a model reaches for are the four that make a deck look automatic.

### Set display type to a measure, not to the frame

Give the display block a `max-width` and let the line break fall out of it. The
measure is the decision; the wrap is the consequence. Around 80 to 90 percent of
the content width puts a two- or three-word headline on two lines with the last
line short, which is the shape that reads as composed.

Neither failure mode is subtle. Without a measure, a heavy headline runs the
full width as one strip and the space above it becomes a hole. Told instead to
"wrap onto several lines", the same model narrows the block until every word
sits on its own line and drops the type size to make it fit. Both were produced
from the same brief in this repo, twenty minutes apart. Say how wide the block
is and neither happens.

`--measure-display` and `--measure-title` in
[assets/slide-base.css](../assets/slide-base.css) carry the numbers.

### Anchor the block, and say which end

A content slide sits in the top two thirds with the base open; that is the
standing rule in `pixelpitch-deck`'s gate law and it is right for slides that
argue. It is wrong for slides that declare. A cover, a section marker, a
statement, a closing slide puts its block on the *floor* of the field, above the
footer rule, and leaves the space above it empty. The emptiness is the
composition, not a gap to fill, and it is the single move that most separates an
editorial deck from a competent one.

Choose per slide role and commit. A deck that centres everything vertically has
made no choice at all.

### Chrome is a frame, or it is absent

A hairline bar at the top edge and another at the bottom, carrying section on
the left and deck or number on the right, at one size, in one face. Both bars or
neither: a top bar alone reads as a stranded header. On declarative slides
suppress the whole frame and let the type own the field.

Chrome type sits at the label floor, never below it. It is the first thing a
model shrinks when a slide feels tight, and it is the one place shrinking is
never the answer.

### One ink, varied by opacity

Hierarchy in a restrained system comes from one text colour at three or four
opacities, not from three greys. Two hues on a slide, one surface and one ink,
is enough for a cover; the accent is a third and needs a reason. Count the
distinct colours a slide resolves to. Five is an editorial system. Twelve is a
palette that was never decided.

## Skill selection

- Always apply the core Pixelpitch slide contract.
- Honor skills or styles explicitly requested by the user.
- Select additional skills from the brief: research, data visualization,
  image generation/editing, editorial storytelling, diagramming, finance,
  product, or another relevant discipline.
- Load only the bodies and references needed for the current phase. The rest of
  the catalog stays available for later phases or subagents.
- A skill may contribute craft or assets without changing the output away from
  a presentation.

## Parallel slide generation

First freeze shared interfaces:

1. narrative arc and slide objectives;
2. design tokens and typography floors;
3. template/source-slide inventory or HTML component grammar;
4. asset manifest and attribution requirements;
5. per-slide structured payload.

Then generate independent slides concurrently. Each worker owns one slide
source and returns the same schema: title, subtitle, body, visual intent,
citations, notes, and preview. Conversion and deck assembly begin as soon as
their dependencies are ready. A single coordinator validates cross-slide
rhythm, repetition, sequencing, and closing consistency.

Never let parallel workers independently reinvent the palette, type scale,
template interpretation, or shared components.
