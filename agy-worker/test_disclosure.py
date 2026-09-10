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

"""The lint's own failure mode is the expensive one.

An orphan it misses costs a page nobody reads. An orphan it invents costs an
author deleting or re-linking a page that was fine, and after the second false
one nobody runs it again. The first version of this walk reported fifty-seven
orphans across four skills; every one was a bug in the walk. So most of what is
here is the reachable direction.
"""

from __future__ import annotations

import json

import pytest
from disclosure import DisclosureLog, Skill, disclosure_findings, skill_cards

FRONT = (
    "---\nname: {name}\ndescription: {description}\n---\n\n# {name}\n\n{body}\n"
)

# Long enough to clear MIN_DESCRIPTION, so a fixture skill fails only on the
# thing under test.
GOOD_DESCRIPTION = (
    "Use when a slide has to sit on an extracted PowerPoint template and the "
    "plate underneath it came from precompute rather than from an authoring "
    "turn, or when a deck check reports a plate that is not one of the "
    "template's own."
)


@pytest.fixture
def skill(tmp_path):
    """One skill on disk, written the way the harness will read it."""

    def build(body: str = "", files: dict[str, str] | None = None, **front):
        root = tmp_path / front.pop("dirname", "pixelpitch-example")
        root.mkdir()
        (root / "SKILL.md").write_text(
            FRONT.format(
                name=front.get("name", "pixelpitch-example"),
                description=front.get("description", GOOD_DESCRIPTION),
                body=body,
            )
        )
        for relative, text in (files or {}).items():
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
        return tmp_path

    return build


def only(paths) -> Skill:
    cards = skill_cards([str(paths)])
    assert len(cards) == 1
    return cards[0]


def orphans(paths) -> set[str]:
    card = only(paths)
    return {str(path.relative_to(card.root)) for path in card.unreachable}


# --------------------------------------------------------------------------
# the frontmatter


def test_the_frontmatter_is_what_the_harness_reads(skill):
    card = only(skill(name="pixelpitch-deck"))
    assert card.name == "pixelpitch-deck"
    assert card.description == GOOD_DESCRIPTION


def test_a_description_that_only_summarises_is_a_finding(skill):
    """The description is the retrieval index and nothing else. `Tools for
    working with decks` names no situation, so no situation retrieves it."""
    root = skill(description="Tools for working with decks.")
    assert any("characters" in problem for problem in disclosure_findings([str(root)]))


def test_a_missing_description_is_a_harder_finding(skill):
    root = skill(description="")
    problem = disclosure_findings([str(root)])[0]
    assert "no description" in problem


def test_a_description_naming_its_triggers_passes(skill):
    assert disclosure_findings([str(skill())]) == []


# --------------------------------------------------------------------------
# reachability


def test_skill_md_is_never_its_own_orphan(skill):
    """It was, for one afternoon: reachability accumulated unresolved paths
    and the directory scan resolved them, so the entry point subtracted from
    nothing and every skill reported `reachable 1, orphaned everything`."""
    assert orphans(skill()) == set()


def test_a_linked_reference_is_reachable(skill):
    root = skill(
        body="The seam rules are in [references/seams.md](references/seams.md).",
        files={"references/seams.md": "# Seams\n"},
    )
    assert orphans(root) == set()


def test_reachability_is_transitive(skill):
    """The shape these skills are actually written in: SKILL.md points at an
    index page, and the index page points at the detail."""
    root = skill(
        body="Start at [references/index.md](references/index.md).",
        files={
            "references/index.md": "Then [seams](seams.md) and [type](type.md).",
            "references/seams.md": "# Seams\n",
            "references/type.md": "# Type\n",
        },
    )
    assert orphans(root) == set()


def test_a_page_nothing_points_at_is_an_orphan(skill):
    """The defect. `references/slidify-hints.md` was exactly this: maintained,
    covered by a test, verified against the converter, and unreachable."""
    root = skill(
        body="The seam rules are in [references/seams.md](references/seams.md).",
        files={"references/seams.md": "# Seams\n", "references/hints.md": "# Hints\n"},
    )
    assert orphans(root) == {"references/hints.md"}


def test_a_bare_relative_path_in_prose_counts(skill):
    """Agent-facing prose links by naming the file, not by writing markdown."""
    root = skill(
        body="Run the lint against `examples/` and read examples/README.md.",
        files={"examples/README.md": "# Gallery\n"},
    )
    assert orphans(root) == set()


