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

"""Deterministic detectors for the mechanically-detectable anti-slop bans.

Every rule here corresponds to an "absolute ban" in
``../references/anti-slop.md`` and carries a ``fixture``: a minimal snippet
that MUST trigger it. ``test_slop.py`` beside this file asserts each detector
fires on its own fixture and stays quiet on a clean slide, and
``../examples/slop-anti-example.html`` is the whole set in one document.

That pairing is not ceremony. The first version of the eyebrow detector was
written with the CSS declarations in the wrong order and silently passed a slide
containing a textbook ``<span class="kicker">`` eyebrow. A detector without a
fixture is worse than no detector: it reports "clean" and is believed.

Detectors are intentionally conservative. A false positive costs a wasted
revision pass on a slide that was fine; a false negative ships slop.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass

# A style rule body, e.g. the text between { and }.
_RULE_BLOCK = re.compile(r"\{[^{}]*\}")
_PX = r"(\d+(?:\.\d+)?)px"

_STYLE_BLOCK = re.compile(r"<style\b[^>]*>.*?</style>", re.I | re.S)
_SCRIPT_BLOCK = re.compile(r"<script\b[^>]*>.*?</script>", re.I | re.S)
_SELECTOR_RULE = re.compile(r"([^{}]+)\{([^{}]*)\}")
_CLASS_IN_SELECTOR = re.compile(r"\.([A-Za-z_][\w-]*)")
_CLASS_ATTR = re.compile(r'\bclass="([^"]*)"', re.I)
_STYLE_ATTR = re.compile(r'\bstyle="([^"]*)"', re.I)
# What must come next for a micro-label to be an eyebrow rather than chrome.
_HEADING_NEXT = re.compile(
    r"\s*(?:<!--.*?-->\s*)*<(?:h1|h2)\b"
    r"|\s*(?:<!--.*?-->\s*)*<[a-z][^>]*data-pptx-role\s*=\s*[\"']title[\"']",
    re.I | re.S,
)


def _decl(block: str, prop: str) -> str | None:
    """Value of ``prop`` inside one CSS rule block, if present."""
    m = re.search(rf"(?<![\w-]){re.escape(prop)}\s*:\s*([^;}}]+)", block, re.I)
    return m.group(1).strip() if m else None


def _blocks(html: str) -> list[str]:
    """All CSS rule bodies plus every inline style attribute."""
    out = [m.group() for m in _RULE_BLOCK.finditer(html)]
    out += [f"{{{m.group(1)}}}" for m in re.finditer(r'style="([^"]*)"', html, re.I)]
    return out


def _props(body: str) -> dict[str, str]:
    """Declarations in one rule body or inline style, as a property map."""
    out: dict[str, str] = {}
    for part in body.split(";"):
        name, sep, value = part.partition(":")
        name = name.strip().lower()
        if sep and name:
            out[name] = value.strip()
    return out


# Where the custom-property map is filed in the class index. No class token can
# collide with it, because a class name cannot begin with a colon.
_VARS_KEY = ":root"
_VAR_REF = re.compile(r"var\(\s*(--[\w-]+)\s*(?:,\s*([^()]*))?\)")


def _resolve(value: str, variables: dict[str, str]) -> str:
    """Substitute ``var(--x)`` with what ``--x`` was declared as.

    Every size detector reads a literal `px`, and every deck built on
    `assets/slide-base.css` writes `font-size: var(--label)`. Unresolved, that
    reads as no size at all, so a 24px structural label was scored as small
    type and a hero figure declared through a token was not counted. One pass
    is enough; a token defined in terms of another is not a shape worth
    supporting here.
    """
    def swap(m: re.Match[str]) -> str:
        return variables.get(m.group(1), (m.group(2) or "").strip())

    return _VAR_REF.sub(swap, value) if "var(" in value else value


def _class_styles(html: str) -> dict[str, dict[str, str]]:
    """Declared properties per class token, merged across every rule naming it.

    Approximate on purpose. A detector that needs to know whether ``.card`` is
    a filled box does not need cascade resolution, it needs to know that
    somebody declared a background on it somewhere.
    """
    index: dict[str, dict[str, str]] = {}
    variables: dict[str, str] = {}
    for block in _STYLE_BLOCK.findall(html):
        for _, body in _SELECTOR_RULE.findall(block):
            variables.update(
                {k: v for k, v in _props(body).items() if k.startswith("--")}
            )
    index[_VARS_KEY] = variables
    for block in _STYLE_BLOCK.findall(html):
        for selector, body in _SELECTOR_RULE.findall(block):
            props = {k: _resolve(v, variables) for k, v in _props(body).items()}
            if not props:
                continue
            for one in selector.split(","):
                # Only the subject of the selector gets the declarations. A
                # descendant rule like `.chrome span` styles the span, and
                # crediting `.chrome` with the span's type made a flex row
                # holding two labels look like a label itself.
                subject = re.split(r"[\s>+~]+", one.strip())[-1]
                for token in set(_CLASS_IN_SELECTOR.findall(subject)):
                    index.setdefault(token, {}).update(props)
    return index


def _declared(attrs: str, styles: dict[str, dict[str, str]]) -> dict[str, str]:
    """Effective properties on one element, from its classes then its inline style."""
    out: dict[str, str] = {}
    classes = _CLASS_ATTR.search(attrs)
    for token in (classes.group(1).split() if classes else []):
        out.update(styles.get(token, {}))
    inline = _STYLE_ATTR.search(attrs)
    if inline:
        variables = styles.get(_VARS_KEY, {})
        out.update({k: _resolve(v, variables) for k, v in _props(inline.group(1)).items()})
    return out


def _visible_text(html: str) -> str:
    """Text a viewer would read, with stylesheet and script bodies removed."""
    stripped = _SCRIPT_BLOCK.sub(" ", _STYLE_BLOCK.sub(" ", html))
    return re.sub(r"<[^>]+>", " ", stripped)


# --------------------------------------------------------------------------
# Detectors
# --------------------------------------------------------------------------


def _side_stripe(html: str) -> int:
    hits = 0
    for b in _blocks(html):
        for side in ("border-left", "border-right"):
            value = _decl(b, side)
            if not value:
                continue
            m = re.search(_PX, value)
            # A hairline rule is legitimate; a thick coloured stripe is the tell.
            if m and float(m.group(1)) > 1 and not re.search(r"\bnone\b", value, re.I):
                hits += 1
    return hits


def _gradient_text(html: str) -> int:
    return len(
        re.findall(r"(?:-webkit-)?background-clip\s*:\s*text", html, re.I)
    )


def _glassmorphism(html: str) -> int:
    return len(re.findall(r"backdrop-filter\s*:\s*[^;}]*blur", html, re.I))


def _over_rounded(html: str) -> int:
    hits = 0
    for b in _blocks(html):
        value = _decl(b, "border-radius")
        if not value or "%" in value:
            continue
        # Pills (very large radius on a short element) are fine; the tell is a
        # card-scale radius. 32px is the manifesto's threshold.
        for m in re.finditer(_PX, value):
            if float(m.group(1)) >= 32:
                hits += 1
                break
    return hits


def _ghost_card(html: str) -> int:
    """1px border AND a wide soft shadow on the same element."""
    hits = 0
    for b in _blocks(html):
        border = _decl(b, "border")
        shadow = _decl(b, "box-shadow")
        if not border or not shadow:
            continue
        if not re.search(r"\b1px\b", border):
            continue
        blurs = [float(x) for x in re.findall(_PX, shadow)]
        # offset-x, offset-y, blur... take the largest as a proxy for softness.
        if blurs and max(blurs) >= 16:
            hits += 1
    return hits


def _stripe_background(html: str) -> int:
    return len(re.findall(r"repeating-linear-gradient", html, re.I))


def _sketchy_svg(html: str) -> int:
    return len(
        re.findall(
            r"feTurbulence|feDisplacementMap|class=\"[^\"]*(?:sketch|doodle|wavy|scribble)",
            html,
            re.I,
        )
    )


def _tracked_micro_label(props: dict[str, str]) -> bool:
    """Small, uppercase, letter-spaced: the type treatment, without the verdict."""
    if "uppercase" not in props.get("text-transform", "").lower():
        return False
    if "letter-spacing" not in props:
        return False
    size = props.get("font-size", "")
    m = re.search(_PX, size)
    # The tell is small type. Large uppercase display is a choice.
    return not (m and float(m.group(1)) > 20)


def _element_end(html: str, tag: str, start: int) -> int:
    """Index just past the element that opens at ``start``, or -1."""
    depth = 0
    for m in re.finditer(rf"<(/?){re.escape(tag)}\b[^>]*>", html[start:], re.I):
        if m.group(1):
            depth -= 1
            if depth == 0:
                return start + m.end()
        else:
            depth += 1
    return -1


def _eyebrow(html: str) -> int:
    """A tracked micro-label sitting immediately on top of a heading.

    The tell is the position, not the type treatment. The same small uppercase
    tracked label pinned to a frame edge, or riding in a hairline chrome bar
    with the deck name and the slide number, is how editorial systems carry
    identity, and refusing it refuses the vocabulary rather than the reflex.
    What reads as machine-made is the content-free warm-up parked directly
    above the headline, which is what this counts.

    The first version counted CSS rule blocks and so fired on any deck that
    used the treatment anywhere, including a deliberate one. The message it
    printed already said "above the heading"; the detector never checked.
    """
    styles = _class_styles(html)
    hits = 0
    for m in re.finditer(r"<(span|p|div|small|em|strong|h6)\b([^>]*)>", html, re.I):
        props = _declared(m.group(2), styles)
        if not _tracked_micro_label(props):
            continue
        if props.get("position", "").lower() in {"absolute", "fixed"}:
            continue
        end = _element_end(html, m.group(1), m.start())
        if end < 0 or not _HEADING_NEXT.match(html, end):
            continue
        # A label's content is a phrase. An element holding its own child
        # elements is a bar carrying two of them, one at each edge, and the
        # type was declared on the bar rather than on the spans inside it.
        if re.search(r"<[a-z]", html[m.end() : end], re.I):
            continue
        hits += 1
    return hits


def _numbered_scaffold(html: str) -> int:
    """`01 \u00b7 Word` markers. The trope is the rail of them, not one of them.

    A single marker on a slide is almost always the slide's own number sitting
    in chrome, which is a real ordered sequence and the one case the ban has
    always exempted. Two or more is the landing-page rail, so the rule budgets
    one rather than banning the shape.
    """
    text = re.sub(r"<[^>]+>", " ", html)
    # Separators: middot, em dash, en dash, hyphen, slash.
    seps = "\u00b7\u2014\u2013\\-/"
    return len(re.findall(rf"\b0[1-9]\s*[{seps}]\s*[A-Z]", text))


_BLOCK_TAG = re.compile(r"<(?:div|section|article|li)\b([^>]*)>", re.I)
_BOX_PROPS = ("background", "background-color", "border", "border-radius", "box-shadow")
_CARD_WORDS = re.compile(r"card|tile|pillar|feature|benefit|panel|box", re.I)
_CARD_WALL = 4


def _card_grid(html: str) -> int:
    """A wall of repeated boxes, however the repetition happens to be spelled.

    The first version keyed on an identical whole class string at five or more
    elements, which caught only the naive case. ``card card--a`` through
    ``card card--e`` evaded it, class-less inline-styled boxes evaded it, and a
    four-up icon wall was under threshold. So count by shared class TOKEN and
    by inline-style signature, and start at four, where a rhetorical triad
    becomes a grid.

    An element only counts when it reads as a box (a fill, a border, a radius,
    a shadow) or is named like one. The remedy this rule recommends is a table
    or a ledger list of hairline rows; without that qualifier the detector
    would fire on its own fix.
    """
    styles = _class_styles(html)
    by_token: dict[str, int] = {}
    by_inline: dict[str, int] = {}
    for match in _BLOCK_TAG.finditer(html):
        attrs = match.group(1)
        classes = _CLASS_ATTR.search(attrs)
        tokens = classes.group(1).split() if classes else []
        declared = _declared(attrs, styles)
        boxed = any(prop in declared for prop in _BOX_PROPS)
        if not (boxed or any(_CARD_WORDS.search(token) for token in tokens)):
            continue
        for token in set(tokens):
            by_token[token] = by_token.get(token, 0) + 1
        inline = _STYLE_ATTR.search(attrs)
        if not tokens and inline:
            signature = ";".join(sorted(_props(inline.group(1))))
            by_inline[signature] = by_inline.get(signature, 0) + 1
    widest = max([*by_token.values(), *by_inline.values(), 0])
    return 1 if widest >= _CARD_WALL else 0


_LEAF_TAG = re.compile(r"<(div|span|p|strong|b|h[1-6])\b([^>]*)>([^<]*)</\1>", re.I)
_FIGURE = re.compile(r"^[+\-]?[$€£¥]?\s?\d[\d,.\s]*\s?(?:%|x|×|k|m|b|bn|pp)?\+?$", re.I)
_HERO_FIGURE_PX = 56.0
_HERO_STATS = 3


def _hero_metric(html: str) -> int:
    """Big number, small label, supporting stats. The SaaS hero-metric template.

    One oversized figure is a legitimate archetype; the roundtrip catalog lists
    "hero stat" by name. Three of them across one slide is the cliche the
    manifesto bans, so the threshold sits where "supporting stats" begins.
    """
    styles = _class_styles(html)
    figures = 0
    for match in _LEAF_TAG.finditer(html):
        text = match.group(3).strip()
        if not text or not _FIGURE.match(text):
            continue
        size = _declared(match.group(2), styles).get("font-size")
        if not size:
            continue
        m = re.search(_PX, size)
        if m and float(m.group(1)) >= _HERO_FIGURE_PX:
            figures += 1
    return 1 if figures >= _HERO_STATS else 0


# Scaffold copy that belongs to the authoring process, never to the audience.
# The list is the union of PowerPoint's own placeholder strings, the seam
# baselines' filler ("Headline 90pt", "Chart area"), and the preview-
# authenticity rules vendored from zarazhangrui/frontend-slides (MIT).
_PLACEHOLDER = re.compile(
    r"lorem ipsum"
    r"|click to (?:edit|add)"
    r"|master (?:title|text) style"
    r"|\bplaceholder\b"
    r"|your (?:title|text|content|headline|logo|copy) here"
    r"|\[\s*insert\b"
    r"|\{\{[^}]*\}\}"
    r"|\bsample (?:content|text|copy|slide|title|headline)\b"
    r"|\boption [a-c]\b"
    r"|\bheadline \d+\s*pt\b"
    r"|\bchart area\b"
    r"|\bstyle option\b"
    r"|\bslide-\d+[-a-z]*\.html\b"
    r"|\b(?:brand-contract|authoring-plan|catalog|slides)\.json\b"
    r"|\b(?:preview|design)\.md\b"
    # The brief echoed back onto the slide. When a turn is asked for a safe
    # option and a bold one, the label it was given is the first thing it
    # reaches for as a headline, and the audience is shown the instructions
    # instead of the deck. None of these has a legitimate use in slide copy.
    r"|\b(?:safe|bold|conservative|expressive) option\b"
    r"|\bwildcard\b"
    r"|\bstyle [a-c]\b"
    r"|\bai[- ]generated\b",
    re.I,
)


def _placeholder_text(html: str) -> int:
    """Authoring scaffold that leaked into what the audience reads."""
    return len(_PLACEHOLDER.findall(_visible_text(html)))


_TITLE_EL = re.compile(
    r"<(\w+)[^>]*\bdata-pptx-role\s*=\s*[\"']title[\"'][^>]*>(.*?)</\1>"
    r"|<(h1|h2)\b[^>]*>(.*?)</\3>",
    re.I | re.S,
)
# Each of these is the speaker's punchline wearing a title's clothes. They are
# listed as whole shapes rather than as banned words because the tell is the
# rhetorical move, not the vocabulary: a title that withholds, reverses, or
# lands a verdict is doing the speaking the speaker is there to do.
_PUNCHLINE = re.compile(
    r"\bit'?s not\b[^.?!]*[.?!]\s*it'?s\b"          # It's not X. It's Y.
    r"|\bthis is(?:n'?t| not)\b[^.?!]*[.?!]\s*it'?s\b"
    r"|^the (?:magic|real|hidden|untold|uncomfortable|inconvenient|surprising)\b"
    r"|^(?:here'?s|here is) (?:the|why|what|how)\b"
    r"|^enter\s+\S"
    r"|\bthe secret\b|\bthe truth about\b|\bthe one thing\b"
    r"|\bwhat (?:nobody|no one|everyone)\b"
    r"|\bchanges everything\b|\bis dead\b"
    r"|\breimagined\b|\brethinking\b|\bunlocking\b",
    re.I,
)


def _punchline_title(html: str) -> int:
    """A title written as the line the speaker delivers, not as a title."""
    hits = 0
    for match in _TITLE_EL.finditer(html):
        text = " ".join(_visible_text(match.group(2) or match.group(4) or "").split())
        if text and _PUNCHLINE.search(text):
            hits += 1
    return hits


@dataclass(frozen=True)
class SlopRule:
    id: str
    detect: Callable[[str], int]
    message: str
    fixture: str
    # Allowed occurrences per slide before it counts as a violation.
    budget: int = 0


RULES: tuple[SlopRule, ...] = (
    SlopRule(
        id="side_stripe",
        detect=_side_stripe,
        message=(
            "SLOP (side-stripe border): a coloured left/right border thicker "
            "than 1px used as an accent. Rewrite with a full border, a "
            "background tint, a leading number, or nothing."
        ),
        fixture='<div style="border-left:4px solid #E85D5D">x</div>',
    ),
    SlopRule(
        id="gradient_text",
        detect=_gradient_text,
        message=(
            "SLOP (gradient text): background-clip:text over a gradient is "
            "decorative, never meaningful — even though it converts natively. "
            "Use one solid colour; carry emphasis with weight or size."
        ),
        fixture="<style>h1{-webkit-background-clip:text;color:transparent}</style>",
    ),
    SlopRule(
        id="glassmorphism",
        detect=_glassmorphism,
        message=(
            "SLOP (glassmorphism): decorative backdrop blur. Remove it, or "
            "make it purposeful and rare."
        ),
        fixture='<div style="backdrop-filter:blur(12px)">x</div>',
    ),
    SlopRule(
        id="over_rounded",
        detect=_over_rounded,
        message=(
            "SLOP (over-rounded): border-radius of 32px or more on a card or "
            "panel. Cards top out at 12-16px; full pills are for tags only."
        ),
        fixture='<div style="border-radius:40px">x</div>',
    ),
    SlopRule(
        id="ghost_card",
        detect=_ghost_card,
        message=(
            "SLOP (ghost card): a 1px border paired with a wide soft shadow. "
            "Pick one — a single defined border, or a shadow at 8px blur max."
        ),
        fixture=(
            '<div style="border:1px solid #ddd;box-shadow:0 8px 24px rgba(0,0,0,.1)">'
            "x</div>"
        ),
    ),
    SlopRule(
        id="stripe_background",
        detect=_stripe_background,
        message=(
            "SLOP (stripe background): repeating-linear-gradient stripes are "
            "pure decoration. Native-safe is not permission — remove them."
        ),
        fixture="<style>body{background:repeating-linear-gradient(45deg,#000,#fff)}</style>",
    ),
    SlopRule(
        id="sketchy_svg",
        detect=_sketchy_svg,
        message=(
            "SLOP (sketchy illustration): hand-drawn/doodle SVG or grain "
            "filters read as amateurish. Ship no illustration instead."
        ),
        fixture='<svg class="loose-sketch"><path d="M0 0"/></svg>',
    ),
    SlopRule(
        id="eyebrow",
        detect=_eyebrow,
        message=(
            "SLOP (eyebrow): a small uppercase letter-spaced label above the "
            "heading. This is the most saturated AI tell there is. The layout "
            "budget reserves the space — use it for something else, or start "
            "with the headline. The same label pinned to a frame edge or in a "
            "chrome bar is structure, not slop, and is not counted."
        ),
        fixture=(
            '<span style="font-size:12px;text-transform:uppercase;'
            'letter-spacing:.14em">About</span><h1>Our platform</h1>'
        ),
    ),
    SlopRule(
        id="numbered_scaffold",
        detect=_numbered_scaffold,
        # One marker is the slide's own number. A rail of them is the trope.
        budget=1,
        message=(
            "SLOP (numbered scaffolding): 01 / 02 / 03 markers used as section "
            "furniture. Numbers earn their place only when the content really "
            "is an ordered sequence."
        ),
        fixture="<div>01 · About</div><div>02 · Process</div><div>03 · Pricing</div>",
    ),
    SlopRule(
        id="card_grid",
        detect=_card_grid,
        message=(
            "SLOP (identical card grid): four or more same-sized cards in one "
            "slide. Vary the layout, collapse to a table or a ledger of hairline "
            "rows, or show fewer, stronger items — one idea per slide, not a "
            "filler grid."
        ),
        fixture=(
            '<div class="card">a</div><div class="card">b</div>'
            '<div class="card">c</div><div class="card">d</div>'
            '<div class="card">e</div>'
        ),
    ),
    SlopRule(
        id="hero_metric",
        detect=_hero_metric,
        message=(
            "SLOP (hero-metric template): three or more oversized figures with "
            "small labels under them. One big number is an archetype; three is "
            "a SaaS landing page. Keep the figure that is the argument and put "
            "the rest in a sentence or a table."
        ),
        fixture=(
            '<div style="font-size:72px">94%</div>'
            '<div style="font-size:72px">3.2x</div>'
            '<div style="font-size:72px">$1.4M</div>'
        ),
    ),
    SlopRule(
        id="placeholder_text",
        detect=_placeholder_text,
        message=(
            "SLOP (placeholder text): authoring scaffold left in what the "
            "audience reads. Replace it with the real content, or delete the "
            "element. Shipping a filename or a template string is the one "
            "defect an audience always notices."
        ),
        fixture="<h1>Click to edit Master title style</h1>",
    ),
    SlopRule(
        id="punchline_title",
        detect=_punchline_title,
        message=(
            "SLOP (punchline title): the title is the line the speaker "
            "delivers, not a title. Withholding, reversal, and verdict "
            "constructions read as AI copy and steal the speaker's move. "
            "Titles orient, like chapter headings: say what the slide is "
            "about, then let the speaker land the point."
        ),
        fixture=(
            '<h1 data-pptx-role="title">It\'s not a cost problem. '
            "It's a timing problem.</h1>"
        ),
    ),
)


def lint_slide(html: str) -> list[str]:
    """Slop violations for one slide, as revision-ready messages."""
    out = []
    for rule in RULES:
        try:
            count = rule.detect(html or "")
        except Exception:  # a broken detector must never break a render
            continue
        if count > rule.budget:
            suffix = f" (found {count})" if count > 1 else ""
            out.append(rule.message + suffix)
    return out


def lint_slides(slides: list[dict]) -> dict[int, list[str]]:
    """Slop violations per slide index."""
    found: dict[int, list[str]] = {}
    for i, slide in enumerate(slides):
        issues = lint_slide(slide.get("html", ""))
        if issues:
            found[i] = issues
    return found


# --------------------------------------------------------------------------
# Deck-level rules
# --------------------------------------------------------------------------

# Every rule above reads one slide. The commonest slop in a finished deck is
# invisible at that scale: twelve slides that are each defensible and all the
# same. That is why the renderer writes a contact sheet, and this is the
# mechanical half of the same check.

_SKELETON = re.compile(
    r"<(section|div|main|article|table|svg|ul|ol|h1|h2|p)\b([^>]*)>", re.I
)
_STRUCTURAL = (
    "display", "grid-template-columns", "grid-template-rows", "flex-direction",
)
_RUN_LIMIT = 3
_SHARE_LIMIT = 0.6
_SHARE_MIN_SLIDES = 4


def layout_signature(html: str) -> str:
    """A coarse fingerprint of a slide's skeleton, blind to its content.

    Tag sequence plus the structural CSS on the classes that carry it. Two
    slides sharing a signature are built the same way, whatever words are in
    them. Deliberately coarse: the goal is to notice a template being reused,
    not to distinguish two honest variations.
    """
    styles = _class_styles(html)
    parts: list[str] = []
    for match in _SKELETON.finditer(html):
        tag = match.group(1).lower()
        declared = _declared(match.group(2), styles)
        shape = ",".join(
            f"{prop}={declared[prop]}" for prop in _STRUCTURAL if prop in declared
        )
        parts.append(f"{tag}[{shape}]" if shape else tag)
    return "|".join(parts)


def lint_deck(slides: list[dict]) -> list[str]:
    """Defects that only exist across slides. Empty when the deck varies."""
    signatures = [layout_signature(s.get("html", "")) for s in slides]
    out: list[str] = []

    run = 1
    for index in range(1, len(signatures)):
        run = run + 1 if signatures[index] == signatures[index - 1] else 1
        if run == _RUN_LIMIT:
            out.append(
                f"SLOP (layout monotony): slides {index - 1}-{index + 1} share one "
                "skeleton. Three in a row reads as a template, not a deck. Give "
                "one of them a different exhibit."
            )

    if len(signatures) >= _SHARE_MIN_SLIDES:
        for signature in set(signatures):
            share = signatures.count(signature) / len(signatures)
            if share > _SHARE_LIMIT:
                where = [i + 1 for i, s in enumerate(signatures) if s == signature]
                out.append(
                    f"SLOP (single skeleton): {len(where)} of {len(signatures)} "
                    f"slides are built the same way (slides {where}). A deck "
                    "with one layout is a form. Vary the exhibit to the claim."
                )
    return out


def slop_signal_counts(slides: list[dict]) -> dict[str, int]:
    """Per-rule totals across a deck, for tracking whether tells trend down."""
    totals: dict[str, int] = {}
    for slide in slides:
        html = slide.get("html", "")
        for rule in RULES:
            try:
                count = rule.detect(html)
            except Exception:
                continue
            if count:
                totals[rule.id] = totals.get(rule.id, 0) + count
    return totals
