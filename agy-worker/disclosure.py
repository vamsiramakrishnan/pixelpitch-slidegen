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

"""What the worker was offered, and what it actually opened.

``skills_paths`` hands the harness a set of directories. The harness reads
each ``SKILL.md``'s YAML frontmatter and puts the ``description`` in front of
the model; everything below the frontmatter is only read if the model decides
to open the file, and everything under ``references/`` is only read if
something it already opened links to it. So the frontmatter is not
documentation. It is the retrieval index, and it is the only part of a skill
that is guaranteed to be seen.

Two things follow, and this module does both.

A reference file nothing links to cannot be reached. Progressive disclosure
walks links; a file in the directory with no path to it from ``SKILL.md`` is
as invisible as a file that is not there. This is the same defect as the seven
``baselines/`` documents no ``slide-types.json`` registered, which is why all
twenty slides of that deck were authored freehand. It is mechanically
checkable, so it is checked here.

And what was opened is the only honest measure of whether guidance arrived.
A turn that produced an off-template slide having never opened the roundtrip
skill is a retrieval failure, and the fix is the description. A turn that
opened it and still produced the slide is a content failure, and the fix is
the body. Those two want opposite edits, so a loop that cannot tell them apart
will churn. ``DisclosureLog`` records which happened.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

# Frontmatter is the fenced YAML block at the very top of the file. Parsed
# with a regex rather than a YAML library because the harness reads only two
# scalar keys out of it and the worker must not gain a dependency to agree
# with the harness about what a skill is called.
_FRONTMATTER = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.S)
_SCALAR = re.compile(r"^(\w[\w-]*)\s*:\s*(.*?)\s*$", re.M)

# Markdown and HTML links, plus the bare relative paths agent-facing prose
# tends to use ("see references/seams.md", "../examples/slop-anti-example.html").
_LINK = re.compile(
    r"\]\(([^)\s#]+)|"
    r"href=[\"']([^\"'#]+)|"
    r"(?<![\w./-])((?:\.\./)*(?:references|assets|examples|scripts)/[\w./-]+)"
)

DOC_SUFFIXES = (".md", ".html", ".css", ".json", ".txt")

# Files worth opening for outbound links. JSON is here because a manifest a
# script queries is the other way a skill discloses a page, and it is the
# better way: `pick_exhibit.py --claim ...` returns the one exemplar that fits
# instead of sixteen the model has to read past. Reach is reach however it is
# obtained, so a walk that only followed prose links would report the whole
# gallery as dead.
_TRAVERSED = (".md", ".html", ".json")


@dataclass(frozen=True)
class Skill:
    """One skill as progressive disclosure sees it."""

    name: str
    description: str
    root: Path
    reachable: frozenset[Path] = frozenset()
    unreachable: frozenset[Path] = frozenset()


def _frontmatter(text: str) -> dict[str, str]:
    block = _FRONTMATTER.match(text)
    if not block:
        return {}
    return dict(_SCALAR.findall(block.group(1)))


def _links(text: str, base: Path) -> set[Path]:
    out: set[Path] = set()
    for match in _LINK.finditer(text):
        # A path written into a sentence ends with the sentence. No file is
        # named `README.md.`, so nothing legitimate is lost by trimming.
        target = next(group for group in match.groups() if group).rstrip(".,;:")
        if not target or "://" in target:
            continue
        try:
            out.add((base / target).resolve())
        except OSError:
            continue
    return out


def _manifest_links(text: str, base: Path) -> set[Path]:
    """Filenames a manifest names, which is how a script-served page is found.

    Every string in the document is tried and only the ones that name a file
    that exists are kept, so a manifest cannot talk a page into being reachable
    the way prose could.
    """
    try:
        loaded = json.loads(text)
    except ValueError:
        return set()
    out: set[Path] = set()
    stack = [loaded]
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            stack.extend(node.values())
        elif isinstance(node, list):
            stack.extend(node)
        elif isinstance(node, str) and node and len(node) < 200:
            try:
                candidate = base / node
                if candidate.is_file():
                    out.add(candidate.resolve())
            except OSError:
                continue
    return out


def _walk(root: Path) -> tuple[set[Path], set[Path]]:
    """Everything reachable from SKILL.md by following links, and the rest.

    Transitive, because a reference that links onward to another reference is
    the shape this repository's skills are actually written in.
    """
    root = root.resolve()
    reachable: set[Path] = set()
    queue = [root / "SKILL.md"]
    while queue:
        current = queue.pop()
        if current in reachable or not current.is_file():
            continue
        reachable.add(current)
        suffix = current.suffix.lower()
        if suffix not in _TRAVERSED:
            continue
        text = current.read_text(encoding="utf-8", errors="replace")
        found = (
            _manifest_links(text, current.parent)
            if suffix == ".json"
            else _links(text, current.parent)
        )
        for target in found:
            if root in target.parents and target not in reachable:
                queue.append(target)

    present = {
        path.resolve()
        for path in root.rglob("*")
        if path.is_file() and path.suffix.lower() in DOC_SUFFIXES
    }
    return reachable, present - reachable


def skill_cards(paths: list[str | Path]) -> list[Skill]:
    """Every skill under the given search roots, as the harness would find it."""
    found: list[Skill] = []
    for entry in paths:
        root = Path(entry)
        if not root.is_dir():
            continue
        candidates = (
            [root] if (root / "SKILL.md").is_file() else sorted(root.iterdir())
        )
        for candidate in candidates:
            manifest = candidate / "SKILL.md"
            if not manifest.is_file():
                continue
            front = _frontmatter(manifest.read_text(encoding="utf-8"))
            reachable, unreachable = _walk(candidate)
            found.append(
                Skill(
                    name=front.get("name", candidate.name),
                    description=front.get("description", ""),
                    root=candidate.resolve(),
                    reachable=frozenset(reachable),
                    unreachable=frozenset(unreachable),
                )
            )
    return found


# --------------------------------------------------------------------------
# the lint


# Long enough to name the situations that should trigger it. The four skills
# in this worker run 250 to 500 characters and each one lists the artifacts
# and symptoms that mean "you are in this territory"; a one-line summary of
# what the skill contains retrieves on nothing.
MIN_DESCRIPTION = 120


def disclosure_findings(paths: list[str | Path]) -> list[str]:
    """Everything that would keep a skill or a page of it from being read."""
    problems: list[str] = []
    for skill in skill_cards(paths):
        where = skill.root.name
        if not skill.description:
            problems.append(
                f"{where}: no description in the frontmatter, so nothing the "
                "model sees can make it open this skill"
            )
        elif len(skill.description) < MIN_DESCRIPTION:
            problems.append(
                f"{where}: the description is {len(skill.description)} "
                f"characters. Under ~{MIN_DESCRIPTION} it is a summary of what "
                "the skill contains rather than a list of the situations that "
                "should trigger it, and retrieval is the only job it has."
            )
        for orphan in sorted(skill.unreachable):
            problems.append(
                f"{where}: nothing links to "
                f"{orphan.relative_to(skill.root)}, so progressive disclosure "
                "cannot reach it. Link it from SKILL.md or from a page that is "
                "itself reachable, or delete it."
            )
    return problems


# --------------------------------------------------------------------------
# the observation


# The keys the harness normalises a read path into. `canonical_path` on the
# chunk is preferred when the harness has already resolved one.
READ_PATH_KEYS = ("path", "file_path", "TargetFile", "AbsolutePath")


def call_path(args, canonical: str | None = None) -> str | None:
    """The file a tool call names, as far as its arguments say."""
    if canonical:
        return canonical
    if not isinstance(args, dict):
        return None
    for key in READ_PATH_KEYS:
        value = args.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return None


@dataclass
class DisclosureLog:
    """Which of the offered skills a turn actually opened.

    Fed from the tool-call stream, where every ``view_file`` carries the path
    it read. A skill counts as opened when its ``SKILL.md`` was read, and the
    depth reached is how many of its pages followed.
    """

    skills: list[Skill]
    opened: dict[str, set[Path]] = field(default_factory=dict)

    def observe(self, tool: str, path: str | None) -> None:
        if tool != "view_file" or not path:
            return
        try:
            resolved = Path(path).expanduser().resolve()
        except OSError:
            return
        for skill in self.skills:
            if skill.root == resolved or skill.root in resolved.parents:
                self.opened.setdefault(skill.name, set()).add(resolved)
                return

    def record(self) -> dict:
        """The turn's retrieval outcome, ready to pair with its gate verdict."""
        return {
            "offered": [skill.name for skill in self.skills],
            "opened": {
                name: sorted(
                    str(path.relative_to(skill.root))
                    for skill in self.skills
                    if skill.name == name
                    for path in paths
                )
                for name, paths in sorted(self.opened.items())
            },
            "unopened": sorted(
                skill.name for skill in self.skills if skill.name not in self.opened
            ),
        }
