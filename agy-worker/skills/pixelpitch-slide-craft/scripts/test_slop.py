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

"""Every slop detector must fire on its own fixture.

A detector that silently passes is worse than no detector, because it reports
"clean" and is believed. These tests exist because the first eyebrow detector
was written with the CSS declarations in the wrong order and passed a slide
containing a textbook `<span class="kicker">` eyebrow.
"""

from __future__ import annotations

import pathlib

import pytest

from slop import (
    RULES,
    layout_signature,
    lint_deck,
    lint_slide,
    lint_slides,
    slop_signal_counts,
)

# A slide that is deliberately plain: no bans, no decoration.
CLEAN_SLIDE = """<!DOCTYPE html><html><head><style>
html,body{margin:0;width:1280px;height:720px;font-family:Inter,sans-serif}
.slide{width:1280px;height:720px;box-sizing:border-box;padding:80px;
background:#FAFAFA;color:#171717}
h1{font-size:72px;line-height:1.05;margin:0;font-weight:800}
p{font-size:20px;color:#4D4D4D;max-width:760px}
.rule{width:56px;height:4px;background:#FF5B4F;margin:28px 0}
</style></head><body><div class="slide">
<h1 data-pptx-role="title">Editable native shapes</h1>
<div class="rule"></div>
<p>Body copy that should survive as real text in PowerPoint.</p>
</div></body></html>"""


@pytest.mark.parametrize("rule", RULES, ids=[r.id for r in RULES])
def test_detector_fires_on_its_fixture(rule):
    """Each rule must detect the violation it claims to detect."""
    count = rule.detect(rule.fixture)
    assert count > rule.budget, (
        f"detector {rule.id!r} did not fire on its own fixture; "
        "it would report slop as clean"
    )


@pytest.mark.parametrize("rule", RULES, ids=[r.id for r in RULES])
def test_detector_quiet_on_clean_slide(rule):
    """No rule may fire on a deliberately plain slide."""
    assert rule.detect(CLEAN_SLIDE) <= rule.budget, (
        f"detector {rule.id!r} false-positived on the clean slide"
    )


def test_clean_slide_has_no_violations():
    assert lint_slide(CLEAN_SLIDE) == []


def test_regression_kicker_eyebrow_is_caught():
    """The exact shape that slipped through the first implementation.

    Declaration order differs from the fixture (letter-spacing precedes
    text-transform) and the element is a class in a <style> block, not an
    inline style.
    """
    html = (
        "<style>.kicker{font-size:13px;font-weight:600;color:#ff385c;"
        "letter-spacing:0.8px;text-transform:uppercase;}</style>"
        '<span class="kicker">Intelligence at Sovereign Scale</span>'
        "<h1>Sovereign inference, on your own metal</h1>"
    )
    issues = lint_slide(html)
    assert any("eyebrow" in i.lower() for i in issues), issues


def test_chrome_bar_label_is_not_an_eyebrow():
    """The same treatment, carrying identity at the frame edge.

    An editorial system that runs the deck name and the slide number along a
    hairline bar uses exactly the type of the eyebrow trope. Refusing it
    refuses the vocabulary rather than the reflex, and the detector's own
    message has always said the tell is the label ABOVE THE HEADING.
    """
    html = (
        "<style>.chrome{display:flex;justify-content:space-between;"
        "border-bottom:1px solid #282826}"
        ".chrome span{font-size:18px;letter-spacing:0.12em;"
        "text-transform:uppercase;color:#505048}</style>"
        '<div class="chrome"><span>02 — Demand</span><span>Northwind Grid</span></div>'
        "<h1>One hundred and twenty hours a year set the capital plan</h1>"
    )
    assert lint_slide(html) == []


def test_absolutely_positioned_micro_label_is_not_an_eyebrow():
    """A label pinned to a corner is chrome wherever the heading happens to be.

    Source order puts it next to the headline; the rendered slide does not.
    """
    html = (
        '<style>.num{position:absolute;top:40px;left:70px;font-size:18px;'
        "letter-spacing:0.1em;text-transform:uppercase}</style>"
        '<span class="num">01 / 03</span><h1>Grid under load</h1>'
    )
    assert lint_slide(html) == []


@pytest.mark.parametrize(
    "copy",
    [
        "Safe option",
        "The bold option",
        "Wildcard: a committed palette",
        "Style B",
        "AI-generated visual",
    ],
)
def test_brief_echoed_onto_the_slide_is_caught(copy):
    """The label the turn was given, rendered as slide copy.

    Asked for one restrained candidate and one adventurous one, a turn puts
    the words it was handed at the top of the slide and the audience reads the
    instructions rather than the deck.
    """
    issues = lint_slide(f"<h1>{copy}</h1>")
    assert any("scaffold" in i.lower() or "placeholder" in i.lower() for i in issues), issues


def test_chrome_bar_typed_on_the_bar_is_not_an_eyebrow():
    """The same bar, with the type declared on the row instead of the spans.

    Both spellings are ordinary. The first version of the exemption only
    handled the one where a descendant rule carried the type, so a deck that
    set it on the container had its frame chrome read as a warm-up label.
    """
    html = (
        "<style>.chrome{display:flex;justify-content:space-between;"
        "font-size:18px;letter-spacing:0.12em;text-transform:uppercase;"
        "border-bottom:1px solid #282826}</style>"
        '<div class="chrome"><span>02 — Demand</span><span>Northwind Grid</span></div>'
        "<h2>One hundred and twenty hours a year set the capital plan</h2>"
    )
    assert lint_slide(html) == []


