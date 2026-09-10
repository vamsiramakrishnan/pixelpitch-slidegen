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

"""The three ways staging quietly ruined a deck, plus the contract it keeps.

Each of the first three tests is a bug that shipped a green build and a broken
deck. None of them raised, none of them changed the slide count, and two of them
were invisible until the PPTX came back as flat pictures. They are the reason
this file exists: a stage that fails loudly is easy, a stage that fails silently
costs a whole conversion.
"""

from __future__ import annotations

import pytest
from stage_deck import StageError, build, scope_selector

SECTION = ("section", frozenset({"slide"}))


def slide(body: str, style: str = "", cls: str = "slide") -> dict:
    return {
        "html": f"<!doctype html><html><head><style>{style}</style></head>"
        f'<body><section class="{cls}">{body}</section></body></html>'
    }


def test_a_selector_that_leads_with_slide_becomes_the_slide():
    assert scope_selector(".slide > :not(.ground)", "#s1", SECTION) == "#s1 > :not(.ground)"
    assert scope_selector(".slide .foot", "#s1", SECTION) == "#s1 .foot"
    assert scope_selector("html, body", "#s1", SECTION) == "#s1"
    assert scope_selector(".foot span", "#s1", SECTION) == "#s1 .foot span"


def test_a_second_class_on_the_slide_root_also_names_the_slide():
    """`class="slide slide--split"` means `.slide--split` is the slide itself.

    Scoped as a descendant it matches nothing, the two-column grid it carries
    stops applying, and the deck still builds and still converts. The only
    symptom was one title wrapping differently.
    """
    root = ("section", frozenset({"slide", "slide--split"}))
    assert scope_selector(".slide--split", "#s4", root) == "#s4"
    assert scope_selector(".slide--split h1", "#s4", root) == "#s4 h1"
    assert scope_selector("section.slide:first-child", "#s4", root) == "#s4:first-child"


def test_a_class_the_slide_does_not_carry_stays_a_descendant():
    assert scope_selector(".card", "#s1", SECTION) == "#s1 .card"
    assert scope_selector("h1", "#s1", SECTION) == "#s1 h1"
    assert scope_selector("*", "#s1", SECTION) == "#s1 *"


def test_the_slide_keeps_its_own_size_and_loses_the_documents():
    """`html { height: 720px }` sized the slide; here it would size the deck."""
    html = build([slide("<h1>a</h1>", "html,body{margin:0;height:720px}.slide{height:720px}")], "T")
    assert "height:720px" in html.replace(" ", "")
    assert html.replace(" ", "").count("height:720px") == 1


def test_slides_may_disagree_about_their_own_layout():
    """The first version of this refused here, which had it exactly backwards.

    `slop.lint_deck` fails a deck whose slides all share one skeleton, so a
    stage that demands one skeleton asks for the defect the lint catches.
    """
    html = build(
        [
            slide("<h1>a</h1>", ".slide{display:grid;grid-template-rows:1fr auto}"),
            slide("<h1>b</h1>", ".slide{display:grid;grid-template-columns:520px 1fr}"),
        ],
        "T",
    )
    assert "#s1 {display:grid;grid-template-rows:1fr auto}" in html.replace("\n", "")
    assert "#s2 {display:grid;grid-template-columns:520px 1fr}" in html.replace("\n", "")


def test_two_slides_may_not_disagree_about_a_global_name():
    with pytest.raises(StageError, match="rise"):
        build(
            [
                slide("<h1>a</h1>", "@keyframes rise{to{opacity:1}}"),
                slide("<h1>b</h1>", "@keyframes rise{to{opacity:0}}"),
            ],
            "T",
        )


def test_the_script_carries_no_sequence_that_ends_an_element_early():
    """libxml2 ends an inline script at the first `</`, not at `</script`.

    slidify's splitter parses with libxml2, so one closing tag inside the
    component's own doc comment spilled the rest of the file into the body as
    markup. It converted as a phantom shape on every slide and a second copy of
    every slide's text.
    """
    html = build([slide("<h1>a</h1>")], "T")
    script = html[html.index("<script>") + len("<script>") : html.index("</script>")]
    assert "</" not in script


def test_notes_land_where_both_the_stage_and_the_pptx_read_them():
    html = build([{"html": slide("<h1>a</h1>")["html"], "notes": 'Open on the "number".'}], "T")
    assert 'data-pptx-notes="Open on the &quot;number&quot;."' in html


def test_inline_notes_are_not_written_twice():
    record = {
        "html": '<body><section class="slide" data-pptx-notes="already">x</section></body>',
        "notes": "different",
    }
    html = build([record], "T")
    body = html[html.index("<pp-deck") : html.index("</pp-deck>")]
    assert body.count("data-pptx-notes") == 1
    assert "already" in body


def test_a_slide_without_the_class_refuses_by_name():
    with pytest.raises(StageError, match="slide 2"):
        build([slide("<h1>a</h1>"), {"html": "<body><section>b</section></body>"}], "T")
