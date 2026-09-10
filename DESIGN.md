---
name: Pixelpitch Slidegen A2UI
description: Native Gemini Enterprise controls for briefing, reviewing, and refining editable presentations.
colors:
  primary: "#0B57D0"
typography:
  heading:
    fontSize: "26px"
    fontWeight: 600
    lineHeight: 1.25
  section:
    fontSize: "18px"
    fontWeight: 600
  body:
    fontSize: "14px"
    lineHeight: 1.5
rounded:
  preview: "8px"
  disclosure: "12px"
spacing:
  compact: "8px"
  related: "12px"
  fields: "16px"
  gallery: "20px"
  sections: "24px"
---

# Design System: Pixelpitch Slidegen A2UI

## Overview

This module presents an editable brief, deck previews, download, and revision
controls inside Gemini Enterprise. Its visual authority is the host's existing
Material interface. The form stays compact and quiet so the presentation can
take priority when it arrives.

The implementation lives in [app/a2ui_composer.py](app/a2ui_composer.py), with
turn behavior in [app/pixelpitch_agent.py](app/pixelpitch_agent.py). Product
context comes from [README.md](README.md) and the module's agent guidance.

## Colors

Google blue is the fixed primary accent for every emitted A2UI view. A selected
brand or reference template changes the deck, never the interface accent.
Text, backgrounds, borders, and control states inherit the host theme. Do not
extract a neutral palette from the local screenshot approximation.

## Typography

Font families inherit from Gemini Enterprise. The composer sets the heading,
section, and body sizes above, using Material text roles for their semantics.
Error titles use 20px at weight 600. Captions retain the body size, and long
text can wrap anywhere to avoid overflowing a narrow conversation column.

## Layout

Use a card-free vertical form at 100% width, capped at 760px, with 8px vertical
root padding. Section spacing separates the brief, visual direction, and
actions. Audience and slide count share a wrapping row; brand and slide style
use equal flexible columns. Controls have zero minimum width where needed so
the host can narrow the layout without horizontal scrolling.

Template and secondary preview galleries wrap items with a 280px flex basis.
The first available slide preview occupies the full content width, preserves
its 16:9 frame, and uses `contain` so the slide is not cropped.

## Elevation & Depth

The composer adds no cards or shadows. Expansion panels have no shadow;
spacing and the host's Material treatment separate optional content.

## Shapes

Preview images and disclosures use the radii above. Inputs, selects, and
buttons retain native Material shapes, focus behavior, and interaction states.
Buttons have a minimum height of 44px and cannot exceed their container width.

## Components

- The brief exposes topic, audience, slide count, and outcome and tone directly.
  `Generate deck` validates the topic and slide count and requires a brand or
  reference template before enabling generation.
- Brand, slide style, template, and revision choices use `MaterialSelect` with
  scalar data-model bindings. Selecting a template reactively disables the
  overridden brand and style controls. Clearing it restores those controls.
- Reference browsing is optional. A closed expansion panel contains up to six
  admitted templates, with preview images when available. The select owns the
  selection; gallery entries are visual references.
- Results pair a filled `Download PowerPoint` action with the full-width cover
  preview. Further previews and the deck outline start collapsed. Missing
  previews or downloads receive explicit text, and partial preview sets show
  how many slides are represented.
- Revision combines a preset direction with optional custom detail. The tonal
  `Review changes` action reopens the updated brief before generation. The text
  action `Start a new deck` opens a fresh brief.
- Error views offer `Edit brief` through the `edit_brief` action, carrying the
  submitted fields back into intake. Stale brand, style, or template choices
  use this same recovery. When no brands or templates load, `Reload choices`
  sends all current field bindings through `edit_brief`, preserving unsent
  edits. Catalog and slide-count normalization still apply.
- Progress remains text during generation. A2UI intake, result, and error
  views appear at turn boundaries.

## Do's and Don'ts

- Do keep native Material controls, readable labels, image descriptions, and
  disclosure semantics. Keep deck branding inside the artifact.
- Don't add a branded application shell, custom control theme, or decorative
  card grid to this module's Gemini Enterprise UI.
- Do distinguish validation from host rendering. The composer validates
  against the Gemini Enterprise composite catalog. Local layout screenshots
  and checks are under [.tmp/a2ui-ux](.tmp/a2ui-ux) and use an approximation
  with sample content. Authenticated Gemini Enterprise rendering and deployment
  of this redesign have not been verified. These are not Gemini Enterprise
  screenshots.
