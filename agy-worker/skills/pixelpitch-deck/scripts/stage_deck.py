#!/usr/bin/env python3
# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Fold N standalone slide documents into one deck.html you can present from.

Three playbooks tell the author to "assemble deck.html" and nothing assembled
it, so the deliverable a human actually stands in front of was the one artifact
with no lever and no gate behind it. This is that lever.

The output is deliberately not a second format. It carries the same `.slide`
elements at authored size, so `slidify convert deck.html` yields the same
slides as converting the directory, and `<pp-deck>` adds presentation on top
without the conversion path ever seeing it.

Merging the stylesheets is the hard part. Every slide arrives as a full
document with its own `<style>`, and concatenating those blindly lets slide 5's
two-column `.slide` rule reflow slide 1. The first attempt here refused to
build when two slides defined a selector differently, which was wrong in a way
worth recording: slides are *supposed* to differ. `slop.lint_deck` fails a deck
whose slides all share one skeleton, so an assembler that demands they share one
skeleton is asking for the defect the lint exists to catch.

So each slide's sheet is scoped to that slide instead, by prefixing every
selector with the slide's id. Nothing has to agree, and nothing leaks. The
only things that cannot be scoped are the globals — `@keyframes` and
`@font-face` names — and those genuinely must agree, so those alone refuse.