def test_a_path_that_climbs_out_of_references_counts(skill):
    """`../examples/slop-anti-example.html`, written from a reference page.
    The first pattern anchored on `examples/` and the leading `../` blocked
    the lookbehind, which is how a whole gallery read as dead."""
    root = skill(
        body="See [the anti-slop rules](references/anti-slop.md).",
        files={
            "references/anti-slop.md": "Proved by `../examples/bad.html`.",
            "examples/bad.html": "<html></html>",
        },
    )
    assert orphans(root) == set()


def test_a_manifest_a_script_serves_is_a_disclosure_channel(skill):
    """The gallery's README says `do not read this directory, query it`, and
    the query goes through index.json. Sixteen exemplars are reachable through
    a manifest and a script with no prose link to any of them."""
    root = skill(
        body="Query the gallery: [examples/README.md](examples/README.md).",
        files={
            "examples/README.md": "Metadata in [index.json](index.json).",
            "examples/index.json": json.dumps(
                {"exhibits": [{"id": "hero", "html": "hero.html"}]}
            ),
            "examples/hero.html": "<html></html>",
        },
    )
    assert orphans(root) == set()


def test_a_manifest_cannot_talk_a_missing_page_into_existing(skill):
    """Only strings that name a file that is there count. Otherwise prose in a
    manifest would grant reach the same way prose in a page does not."""
    root = skill(
        body="Query the gallery: [examples/README.md](examples/README.md).",
        files={
            "examples/README.md": "Metadata in [index.json](index.json).",
            "examples/index.json": json.dumps({"about": "sixteen slides"}),
            "examples/orphan.html": "<html></html>",
        },
    )
    assert orphans(root) == {"examples/orphan.html"}


def test_a_link_out_of_the_skill_is_not_this_skills_business(skill):
    root = skill(body="See [the spec](../../docs/spec.md) and [a page](https://x/y).")
    assert orphans(root) == set()


def test_a_search_root_holding_many_skills_finds_each(tmp_path):
    for name in ("pixelpitch-deck", "pixelpitch-slide-craft"):
        (tmp_path / name).mkdir()
        (tmp_path / name / "SKILL.md").write_text(
            FRONT.format(name=name, description=GOOD_DESCRIPTION, body="")
        )
    assert {card.name for card in skill_cards([str(tmp_path)])} == {
        "pixelpitch-deck",
        "pixelpitch-slide-craft",
    }


# --------------------------------------------------------------------------
# what the turn actually opened


def test_a_turn_that_never_opened_a_skill_says_so(skill):
    log = DisclosureLog(skill_cards([str(skill())]))
    log.observe("run_command", None)
    assert log.record() == {
        "offered": ["pixelpitch-example"],
        "opened": {},
        "unopened": ["pixelpitch-example"],
    }


def test_opening_a_skill_and_a_page_of_it_is_recorded(skill):
    """The distinction the loop is for. A bad slide from a turn that opened
    nothing is a retrieval failure and the description is wrong; a bad slide
    from a turn that read the page is a content failure and the page is wrong.
    Those want opposite edits."""
    root = skill(
        body="The seam rules are in [references/seams.md](references/seams.md).",
        files={"references/seams.md": "# Seams\n"},
    )
    card = only(root)
    log = DisclosureLog([card])
    log.observe("view_file", str(card.root / "SKILL.md"))
    log.observe("view_file", str(card.root / "references" / "seams.md"))
    assert log.record()["opened"] == {
        "pixelpitch-example": ["SKILL.md", "references/seams.md"]
    }
    assert log.record()["unopened"] == []


def test_only_reads_count_as_opening(skill):
    """A turn that wrote a file into a skill directory did not read the skill,
    and counting it as retrieval would hide the failure the log exists for."""
    card = only(skill())
    log = DisclosureLog([card])
    log.observe("create_file", str(card.root / "SKILL.md"))
    assert log.record()["opened"] == {}


def test_a_file_outside_every_skill_is_not_a_skill_read(skill, tmp_path):
    card = only(skill())
    log = DisclosureLog([card])
    log.observe("view_file", str(tmp_path / "slides" / "07.html"))
    assert log.record()["opened"] == {}
