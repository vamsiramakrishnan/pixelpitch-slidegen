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

"""Tool-call denials for the parts of a workspace that are already decided.

A gate that runs after the turn reports a finding. The turn is already spent,
the author has already built twenty slides on the mistake, and fixing it costs
a whole re-run. The same rule expressed as a deny predicate costs one tool
call: the model is refused, reads the reason, and does the right thing on its
next step. Anything a gate can decide from a tool call's arguments alone
belongs here rather than there.

What this protects is the extracted template. ``spec-out/`` is what PowerPoint
said and ``clean/`` is the template's own artwork with the text painted off it;
neither is an opinion a turn is entitled to revise. But they cannot simply be
read-only, because ``deck spec`` writes the first and ``deck plates`` writes
the second. The line is ``layouts.json``: it hashes every plate, so the moment
it exists the template is sealed and any later write is the defect.

The defect is not hypothetical. One deck shipped with three slides sitting on
``navy-statement.png``, ``blue-statement.png`` and ``white-content.png`` —
flat rectangles an authoring turn produced mid-run and dropped into ``clean/``
beside the real plates. Every downstream gate passed them.
"""

from __future__ import annotations

import re
import shlex
from collections.abc import Iterable
from pathlib import Path
from urllib.parse import urlparse

# Sealed by `deck layouts`, which hashes every plate in clean/ into the
# contract. spec-out/ is sealed with it: a plate hash is only meaningful
# against the geometry it was measured from.
SEAL = "layouts.json"
SEALED_DIRS = ("clean", "spec-out")

# The keys the harness normalises paths into, per its own sanitisation list in
# local_connection.py, plus the ones our tools use for outputs.
PATH_KEYS = ("path", "file_path", "directory_path", "TargetFile", "output_path")

# run_command carries its command as CommandLine over MCP and command_line in
# the native proto.
COMMAND_KEYS = ("CommandLine", "command_line", "command")

# Shell verbs whose non-flag operands are destinations. Reads are not denied:
# `deck diff --b clean/slide-03.png` must keep working, and an author who has
# been refused once on a correct call stops reading refusals.
WRITE_VERBS = {
    "cp", "mv", "rm", "rmdir", "install", "tee", "touch", "dd", "truncate",
    "ln", "mkdir", "unzip", "tar", "convert", "magick", "rsync", "chmod",
    "chown",
}

# `--out clean`, `-o clean/x.png`, `--output=clean`. The pipeline's own
# writers use these, which is the point: re-running `deck plates` over a
# sealed clean/ is the "overwritten since precompute" finding, caught one
# tool call earlier.
_OUT_FLAG = re.compile(r"^--?(?:out|output|outdir|out-dir|o)(?:=(.*))?$")

# `> clean/x.png`, `>> clean/x.png`.
_REDIRECT = re.compile(r"(?:^|\s)\d?>>?\s*([^\s;|&]+)")


def _resolve(value: str, cwd: Path | None) -> Path | None:
    if value.startswith(("file://", "cns://")):
        value = urlparse(value).path
    try:
        path = Path(value).expanduser()
        return (cwd / path if cwd and not path.is_absolute() else path).resolve()
    except OSError:
        return None


def sealed_roots(workspaces: Iterable[str | Path]) -> list[Path]:
    """The ``clean/`` and ``spec-out/`` directories that are already pinned.

    Recomputed per tool call rather than cached, because the seal appears
    partway through a session: the turn that runs `deck layouts` is usually
    the same turn that goes on to author slides.
    """
    roots: list[Path] = []
    for workspace in workspaces:
        try:
            base = Path(workspace).resolve()
        except OSError:
            continue
        compiled = (base / "source-extraction.json").is_file()
        if not (base / SEAL).is_file() and not compiled:
            continue
        roots += [base / name for name in SEALED_DIRS if (base / name).is_dir()]
        if compiled and (base / "baselines").is_dir():
            roots.append(base / "baselines")
    return roots


def _inside(path: Path | None, roots: list[Path]) -> bool:
    return path is not None and any(
        path == root or root in path.parents for root in roots
    )


def _command_targets(command: str, cwd: Path | None) -> list[Path]:
    """Where a shell command would write, as far as a string can say.

    Deliberately partial. It reads redirects, `--out`-style flags and the
    operands of verbs that exist to write, and says nothing about anything
    else. A guard that guessed would refuse correct calls, and an author who
    is refused on a correct call stops reading refusals.
    """
    targets: list[Path] = []
    for match in _REDIRECT.finditer(command):
        resolved = _resolve(match.group(1), cwd)
        if resolved is not None:
            targets.append(resolved)

    try:
        words = shlex.split(command)
    except ValueError:
        return targets

    expect_value = False
    verb_operands = False
    for index, word in enumerate(words):
        if expect_value:
            expect_value = False
            resolved = _resolve(word, cwd)
            if resolved is not None:
                targets.append(resolved)
            continue
        flag = _OUT_FLAG.match(word)
        if flag:
            if flag.group(1):
                resolved = _resolve(flag.group(1), cwd)
                if resolved is not None:
                    targets.append(resolved)
            else:
                expect_value = True
            continue
        if index == 0 or word in {"&&", "||", ";", "|"}:
            verb_operands = False
        if Path(word).name in WRITE_VERBS:
            verb_operands = True
            continue
        if verb_operands and not word.startswith("-"):
            resolved = _resolve(word, cwd)
            if resolved is not None:
                targets.append(resolved)
    return targets


def writes_into_sealed(args, roots: list[Path], cwd: Path | None = None) -> bool:
    """True when this tool call would write inside a sealed directory."""
    if not isinstance(args, dict) or not roots:
        return False
    for key in PATH_KEYS:
        value = args.get(key)
        if isinstance(value, str) and value.strip():
            if _inside(_resolve(value, cwd), roots):
                return True
    for key in COMMAND_KEYS:
        command = args.get(key)
        if isinstance(command, str) and command.strip():
            if any(_inside(t, roots) for t in _command_targets(command, cwd)):
                return True
    return False


# Tools that can put bytes on disk. `generate_image` is here because an
# invented flat plate is exactly what an image generator produces when a turn
# decides the template is missing a ground it needs.
WRITE_TOOLS = ("create_file", "edit_file", "generate_image", "run_command")

DENIAL = (
    "the template is sealed: clean/ and spec-out/ are hash-pinned in "
    "layouts.json, so a file written there is an invented ground however it "
    "is named. Pick a layout from layouts.json, or re-run `deck plates` and "
    "`deck layouts` together if the plates are genuinely wrong."
)


def seal_policies(workspaces: list[str], cwd: Path | None = None) -> list:
    """Deny writes into any sealed template directory.

    Sorted before the caller's wildcard allow by the same rule the scoped
    file denies rely on: the evaluator prefers a specific tool over ``*``.
    """
    from google.antigravity.hooks import policy

    def denied(args) -> bool:
        return writes_into_sealed(args, sealed_roots(workspaces), cwd)

    return [
        policy.deny(tool, when=denied, name="sealed_template")
        for tool in WRITE_TOOLS
    ]