Stdlib only.
"""

from __future__ import annotations

import argparse
import html as html_mod
import json
import re
import sys
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
STAGE_ASSET = SKILL_DIR / "assets" / "deck-stage.js"

_SLIDE_OPEN = re.compile(
    r"""<(?P<tag>\w+)(?P<pre>[^>]*\bclass\s*=\s*"""
    r"""(?P<q>["'])(?P<cls>[^"']*\bslide\b[^"']*)(?P=q))""",
    re.I,
)
_STYLE = re.compile(r"<style\b[^>]*>(.*?)</style>", re.I | re.S)
_BODY = re.compile(r"<body\b[^>]*>(.*?)</body>", re.I | re.S)
_TITLE = re.compile(r"<title\b[^>]*>(.*?)</title>", re.I | re.S)
_COMMENT = re.compile(r"/\*.*?\*/", re.S)

# Names for the whole document, back when the slide was the whole document.
_DOCUMENT_PIECE = re.compile(r"^(?:html|body|:root)$", re.I)
# One piece of a compound selector: `section`, `.slide`, `#id`, `:hover`,
# `::before`, `[data-x]`, `*`.
_PIECE = re.compile(
    r"[a-zA-Z][\w-]*|\.[-\w]+|\#[-\w]+|::?[-\w]+(?:\([^)]*\))?|\[[^\]]*\]|\*"
)
# A viewport size on `html`/`body` sized the slide when the slide was the whole
# document. Here it would size the deck, so it goes. `.slide` is not in this
# set: its own 1280x720 is the frame every gate measures against, and stripping
# it is how the staged deck starts overflowing where the directory did not.
_IS_DOCUMENT = re.compile(r"^(?:html|body|:root)$", re.I)
_PAGE_SIZING = re.compile(r"^\s*(?:width|height|min-height|max-height)\s*:", re.I)
_GLOBAL_AT = re.compile(r"^@(keyframes|font-face|counter-style|property)\b", re.I)


class StageError(SystemExit):
    """Raised with a message the author can act on without reading this file."""


def _rules(css: str) -> list[tuple[str, str]]:
    """Split a stylesheet into (prelude, body) pairs by brace matching.

    Not a CSS parser. It handles the constructs slide sheets actually use —
    plain rules, `@media`, `@keyframes` — and treats anything nested as opaque
    text belonging to its outer prelude, which is all the comparison needs.
    """
    css = _COMMENT.sub("", css)
    out: list[tuple[str, str]] = []
    depth = 0
    start = 0
    prelude = ""
    for i, ch in enumerate(css):
        if ch == "{":
            if depth == 0:
                prelude = css[start:i]
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                out.append((" ".join(prelude.split()), css[css.index("{", start) + 1 : i]))
                start = i + 1
    return out


def _normalise(body: str) -> str:
    """Declarations compared for sameness, not for formatting."""
    return ";".join(sorted(part.strip() for part in body.split(";") if part.strip()))


def _split_commas(selector: str) -> list[str]:
    """Top-level commas only. A comma inside :is(), :not() or [attr] is not one."""
    parts: list[str] = []
    depth = 0
    current = ""
    quote = ""
    for ch in selector:
        if quote:
            if ch == quote:
                quote = ""
        elif ch in "\"'":
            quote = ch
        elif ch in "([":
            depth += 1
        elif ch in ")]":
            depth -= 1
        elif ch == "," and depth == 0:
            parts.append(current)
            current = ""
            continue
        current += ch
    parts.append(current)
    return [p.strip() for p in parts if p.strip()]


def _leading_compound(part: str) -> tuple[str, str]:
    """Split a selector at its first combinator. `.a.b > c` -> (`.a.b`, ` > c`)."""
    depth = 0
    for i, ch in enumerate(part):
        if ch in "([":
            depth += 1
        elif ch in ")]":
            depth -= 1
        elif depth == 0 and (ch.isspace() or ch in ">+~"):
            return part[:i], part[i:]
    return part, ""


def _names_the_slide(compound: str, root: tuple[str, frozenset[str]]) -> str | None:
    """The compound rewritten onto the slide element, or None if it is a descendant.

    A selector names the slide when it names the document (`body`), or when
    every tag and class it mentions is one the slide element actually carries.
    The second half is the case that is easy to miss: slide roots are written
    `class="slide slide--split"`, so `.slide--split` is the slide, not something
    inside it. Scoped as a descendant it matches nothing, and the two-column
    grid it defines silently stops applying while the deck still builds green.
    """
    tag, classes = root
    pieces = _PIECE.findall(compound)
    if "".join(pieces) != compound:
        return None
    kept: list[str] = []
    named = False
    for piece in pieces:
        if _DOCUMENT_PIECE.match(piece):
            named = True
        elif piece.startswith("."):
            if piece[1:] not in classes:
                return None
            named = True
        elif piece[0].isalpha():
            if piece.lower() != tag:
                return None
            named = True
        elif piece == "*":
            return None
        else:
            kept.append(piece)
    return "".join(kept) if named else None


def scope_selector(selector: str, scope: str, root: tuple[str, frozenset[str]]) -> str:
    """Confine a selector to one slide.

    Selectors that name the slide itself become `#sN`; everything else becomes
    a descendant of it. `html, body` collapses onto one selector, so duplicates
    are dropped.
    """
    out: list[str] = []
    for part in _split_commas(selector):
        compound, rest = _leading_compound(part)
        tail = _names_the_slide(compound, root)
        scoped = f"{scope}{tail}{rest}" if tail is not None else f"{scope} {part}"
        scoped = " ".join(scoped.split())
        if scoped not in out:
            out.append(scoped)
    return ", ".join(out)


def _scope_rules(
    sheet: str,
    scope: str,
    root: tuple[str, frozenset[str]],
    globals_: dict,
    index: int,
    conflicts: list,
) -> list[str]:
    lines: list[str] = []
    for prelude, body in _rules(sheet):
        if _GLOBAL_AT.match(prelude):
            # Names in a global namespace. Two slides may not disagree about
            # what `@keyframes rise` means, because only one can win.
            shape = _normalise(body)
            first = globals_.get(prelude)
            if first is None:
                globals_[prelude] = (index, shape)
                lines.append(f"{prelude} {{{body}}}")
            elif first[1] != shape:
                conflicts.append(
                    f"`{prelude}` means different things on slide {first[0] + 1} and "
                    f"slide {index + 1}. That name is global to the deck, so rename "
                    "one of them."
                )
            continue
        if prelude.startswith("@"):
            # A conditional group. Its prelude stays; its contents get scoped.
            inner = _scope_rules(body, scope, root, globals_, index, conflicts)
            if inner:
                lines.append(prelude + " {\n" + "\n".join(inner) + "\n}")
            continue
        if all(_IS_DOCUMENT.match(p) for p in _split_commas(prelude)):
            body = ";".join(d for d in body.split(";") if d.strip() and not _PAGE_SIZING.match(d))
        if body.strip():
            lines.append(f"{scope_selector(prelude, scope, root)} {{{body}}}")
    return lines


def merge_styles(
    sheets: list[str], scopes: list[str], roots: list[tuple[str, frozenset[str]]]
) -> tuple[str, list[str]]:
    """One stylesheet for the deck, plus the conflicts that block the build."""
    globals_: dict[str, tuple[int, str]] = {}
    conflicts: list[str] = []
    blocks: list[str] = []
    for index, (sheet, scope, root) in enumerate(zip(sheets, scopes, roots)):
        rules = _scope_rules(sheet, scope, root, globals_, index, conflicts)
        if rules:
            blocks.append(f"/* slide {index + 1} */\n" + "\n".join(rules))
    return "\n\n".join(blocks), conflicts


def _slide_root(record: dict, index: int) -> tuple[str, re.Match[str]]:
    """The slide's body content and the match that opens its root element."""
    document = record.get("html") or ""
    match = _BODY.search(document)
    inner = (match.group(1) if match else document).strip()
    opener = _SLIDE_OPEN.search(inner)
    if not opener:
        raise StageError(
            f'stage: slide {index + 1} has no element with class="slide". The '
            "stage and slidify's splitter both key on that class; it is the "
            "contract, not a style hook."
        )
    return inner, opener


