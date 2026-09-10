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

"""Did the guidance arrive, and did it land? Two questions, one answer each.

A defect count alone cannot improve a skill. Twenty off-template slides tell
you the deck is wrong and nothing about which edit fixes it, so the loop churns
between rewriting descriptions and rewriting pages, half of them making things
worse. The two failures look identical in the output and want opposite edits.

This pairs every defect with what the turn had actually read when it made it.

``retrieval``
    The skill that governs this defect was never opened. The body is
    irrelevant; the frontmatter description did not name the situation the turn
    was in. Fix the description.

``disclosure``
    The skill was opened, the page that governs the defect was not. It was
    offered and unreached, which is a link problem inside the skill. Fix the
    link, or fold the rule into a page that is read.

``content``
    The page was open and the defect happened anyway. Retrieval worked. The
    page is unconvincing, buried, or wrong. Fix the prose.

The first two are cheap to fix and account for most of what looks like model
failure. Only the third is evidence about the model.

Usage:
  eval_guidance.py --events run.ndjson --defects check.json [--defects lint.json]

``run.ndjson`` is the worker's stderr, which carries the ``disclosure`` event
`runner.py` emits at the end of every turn. ``--defects`` takes any number of
gate outputs in the shape both `deck check` and `lint_slides.py` already print,
``{"<index>": ["defect", ...]}``.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Territory:
    """Which page owns a class of defect.

    Matched on the marks the gates already print rather than on rule ids,
    because the gates are three separate programs on two interpreters and a
    shared id registry between them would be a fourth thing to keep in sync.
    The marks are asserted against real gate output in the tests.
    """

    skill: str
    page: str
    marks: tuple[str, ...]


# First match wins, so the specific marks come before the general ones.
TERRITORIES: tuple[Territory, ...] = (
    Territory(
        skill="pixelpitch-slide-craft",
        page="references/anti-slop.md",
        marks=("SLOP (",),
    ),
    Territory(
        skill="pixelpitch-brand-derivation",
        page="SKILL.md",
        marks=("BRAND (", "is not in the derived brand palette"),
    ),
    Territory(
        skill="pixelpitch-template-roundtrip",
        page="references/source-roundtrip.md",
        marks=("plate ", "no plate:", "layouts.json"),
    ),
    Territory(
        skill="pixelpitch-deck",
        page="references/playbooks/precompute.md",
        marks=("slide type ", "baselines/", "is not registered"),
    ),
    Territory(
        skill="pixelpitch-deck",
        page="SKILL.md",
        marks=(
            "data-pptx-role",
            "canvas is not pinned",
            "<script>",
            "<link>",
            "background-image",
            "forces a raster",
            "font-size below",
            "below the 40px floor",
        ),
    ),
)


def territory(defect: str) -> Territory | None:
    for entry in TERRITORIES:
        if any(mark in defect for mark in entry.marks):
            return entry
    return None


def last_disclosure(lines) -> dict:
    """The turn's retrieval record, from the worker's NDJSON stderr.

    The last one wins: a run that chatted more than once emits one per turn and
    the deck is judged as the last turn left it.
    """
    found: dict = {}
    for line in lines:
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if isinstance(event, dict) and event.get("agy_evt") == "disclosure":
            found = event
    return found


def classify(defect: str, opened: dict[str, list[str]]) -> dict:
    """One defect, paired with what the turn had read when it made it."""
    where = territory(defect)
    if where is None:
        # Not a failure of the eval to be hidden. A defect no page claims is a
        # rule enforced by a gate and taught by nobody, which is its own
        # finding and a common one.
        return {"kind": "unattributed", "defect": defect}
    pages = opened.get(where.skill)
    if pages is None:
        kind = "retrieval"
    elif where.page not in pages:
        kind = "disclosure"
    else:
        kind = "content"
    return {
        "kind": kind,
        "defect": defect,
        "skill": where.skill,
        "page": where.page,
    }


ADVICE = {
    "retrieval": (
        "{skill} was offered and never opened. Its frontmatter description "
        "does not name the situation this turn was in."
    ),
    "disclosure": (
        "{skill} was opened but {page} was not reached. Nothing the turn read "
        "linked to it."
    ),
    "content": (
        "{page} was open and the defect happened anyway. This one is about the "
        "page, not about retrieval."
    ),
    "unattributed": (
        "No page claims this defect. A gate enforces a rule that nothing "
        "teaches, so no turn can be expected to satisfy it."
    ),
}


def score(lines, defects: dict[str, list[str]]) -> dict:
    """The run's guidance verdict: what reached the turn, and what it cost."""
    record = last_disclosure(lines)
    offered = list(record.get("offered") or [])
    opened = dict(record.get("opened") or {})

    findings = [
        classify(defect, opened)
        for issues in defects.values()
        for defect in issues
    ]
    kinds: dict[str, int] = {}
    for finding in findings:
        kinds[finding["kind"]] = kinds.get(finding["kind"], 0) + 1

    return {
        "offered": offered,
        "opened": {name: sorted(pages) for name, pages in sorted(opened.items())},
        "unopened": sorted(record.get("unopened") or []),
        "reach": round(len(opened) / len(offered), 3) if offered else None,
        "defects": len(findings),
        "gaps": kinds,
        "findings": findings,
        # Which edit to make first. A skill nothing opened is one description
        # away from being read at all, so it outranks a page that was read and
        # ignored however many defects each accounts for.
        "fix_first": _fix_first(findings),
    }


def _fix_first(findings: list[dict]) -> list[str]:
    for kind in ("unattributed", "retrieval", "disclosure", "content"):
        matching = [f for f in findings if f["kind"] == kind]
        if matching:
            return sorted(
                {
                    ADVICE[kind].format(
                        skill=f.get("skill", "?"), page=f.get("page", "?")
                    )
                    for f in matching
                }
            )
    return []


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--events", required=True, help="the worker's NDJSON stderr")
    parser.add_argument(
        "--defects",
        action="append",
        default=[],
        help="a gate's JSON output; repeatable",
    )
    args = parser.parse_args(argv[1:])

    merged: dict[str, list[str]] = {}
    for entry in args.defects:
        loaded = json.loads(Path(entry).read_text(encoding="utf-8"))
        for key, issues in (loaded.get("defects", loaded) or {}).items():
            merged.setdefault(str(key), []).extend(issues)

    lines = Path(args.events).read_text(encoding="utf-8").splitlines()
    verdict = score(lines, merged)
    print(json.dumps(verdict, indent=2, sort_keys=True))
    return 1 if verdict["defects"] else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
