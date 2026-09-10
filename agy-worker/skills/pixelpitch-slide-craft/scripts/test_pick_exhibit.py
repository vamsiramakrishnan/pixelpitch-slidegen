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

"""The gallery index is load-bearing routing, so it is tested like code.

An entry pointing at a file that does not exist sends an author to a dead path
at the exact moment they asked for help, which is worse than no picker at all.
"""

from __future__ import annotations

import json

import pytest
from pick_exhibit import GALLERY, score, words

EXHIBITS = json.loads(GALLERY.read_text(encoding="utf-8"))["exhibits"]
FIELDS = ("id", "role", "carries", "best_for", "avoid_for", "keywords",
          "primitives", "watch", "html", "png")


@pytest.mark.parametrize("exhibit", EXHIBITS, ids=lambda e: e["id"])
def test_entry_is_complete_and_its_files_exist(exhibit):
    assert not [f for f in FIELDS if not exhibit.get(f)]
    assert exhibit["role"] in ("hero", "section", "body", "closing")
    for key in ("html", "png"):
        assert (GALLERY.parent / exhibit[key]).is_file()


def test_ids_are_unique():
    ids = [e["id"] for e in EXHIBITS]
    assert len(set(ids)) == len(ids)


def test_every_exemplar_html_is_indexed():
    on_disk = {
        p.name for p in GALLERY.parent.glob("*.html") if p.name != "slop-anti-example.html"
    }
    assert on_disk == {e["html"] for e in EXHIBITS}


@pytest.mark.parametrize(
    "claim, expected",
    [
        ("how long the outage went undetected", "diagram-timeline"),
        ("38 of 214 circuits are over their rating", "matrix-unit-dots"),
        ("who owns each action and by when", "table-with-owners"),
        ("reliability falling past the licence limit", "chart-line-threshold"),
        ("how the eligible population narrowed at each stage", "diagram-flow"),
        ("today's process against the proposal", "compare-two-states"),
        ("build the line or buy the hour back", "split-field-positions"),
        ("the pivot sentence between the two halves of the deck", "statement-single-line"),
        ("open on one declaration at full volume", "field-declarative"),
    ],
)
def test_claim_ranks_the_right_form_first(claim, expected):
    ranked = sorted(EXHIBITS, key=lambda e: score(e, words(claim)), reverse=True)
    assert ranked[0]["id"] == expected


def test_noise_words_alone_match_nothing():
    assert not [e for e in EXHIBITS if score(e, words("the a of and with this that"))]
