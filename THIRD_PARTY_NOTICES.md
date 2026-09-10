# Third-party notices

Pixelpitch Slidegen is distributed under the [Apache License 2.0](LICENSE),
except for the third-party material identified below. Existing source-file
copyright notices remain in place. Dependency packages installed by `uv`, npm,
or the container package manager retain their own licenses.

This standalone repository includes four project-specific authoring skills.
It does not include the former general-purpose repository skill collection.
The style index described below contains compact descriptors, not those full
skill packages or their reference decks.

## Pixelpitch and Open Design

This project was extracted from Pixelpitch. Its vendored Slidify source is in
`vendor/slidify/`, and its agent, renderer, and four core skills retain their
existing Apache-2.0 licensing and copyright headers.

Pixelpitch's bundled design and skill material was developed from
[Open Design](https://github.com/nexu-io/open-design), licensed under Apache-2.0.
The original Pixelpitch attribution records upstream commit
`3c954ad2b322e81a37e694d4b210f73742798538` at copy time. Relevant retained
material consists of the curated brand descriptions in `app/assets/brands/`
and extracted style metadata in `app/assets/styles/index.json`.
The desktop application, web application, daemon, and general-purpose skill
collection are not part of this distribution.

## Frontend Slides

[Frontend Slides](https://github.com/zarazhangrui/frontend-slides) is licensed
under MIT, copyright 2025 Zara Zhang. Its full license is preserved in
[licenses/frontend-slides-MIT.txt](licenses/frontend-slides-MIT.txt).

The following files adapt its authenticity rules, style-selection workflow,
or structured selection-index pattern:

- `agy-worker/skills/pixelpitch-slide-craft/scripts/slop.py`
- `agy-worker/skills/pixelpitch-slide-craft/scripts/pick_exhibit.py`
- `agy-worker/skills/pixelpitch-slide-craft/examples/index.json`
- `agy-worker/skills/pixelpitch-deck/references/playbooks/new-deck.md`

The example slide content and the project-specific implementations are
Pixelpitch's own. The index structure and the adapted workflow remain
credited to Frontend Slides.

## Impeccable

`agy-worker/skills/pixelpitch-slide-craft/references/anti-slop.md` preserves
rules from [Impeccable](https://github.com/pbakaus/impeccable). Pixelpitch adds
a precedence section explaining how those rules interact with Slidify's
conversion capabilities. The file identifies the retained upstream text.

Impeccable is licensed under Apache-2.0, copyright 2025 Paul Bakaus. Its
license and copyright notice are preserved in
[licenses/impeccable-Apache-2.0.txt](licenses/impeccable-Apache-2.0.txt).

## Extracted style descriptors

`app/assets/styles/index.json` retains style names, descriptions, palettes,
font names, and usage guidance extracted from Pixelpitch's style collection.
The full reference HTML decks and general-purpose skills are not included.
The relevant upstream sources are:

- [Beautiful HTML Templates](https://github.com/zarazhangrui/beautiful-html-templates),
  copyright 2026 Zara Zhang, MIT. This covers the `html-ppt-zhangzara-*`
  descriptors. See
  [licenses/beautiful-html-templates-MIT.txt](licenses/beautiful-html-templates-MIT.txt).
- [HTML PPT Skill](https://github.com/lewislulu/html-ppt-skill), copyright 2026
  lewis, MIT. This covers the descriptors adapted from that project's
  presentation styles. See
  [licenses/html-ppt-skill-MIT.txt](licenses/html-ppt-skill-MIT.txt).
- [HTML Anything](https://github.com/nexu-io/html-anything), Apache-2.0.
  This covers the `deck-guizang-editorial`, `deck-open-slide-canvas`,
  `deck-swiss-international`, and `ppt-keynote` descriptors. The Apache-2.0
  terms are included in the root [LICENSE](LICENSE).
- [Kami](https://github.com/tw93/kami), copyright 2026 Tw93, MIT.
  The `kami-deck` descriptor derives from Pixelpitch's Kami-inspired design
  system. See [licenses/kami-MIT.txt](licenses/kami-MIT.txt).

Other descriptors were authored within Pixelpitch and retain the repository's
Apache-2.0 license.

## Brand descriptions

`app/assets/brands/` retains curated design descriptions from Pixelpitch's
design-system collection. That collection credits
[Awesome Design MD](https://github.com/VoltAgent/awesome-design-md),
copyright 2026 VoltAgent, MIT, for its imported product design systems.
The full license is preserved in
[licenses/awesome-design-md-MIT.txt](licenses/awesome-design-md-MIT.txt).

These descriptions are independent design references, not official brand
guidelines or an endorsement by the named companies. Brand names and
trademarks belong to their respective owners. Font names in the descriptions
do not grant a license to proprietary fonts; no proprietary font files are
bundled here. Customers must have permission to use any templates, logos,
fonts, or other brand assets they supply.

## License verification

The third-party license copies in `licenses/` were checked against their
upstream repositories on September 10, 2026. Their links identify the sources;
the bundled license text is the copy distributed with this release.