def test_font_size_through_a_custom_property_is_resolved():
    """`font-size: var(--axis)` is a size, not the absence of one.

    Every deck built on assets/slide-base.css declares type through tokens.
    Read literally, a 24px structural label scored as small type and tripped
    the eyebrow rule that exists to catch 12px warm-ups.
    """
    html = (
        "<style>:root{--axis:24px}"
        ".axis{font-size:var(--axis);letter-spacing:0.08em;text-transform:uppercase}"
        "</style>"
        '<p class="axis">Build for the peak</p><h2>Copper answers the hour</h2>'
    )
    assert lint_slide(html) == []

    small = html.replace("--axis:24px", "--axis:13px")
    assert any("eyebrow" in i.lower() for i in lint_slide(small)), small


def test_lint_slides_indexes_by_slide():
    slides = [
        {"html": CLEAN_SLIDE},
        {"html": '<div style="border-radius:40px">x</div>'},
    ]
    found = lint_slides(slides)
    assert 0 not in found
    assert 1 in found


def test_signal_counts_aggregate_across_deck():
    slides = [
        {"html": '<div style="backdrop-filter:blur(4px)">a</div>'},
        {"html": '<div style="backdrop-filter:blur(8px)">b</div>'},
    ]
    assert slop_signal_counts(slides).get("glassmorphism") == 2


def test_hairline_border_is_not_a_side_stripe():
    """1px rules are legitimate; only thick coloured stripes are the tell."""
    assert lint_slide('<div style="border-left:1px solid #eee">x</div>') == []


def test_three_up_card_row_is_not_a_grid_wall():
    """A rhetorical triad is legitimate. Four is where it becomes a wall."""
    three = "".join('<div class="card">x</div>' for _ in range(3))
    assert lint_slide(f"<body>{three}</body>") == []


def test_four_identical_cards_trip_the_grid_wall():
    four = "".join('<div class="card">x</div>' for _ in range(4))
    issues = lint_slide(f"<body>{four}</body>")
    assert any("card grid" in i.lower() for i in issues), issues


def test_anti_example_trips_every_rule():
    """The negative control has to stay complete as rules are added.

    It drifted once: two detectors were written and never registered, so the
    example and its README both described a ten-rule set that had grown to
    twelve. A count in prose cannot notice that. This can.
    """
    html = (
        pathlib.Path(__file__).resolve().parent.parent
        / "examples"
        / "slop-anti-example.html"
    ).read_text(encoding="utf-8")
    silent = [rule.id for rule in RULES if rule.detect(html) <= rule.budget]
    assert not silent, f"slop-anti-example.html no longer trips: {silent}"


def test_every_detector_is_named_in_the_manifesto():
    """A rule the reader cannot find is a rule they cannot design around.

    The lint tells an author what tripped; only the manifesto tells them what to
    write instead. A detector added without its paragraph produces a defect
    message pointing at a page that does not mention it.
    """
    page = (
        pathlib.Path(__file__).resolve().parent.parent / "references" / "anti-slop.md"
    ).read_text(encoding="utf-8")
    missing = sorted(rule.id for rule in RULES if rule.id not in page)
    assert not missing, f"detectors with no entry in anti-slop.md: {missing}"


def test_every_gallery_exemplar_is_clean():
    """The positive half of the same pairing.

    Guidance that says 'do this' while the exemplar trips a gate teaches the
    gate is optional.
    """
    gallery = pathlib.Path(__file__).resolve().parent.parent / "examples"
    dirty = {
        path.name: lint_slide(path.read_text(encoding="utf-8"))
        for path in sorted(gallery.glob("*.html"))
        if path.name != "slop-anti-example.html"
    }
    assert not {name: issues for name, issues in dirty.items() if issues}


def _skeleton(body: str) -> str:
    return (
        "<style>.two{display:grid;grid-template-columns:1fr 1fr}</style>"
        f'<section class="slide"><h1>t</h1><div class="two">{body}</div></section>'
    )


def test_three_consecutive_identical_skeletons_are_flagged():
    deck = [{"html": _skeleton(f"<p>{n}</p>")} for n in "abc"]
    issues = lint_deck(deck)
    assert any("layout monotony" in i.lower() for i in issues), issues


def test_two_in_a_row_is_a_rhyme_not_monotony():
    deck = [
        {"html": _skeleton("<p>a</p>")},
        {"html": _skeleton("<p>b</p>")},
        {"html": "<section class='slide'><h1>t</h1><table><tr><td>x</td></tr></table></section>"},
    ]
    assert lint_deck(deck) == []


def test_one_skeleton_owning_most_of_a_deck_is_flagged():
    deck = [
        {"html": _skeleton("<p>a</p>")},
        {"html": "<section class='slide'><h1>t</h1><svg><text>x</text></svg></section>"},
        {"html": _skeleton("<p>b</p>")},
        {"html": _skeleton("<p>c</p>")},
        {"html": _skeleton("<p>d</p>")},
    ]
    issues = lint_deck(deck)
    assert any("single skeleton" in i.lower() for i in issues), issues


def test_signature_ignores_content_and_notices_structure():
    same = layout_signature(_skeleton("<p>alpha</p>"))
    reworded = layout_signature(_skeleton("<p>a completely different sentence</p>"))
    restructured = layout_signature(
        "<style>.two{display:grid;grid-template-columns:2fr 1fr}</style>"
        '<section class="slide"><h1>t</h1><div class="two"><p>alpha</p></div></section>'
    )
    assert same == reworded
    assert same != restructured


def test_the_gallery_decks_vary():
    """The three dry-run decks the exemplars came from must pass their own rule."""
    gallery = pathlib.Path(__file__).resolve().parent.parent / "examples"
    slides = [
        {"html": p.read_text(encoding="utf-8")}
        for p in sorted(gallery.glob("*.html"))
        if p.name != "slop-anti-example.html"
    ]
    assert lint_deck(slides) == []