def _root_signature(record: dict, index: int) -> tuple[str, frozenset[str]]:
    """The tag and classes a rule could use to name this slide's root element."""
    _, opener = _slide_root(record, index)
    return opener.group("tag").lower(), frozenset(opener.group("cls").split())


def _slide_markup(record: dict, index: int, scope: str) -> str:
    """One slide's body content, carrying the id its scoped CSS refers to."""
    inner, opener = _slide_root(record, index)
    attrs = f' id="{scope.lstrip("#")}"'
    notes = record.get("notes") or ""
    if notes and "data-pptx-notes" not in inner:
        # Authored out of band in index.json rather than inline. Put it where
        # slidify reads it, so the deck and the PPTX cannot disagree.
        attrs += f' data-pptx-notes="{html_mod.escape(notes, quote=True)}"'
    cut = opener.start("pre")
    return inner[:cut] + attrs + inner[cut:]


def build(slides: list[dict], title: str) -> str:
    scopes = [f"#s{i + 1}" for i in range(len(slides))]
    sheets = [
        "\n".join(m.group(1) for m in _STYLE.finditer(record.get("html") or ""))
        for record in slides
    ]
    roots = [_root_signature(record, i) for i, record in enumerate(slides)]
    sheet, conflicts = merge_styles(sheets, scopes, roots)
    if conflicts:
        raise StageError("stage: names that are global to the deck collide.\n  - " + "\n  - ".join(conflicts))

    bodies = "\n\n".join(
        _slide_markup(record, i, scopes[i]) for i, record in enumerate(slides)
    )
    # Every `</` in the script becomes `<\/`. A browser ends an inline script
    # only at `</script`, so this is invisible there; libxml2, which slidify's
    # splitter parses with, follows the older SGML rule and ends the element at
    # the *first* `</` it sees. One `</pp-deck>` in a doc comment was enough to
    # spill the rest of the file into the body as markup, which converted as a
    # phantom "Speaker notes" shape and a second copy of every slide. The
    # sequence never appears outside a string or a comment, so replacing it
    # wholesale is safe: in a string it is the identical escape, in a comment it
    # is cosmetic.
    script = STAGE_ASSET.read_text(encoding="utf-8").replace("</", "<\\/")
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>{html_mod.escape(title)}</title>
<style>
html, body {{ margin: 0; padding: 0; background: #111; }}
/* Before the script upgrades it, and in slidify's parse where it never does,
   pp-deck is an unknown element and therefore inline. An inline wrapper puts
   the slide on a text baseline and the whole frame lands a line's leading too
   low, which reads downstream as every slide overflowing by the same amount. */
pp-deck {{ display: block; }}
pp-deck:not(:defined) > .slide:not(:first-of-type) {{ display: none; }}
@page {{ size: 1280px 720px; margin: 0; }}
{sheet}
</style>
</head>
<body>
<pp-deck data-slidify-deck width="1280" height="720">
{bodies}
</pp-deck>
<script>
{script}
</script>
</body>
</html>
"""


def _title_of(slides: list[dict], fallback: str) -> str:
    if fallback:
        return fallback
    match = _TITLE.search(slides[0].get("html") or "")
    return match.group(1).strip() if match else "Deck"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Assemble slides into one presentable, convertible deck.html."
    )
    parser.add_argument("slides", help="slides.json, slides/index.json, or a directory")
    parser.add_argument("--out", required=True, help="path to write deck.html")
    parser.add_argument("--title", default="", help="defaults to slide 1's <title>")
    args = parser.parse_args(argv)

    sys.path.insert(0, str(SKILL_DIR.parent / "pixelpitch-slide-craft" / "scripts"))
    from slides_io import load_slides  # noqa: E402

    slides = load_slides(Path(args.slides))
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(build(slides, _title_of(slides, args.title)), encoding="utf-8")

    notes = sum(1 for s in slides if "data-pptx-notes" in (s.get("html") or "") or s.get("notes"))
    print(json.dumps({"deck": str(out), "slides": len(slides), "with_notes": notes}, indent=2))
    if notes < len(slides):
        print(
            f"\n{len(slides) - notes} of {len(slides)} slides carry no speaker notes. "
            "The argument that did not fit on the slide belongs in "
            "data-pptx-notes, which both this stage and the PPTX read.",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
