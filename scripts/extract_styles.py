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

"""Vendor the repo's deck-style skills into compact style descriptors.

Each upstream style skill ships:

* ``template.json``  — slug, name, tagline, mood, occasion (not always present)
* ``SKILL.md``       — YAML frontmatter description, "Best for" / "Avoid for"
* ``example.html``   — a full reference deck whose ``:root`` custom properties
                       and ``font-family`` declarations are the design system

The reference decks run to ~90 KB, which is far too much to feed the authoring
model per request. This extracts just the load-bearing parts (palette, fonts,
voice, when-to-use) into one index the agent can ship in its image.

Run from the agent project root:

    uv run python scripts/extract_styles.py
"""

from __future__ import annotations

import json
import os
import re
from collections import OrderedDict
from pathlib import Path

# Optional maintenance source; presets are bundled in app/assets/styles.
REPO_SKILLS = Path(os.environ.get("SLIDEGEN_STYLE_SOURCE", "style-source"))
OUT_DIR = Path(__file__).resolve().parents[1] / "app" / "assets" / "styles"

# Skill-name prefixes that denote a full deck visual system.
STYLE_PREFIXES = (
    "html-ppt-zhangzara-",
    "html-ppt-taste-",
    "deck-",
    "kami-deck",
    "replit-deck",
    "magazine-web-ppt",
    "ppt-keynote",
    "html-ppt-xhs-",
    "html-ppt-dir-key-nav-minimal",
    "html-ppt-graphify-dark-graph",
    "html-ppt-hermes-cyber-terminal",
    "html-ppt-knowledge-arch-blueprint",
    "html-ppt-obsidian-claude-gradient",
    "html-ppt-retro-quarterly-review",
    "html-ppt-testing-safety-alert",
)

# Custom properties that are clearly not colours.
_NON_COLOUR = re.compile(r"(font|size|weight|space|gap|radius|width|height|dur)")


def _frontmatter(text: str) -> dict:
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end == -1:
        return {}
    block = text[3:end]
    out: dict = {}
    key = None
    for line in block.splitlines():
        if re.match(r"^[a-zA-Z_]+:", line):
            key, _, val = line.partition(":")
            key = key.strip()
            val = val.strip()
            out[key] = val if val and val != "|" else ""
        elif key and line.strip().startswith("- "):
            out.setdefault(key + "_list", []).append(line.strip()[2:].strip('"'))
        elif key and line.strip():
            out[key] = (out.get(key, "") + " " + line.strip()).strip()
    return out


def _section(text: str, heading: str) -> str:
    m = re.search(rf"^##\s+{re.escape(heading)}\s*$", text, re.M)
    if not m:
        return ""
    rest = text[m.end() :]
    nxt = re.search(r"^##\s+", rest, re.M)
    body = rest[: nxt.start()] if nxt else rest
    return " ".join(body.split()).strip()


def _palette(html: str) -> OrderedDict[str, str]:
    palette: OrderedDict[str, str] = OrderedDict()
    for name, value in re.findall(
        r"--([a-zA-Z0-9-]+)\s*:\s*(#[0-9A-Fa-f]{3,8})\s*;", html
    ):
        if _NON_COLOUR.search(name.lower()):
            continue
        palette.setdefault(name, value.upper())
    if not palette:  # fall back to most common literal hexes
        counts: dict[str, int] = {}
        for hexv in re.findall(r"#[0-9A-Fa-f]{6}", html):
            counts[hexv.upper()] = counts.get(hexv.upper(), 0) + 1
        for i, (hexv, _) in enumerate(
            sorted(counts.items(), key=lambda kv: -kv[1])[:6]
        ):
            palette[f"color{i + 1}"] = hexv
    return palette


def _fonts(html: str) -> list[str]:
    seen: list[str] = []
    for decl in re.findall(r"font-family:\s*([^;{}]+)", html):
        first = decl.split(",")[0].strip().strip("'\"")
        low = first.lower()
        if not first or low in ("inherit", "initial", "unset"):
            continue
        if low.startswith("var(") or first.startswith("--"):
            continue
        if first not in seen:
            seen.append(first)
    return seen[:4]


def main() -> None:
    if not REPO_SKILLS.is_dir():
        raise SystemExit(f"skills dir not found: {REPO_SKILLS}")

    styles = []
    for d in sorted(REPO_SKILLS.iterdir()):
        if not d.is_dir() or not d.name.startswith(STYLE_PREFIXES):
            continue
        skill_md = d / "SKILL.md"
        example = d / "example.html"
        if not skill_md.is_file() or not example.is_file():
            continue

        md = skill_md.read_text(encoding="utf-8", errors="ignore")
        html = example.read_text(encoding="utf-8", errors="ignore")
        tpl = {}
        tpl_path = d / "template.json"
        if tpl_path.is_file():
            try:
                tpl = json.loads(tpl_path.read_text(encoding="utf-8"))
            except ValueError:
                tpl = {}

        fm = _frontmatter(md)
        name = tpl.get("name") or d.name.replace("html-ppt-", "").replace("-", " ").title()
        tagline = tpl.get("tagline") or fm.get("description", "")
        tagline = " ".join(tagline.split()).strip().strip('"').strip("|").strip()[:300]

        styles.append(
            {
                "id": d.name,
                "name": name,
                "tagline": tagline,
                "mood": tpl.get("mood", []),
                "occasion": tpl.get("occasion", []),
                "best_for": _section(md, "Best for")[:400],
                "avoid_for": _section(md, "Avoid for")[:300],
                "palette": _palette(html),
                "fonts": _fonts(html),
            }
        )

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "index.json").write_text(
        json.dumps(styles, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    with_pal = sum(1 for s in styles if s["palette"])
    print(f"extracted {len(styles)} styles -> {OUT_DIR / 'index.json'}")
    print(f"  with palette: {with_pal}   with fonts: "
          f"{sum(1 for s in styles if s['fonts'])}")


if __name__ == "__main__":
    main()
