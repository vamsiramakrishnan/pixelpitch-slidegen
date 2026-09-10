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

"""Pick the slide form that carries a claim, without reading the whole canon.

The craft references total some six hundred lines. An author deciding how to
show one claim needs about eight of them, and cannot know which eight until
after reading. This inverts that: describe the claim, get one exhibit back,
with the exemplar to open and the one mistake that form invites.

Usage:
  pick_exhibit.py                          list every form, one line each
  pick_exhibit.py --role hero              list one role
  pick_exhibit.py --claim "how long it went undetected"
  pick_exhibit.py --id diagram-timeline
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

GALLERY = Path(__file__).resolve().parent.parent / "examples" / "index.json"

# Words that appear in almost every claim an author will type, so matching on
# them ranks by verbosity instead of by meaning.
NOISE = {
    "a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "how",
    "in", "is", "it", "its", "of", "on", "or", "our", "over", "show", "shows",
    "slide", "that", "the", "their", "then", "there", "they", "this", "to",
    "was", "we", "went", "were", "what", "when", "where", "which", "with",
}


def words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", text.lower()) if w not in NOISE}


def score(exhibit: dict, claim: set[str]) -> int:
    """Weight by how load-bearing the field is for the choice."""
    hits = 0
    for weight, field in (
        (3, "keywords"), (3, "best_for"), (2, "carries"), (2, "id"), (1, "primitives")
    ):
        value = exhibit.get(field) or ""
        text = " ".join(value) if isinstance(value, list) else str(value)
        hits += weight * len(claim & words(text))
    return hits


def brief(exhibit: dict) -> str:
    return f"{exhibit['id']:<22} {exhibit['role']:<8} {exhibit['carries']}"


def full(exhibit: dict, base: Path) -> str:
    lines = [
        f"{exhibit['id']}  ({exhibit['role']})",
        f"  carries    {exhibit['carries']}",
        f"  best for   {'; '.join(exhibit['best_for'])}",
        f"  avoid for  {'; '.join(exhibit['avoid_for'])}",
        f"  built from {', '.join(exhibit['primitives'])}",
        f"  watch out  {exhibit['watch']}",
        f"  look at    {base / exhibit['png']}",
        f"  start from {base / exhibit['html']}",
    ]
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--claim", help="what the slide has to say, in your own words")
    parser.add_argument("--id", dest="ident", help="one exhibit by id")
    parser.add_argument("--role", choices=("hero", "section", "body", "closing"))
    parser.add_argument("--top", type=int, default=3)
    args = parser.parse_args(argv[1:])

    data = json.loads(GALLERY.read_text(encoding="utf-8"))
    base = GALLERY.parent
    exhibits = data["exhibits"]
    if args.role:
        exhibits = [e for e in exhibits if e["role"] == args.role]

    if args.ident:
        chosen = [e for e in exhibits if e["id"] == args.ident]
        if not chosen:
            print(f"no exhibit '{args.ident}'. Run with no arguments to list them.",
                  file=sys.stderr)
            return 1
        print(full(chosen[0], base))
        return 0

    if args.claim:
        claim = words(args.claim)
        ranked = [e for e in exhibits if score(e, claim)]
        ranked.sort(key=lambda e: score(e, claim), reverse=True)
        if not ranked:
            print("Nothing matched that claim. Every form, pick by hand:\n", file=sys.stderr)
            for exhibit in exhibits:
                print(brief(exhibit))
            return 1
        for exhibit in ranked[: args.top]:
            print(full(exhibit, base))
            print()
        print(
            "The first is the suggestion, not the answer. Read 'avoid for' "
            "before you commit, and open the PNG before you copy the HTML.",
            file=sys.stderr,
        )
        return 0

    for exhibit in exhibits:
        print(brief(exhibit))
    print(
        f"\n{len(exhibits)} forms. Re-run with --claim '<what the slide says>' "
        "for the exemplar and the trap.",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
