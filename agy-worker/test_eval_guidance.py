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

"""The attribution table joins on strings three other programs print.

So it is checked against what they actually print. Every defect the real gates
emit for the deck's own negative controls has to land in some territory, and
every page a territory names has to exist. A table asserted against strings
typed into this file would agree with itself forever and drift the week a gate
reworded a message.
"""

from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest
from eval_guidance import TERRITORIES, classify, last_disclosure, score, territory

SKILLS = Path(__file__).resolve().parent / "skills"
CRAFT = SKILLS / "pixelpitch-slide-craft"


def event(**fields) -> str:
    return json.dumps({"agy_evt": "disclosure", **fields})


def _deck_cli():
    """The `deck` command as a module. It has no .py suffix, being a CLI."""
    path = SKILLS / "pixelpitch-deck" / "scripts" / "deck"
    spec = importlib.util.spec_from_loader(
        "deck_cli", importlib.machinery.SourceFileLoader("deck_cli", str(path))
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# --------------------------------------------------------------------------
# the table, against the gates and against the filesystem


def test_every_page_a_territory_names_exists():
    """A verdict that tells an author to fix `references/seams.md` when the
    page is called something else sends them looking for a file."""
    for entry in TERRITORIES:
        assert (SKILLS / entry.skill / entry.page).is_file(), entry


def test_every_defect_the_slop_lint_emits_is_attributed():
    """Run the real lint over the negative control, which is built to trip
    every registered rule, and require the table to claim all of them."""
    done = subprocess.run(
        [
            sys.executable,
            str(CRAFT / "scripts" / "lint_slides.py"),
            str(CRAFT / "examples"),
        ],
        capture_output=True,
        text=True,
    )
    defects = json.loads(done.stdout)
    emitted = [d for issues in defects.values() for d in issues]
    assert emitted, "the anti-example stopped tripping the lint"
    unclaimed = [d for d in emitted if territory(d) is None]
    assert unclaimed == []


def test_every_structural_defect_deck_check_emits_is_attributed():
    """The other gate, on the other interpreter. `deck check`'s structural
    findings are plain sentences with no rule ids, which is exactly why the
    marks have to be checked rather than assumed."""
    module = _deck_cli()
    broken = {
        "role": "body",
        "html": "<html><body><script>x</script><link rel=s>"
        '<div style="background-image:url(a.png);font-size:9px">t</div>'
        "</body></html>",
    }
    emitted = module.structural_defects(broken)
    assert len(emitted) >= 5, emitted
    unclaimed = [d for d in emitted if territory(d) is None]
    assert unclaimed == []


@pytest.mark.parametrize(
    ("defect", "skill"),
    [
        (
            "plate 'clean/navy-statement.png' is not one of the template's plates.",
            "pixelpitch-template-roundtrip",
        ),
        (
            "baselines/white_metric_grid.html is not registered in slide-types.json",
            "pixelpitch-deck",
        ),
        (
            "#ff0000 is not in the derived brand palette. Use only the",
            "pixelpitch-brand-derivation",
        ),
    ],
)
def test_the_gates_that_need_a_workspace_are_attributed_by_message(defect, skill):
    """These three come from gates that need an extracted template or a brand
    contract to run at all, so their exact strings are pinned here and the
    strings themselves are lifted from the emitting source."""
    assert territory(defect).skill == skill


def test_a_defect_no_page_claims_is_its_own_finding():
    """Not swallowed. A gate enforcing a rule nothing teaches is the one
    failure no amount of model improvement can fix."""
    found = classify("the deck is 40 slides, over the 30 slide ceiling", {})
    assert found["kind"] == "unattributed"


# --------------------------------------------------------------------------
# the join


def test_a_skill_never_opened_is_a_retrieval_failure():
    """The description is wrong. Nothing about the body can matter, because
    nothing read it."""
    found = classify("SLOP (ghost card): ...", {"pixelpitch-deck": ["SKILL.md"]})
    assert found["kind"] == "retrieval"
    assert found["skill"] == "pixelpitch-slide-craft"


def test_a_skill_opened_but_a_page_unreached_is_a_disclosure_failure():
    """The description worked and the link did not. A different edit."""
    found = classify(
        "SLOP (ghost card): ...", {"pixelpitch-slide-craft": ["SKILL.md"]}
    )
    assert found["kind"] == "disclosure"
    assert found["page"] == "references/anti-slop.md"


def test_a_page_read_and_disobeyed_is_a_content_failure():
    """The only one of the three that is evidence about the model."""
    found = classify(
        "SLOP (ghost card): ...",
        {"pixelpitch-slide-craft": ["SKILL.md", "references/anti-slop.md"]},
    )
    assert found["kind"] == "content"


def test_the_same_defect_gets_three_verdicts_from_three_histories():
    """The point of the whole module, stated as one assertion. One defect
    string, three turns, three different edits to make."""
    defect = "SLOP (eyebrow): ..."
    kinds = [
        classify(defect, opened)["kind"]
        for opened in (
            {},
            {"pixelpitch-slide-craft": ["SKILL.md"]},
            {"pixelpitch-slide-craft": ["references/anti-slop.md"]},
        )
    ]
    assert kinds == ["retrieval", "disclosure", "content"]


# --------------------------------------------------------------------------
# the run


def test_the_verdict_reads_the_last_turns_record():
    """A run that chatted twice emits two. The deck is what the last turn
    left, so it is judged against what the last turn had read."""
    lines = [
        event(offered=["a"], opened={}, unopened=["a"]),
        event(offered=["a"], opened={"a": ["SKILL.md"]}, unopened=[]),
    ]
    assert last_disclosure(lines)["opened"] == {"a": ["SKILL.md"]}


def test_noise_on_the_event_stream_does_not_break_the_read():
    """stderr carries the harness's own log lines alongside the NDJSON."""
    lines = [
        "[agy] tool: view_file",
        "not json at all",
        json.dumps({"agy_evt": "tool", "name": "view_file"}),
        event(offered=["a"], opened={"a": ["SKILL.md"]}, unopened=[]),
    ]
    assert last_disclosure(lines)["offered"] == ["a"]


def test_a_run_with_no_disclosure_event_scores_without_crashing():
    """An older worker, or a run that died before the turn completed. Every
    defect reads as a retrieval failure, which is honest: nothing is known to
    have been opened."""
    verdict = score(["[agy] turn complete"], {"1": ["SLOP (eyebrow): ..."]})
    assert verdict["reach"] is None
    assert verdict["gaps"] == {"retrieval": 1}


def test_a_clean_run_has_no_gaps():
    verdict = score([event(offered=["a"], opened={"a": ["SKILL.md"]}, unopened=[])], {})
    assert (verdict["defects"], verdict["gaps"], verdict["fix_first"]) == (0, {}, [])
    assert verdict["reach"] == 1.0


def test_the_cheapest_fix_is_named_first():
    """Twelve content defects and one unopened skill: open the skill. A
    verdict ranked by defect count would send the author to rewrite the page
    that was working."""
    opened = {"pixelpitch-slide-craft": ["references/anti-slop.md"]}
    defects = {
        "1": ["SLOP (eyebrow): x"] * 12
        + ["plate 'clean/invented.png' is not one of the template's plates"]
    }
    verdict = score(
        [event(offered=["pixelpitch-slide-craft"], opened=opened, unopened=[])],
        defects,
    )
    assert verdict["gaps"] == {"content": 12, "retrieval": 1}
    assert "pixelpitch-template-roundtrip was offered and never opened" in (
        verdict["fix_first"][0]
    )
