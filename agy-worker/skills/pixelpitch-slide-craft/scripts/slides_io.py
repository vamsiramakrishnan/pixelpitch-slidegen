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

"""One reader for every shape a deck workspace calls "the slides".

Three shapes exist in the wild and every gate has to accept all three, because
the workspace reference tells the author to write the third one and the gates
were built against the first.

1. ``[{"html": "<!doctype html>..."}]`` — inline, what the renderer wants.
2. ``{"slides": [{"html": ...}]}`` — the same, wrapped.
3. ``{"slides": [{"file": "01-x.html", "title": ..., "role": ...}]}`` — the
   deliverable ``slides/index.json`` documented in the deck skill's
   ``references/workspace.md``. Paths resolve against the index's directory.

A directory also works, and sorts by filename. That is the shape a hurried
author actually produces.

Stdlib only. Every caller here runs under whichever interpreter the harness
could reach.
"""

from __future__ import annotations

import base64
import json
import mimetypes
import re
from pathlib import Path

__all__ = ["load_slides", "inline_assets"]


def _from_directory(path: Path) -> list[dict]:
    files = sorted(p for p in path.glob("*.html"))
    if not files:
        raise SystemExit(f"slides: no .html files in {path}")
    return [
        {"file": p.name, "path": str(p.resolve()), "title": p.stem, "html": p.read_text(encoding="utf-8")}
        for p in files
    ]


_ASSET = re.compile(
    r"""(?P<lead>\bsrc\s*=\s*|\burl\s*\()(?P<quote>["']?)(?P<ref>[^"')>]+)(?P=quote)""",
    re.IGNORECASE,
)
_REMOTE = ("http:", "https:", "data:", "blob:", "//", "#")


def inline_assets(slide: dict) -> str:
    """The slide's HTML with every local ``src`` and ``url()`` as a data URI.

    A slide travels to the renderer as a bare string, so a relative path in it
    resolves against wherever that process happens to be rather than against the
    workspace. Local Chromium reads the file off disk and the gate goes green;
    the shipped deck gets a broken-image icon and the converter, seeing no
    image, rasterises the whole slide. The plates disappear and the text stops
    being text, and nothing that reads the HTML can tell.

    A reference that does not resolve raises. Degrading quietly here is the
    defect: the caller cannot see the render, so a warning it never reads buys
    nothing, and a deck of blank rectangles converts perfectly well.
    """
    html = slide.get("html") or ""
    path = slide.get("path")
    base = Path(path).parent if path else Path.cwd()

    def replace(match: re.Match[str]) -> str:
        ref = match.group("ref").strip()
        if not ref or ref.lower().startswith(_REMOTE):
            return match.group(0)
        target = (base / ref.split("?", 1)[0].split("#", 1)[0]).resolve()
        if not target.is_file():
            raise SystemExit(
                f"slides: {slide.get('file') or path or 'slide'} references "
                f"{ref}, which does not exist at {target}. A slide travels to "
                "the renderer as text, so every asset it names has to be a file "
                "this process can read."
            )
        mime = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        encoded = base64.b64encode(target.read_bytes()).decode("ascii")
        quote = match.group("quote")
        return f"{match.group('lead')}{quote}data:{mime};base64,{encoded}{quote}"

    return _ASSET.sub(replace, html)


def load_slides(path: Path) -> list[dict]:
    """Slide records with ``html`` populated, whatever shape was on disk."""
    if path.is_dir():
        return _from_directory(path)

    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, dict):
        data = data.get("slides")
    if not isinstance(data, list) or not data:
        raise SystemExit(f"slides: {path} is not a non-empty list of slides")

    base = path.parent
    out: list[dict] = []
    for index, entry in enumerate(data):
        if not isinstance(entry, dict):
            raise SystemExit(
                f"slides: {path} entry {index} is {type(entry).__name__}, not an "
                'object. Expected {"file": ...} or {"html": ...}.'
            )
        slide = dict(entry)
        if not slide.get("html"):
            ref = slide.get("file") or slide.get("path")
            if not ref:
                raise SystemExit(
                    f"slides: {path} entry {index} has neither 'html' nor 'file'."
                )
            target = (base / ref).resolve()
            if not target.is_file():
                raise SystemExit(f"slides: {path} entry {index} points at missing {target}")
            slide["html"] = target.read_text(encoding="utf-8")
            slide["path"] = str(target)
        out.append(slide)
    return out
