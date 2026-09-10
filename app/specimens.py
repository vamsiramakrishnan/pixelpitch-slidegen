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

"""Visual specimens for brand and style pickers.

A picker that offers only "Airbnb | Travel / Marketplace" asks the user to
choose a visual identity from a category label, which they cannot do. These
helpers render each option as a small visual card — palette chips, a type
specimen, and a miniature 16:9 slide — so the choice is made by eye.

The rendered document is substituted into an ``IFrameSrcdoc`` component by the
same token mechanism used for the deck preview (see ``app.tools``).
"""

from __future__ import annotations

import html as html_lib
import json
import logging
import re
from functools import lru_cache
from pathlib import Path

logger = logging.getLogger(__name__)

_ASSETS = Path(__file__).parent / "assets"
_BRANDS = _ASSETS / "brands"
_STYLES_INDEX = _ASSETS / "styles" / "index.json"

_HEX = re.compile(r"#[0-9A-Fa-f]{6}\b")

# Colours that carry no identity and would dilute a palette strip.
_IGNORED = {"#FFFFFF", "#000000", "#FFF", "#000"}


def _readable_on(hex_colour: str) -> str:
    """Pick black or white text for adequate contrast on ``hex_colour``."""
    try:
        r = int(hex_colour[1:3], 16)
        g = int(hex_colour[3:5], 16)
        b = int(hex_colour[5:7], 16)
    except (ValueError, IndexError):
        return "#000"
    # Perceived luminance (ITU-R BT.601).
    return "#000" if (r * 299 + g * 587 + b * 114) / 1000 > 140 else "#fff"


@lru_cache(maxsize=32)
def brand_palette(brand_id: str, limit: int = 6) -> tuple[str, ...]:
    """Most prominent identity colours in a brand guideline, in order."""
    path = _BRANDS / f"{brand_id}.md"
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return ()
    counts: dict[str, int] = {}
    order: list[str] = []
    for raw in _HEX.findall(text):
        value = raw.upper()
        if value in _IGNORED:
            continue
        if value not in counts:
            order.append(value)
        counts[value] = counts.get(value, 0) + 1
    # Prefer frequently-referenced colours, keep document order as tiebreak.
    ranked = sorted(order, key=lambda c: (-counts[c], order.index(c)))
    return tuple(ranked[:limit])


@lru_cache(maxsize=32)
def brand_fonts(brand_id: str) -> tuple[str, ...]:
    """Typeface names from a guideline's "Font Family" section.

    Guidelines describe type in prose rather than CSS, e.g.
    ``- **Airbnb Cereal VF** (primary and only): ...`` or
    ``- **Primary**: `Geist`, with fallbacks: ...``. Bold labels that are just
    role names are skipped in favour of the actual typeface.
    """
    path = _BRANDS / f"{brand_id}.md"
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return ()

    match = re.search(r"###\s*Font Famil(?:y|ies)(.*?)(?=\n###|\n##|\Z)", text, re.S)
    if not match:
        return ()
    section = match.group(1)

    skip = re.compile(r"^(no\b|opentype|usage|loading|fallback|weights|features)", re.I)
    # Bold labels that name a ROLE rather than a typeface.
    role_label = re.compile(
        r"^(primary|secondary|monospace|mono|display|text|ui|body|title|code"
        r"|heading|serif|script|accent|rewards)\b|/",
        re.I,
    )
    found: list[str] = []
    for line in section.splitlines():
        line = line.strip()
        if not line.startswith(("-", "*")):
            continue
        label = re.search(r"\*\*(.+?)\*\*", line)
        label_text = label.group(1).strip(" :*") if label else ""
        if label_text and skip.match(label_text):
            continue
        tick = re.search(r"`([^`\n]+)`", line)
        # When the bold label is itself the typeface (e.g. **Airbnb Cereal
        # VF**) prefer it; otherwise the face is the first backticked value,
        # since the backticks then hold the fallback stack.
        if label_text and not role_label.match(label_text):
            raw = label_text
        elif tick:
            raw = tick.group(1)
        else:
            raw = label_text
        name = raw.split(",")[0].strip().strip("'\"`*: ")
        if not name or skip.match(name):
            continue
        if not re.match(r"^[A-Za-z][A-Za-z0-9 .\-]*$", name):
            continue
        if name.lower().startswith(("-apple", "system-ui", "ui-")):
            continue
        if name not in found:
            found.append(name)
    return tuple(found[:3])


def _card(
    *,
    label: str,
    sublabel: str,
    palette: list[str],
    fonts: list[str],
    blurb: str,
) -> str:
    """One option card: mini slide, palette chips, type note."""
    bg = palette[0] if palette else "#1A1A1A"
    accent = next((c for c in palette[1:] if c != bg), "#E85D5D")
    surface = palette[2] if len(palette) > 2 else "#F5F0E8"
    ink = _readable_on(bg)
    display = fonts[0] if fonts else "Inter"

    chips = "".join(
        f'<i style="background:{html_lib.escape(c)}" title="{html_lib.escape(c)}"></i>'
        for c in palette[:6]
    )
    font_note = html_lib.escape(" / ".join(fonts[:2])) if fonts else "system stack"

    return f"""
<figure class="card">
  <div class="mini" style="background:{html_lib.escape(bg)};color:{ink}">
    <span class="kicker" style="color:{html_lib.escape(accent)}">
      {html_lib.escape(sublabel)}
    </span>
    <strong style="font-family:'{html_lib.escape(display)}',Inter,sans-serif">
      {html_lib.escape(label)}
    </strong>
    <span class="rule" style="background:{html_lib.escape(accent)}"></span>
    <span class="body" style="color:{html_lib.escape(surface)}">
      One idea per slide.
    </span>
  </div>
  <figcaption>
    <b>{html_lib.escape(label)}</b>
    <span class="chips">{chips}</span>
    <span class="meta">{font_note}</span>
    <span class="blurb">{html_lib.escape(blurb[:110])}</span>
  </figcaption>
</figure>"""


_SHELL = """<!doctype html><html><head><meta charset="utf-8"><style>
*{{box-sizing:border-box}}
body{{margin:0;padding:14px;background:#fff;
font:13px/1.45 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;color:#202124}}
h4{{margin:0 0 10px;font-size:13px;font-weight:600;color:#5f6368;
text-transform:uppercase;letter-spacing:.08em}}
.grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(190px,1fr));gap:14px}}
.card{{margin:0;border:1px solid #dadce0;border-radius:10px;overflow:hidden;background:#fff}}
.mini{{aspect-ratio:16/9;padding:14px;display:flex;flex-direction:column;
justify-content:center;gap:6px}}
.mini .kicker{{font-size:9px;letter-spacing:.14em;text-transform:uppercase;font-weight:700}}
.mini strong{{font-size:23px;line-height:1.05;font-weight:800}}
.mini .rule{{width:38px;height:3px;border-radius:2px}}
.mini .body{{font-size:10px;opacity:.85}}
figcaption{{padding:9px 11px;display:flex;flex-direction:column;gap:5px}}
figcaption b{{font-size:13px}}
.chips{{display:flex;gap:3px}}
.chips i{{width:15px;height:15px;border-radius:3px;display:block;
box-shadow:inset 0 0 0 1px rgba(0,0,0,.12)}}
.meta{{font-size:10px;color:#5f6368}}
.blurb{{font-size:10.5px;color:#5f6368;line-height:1.35}}
</style></head><body><h4>{heading}</h4><div class="grid">{cards}</div></body></html>"""


def brand_specimens_html(brands: list[dict]) -> str:
    """Specimen strip for the brand picker."""
    cards = "".join(
        _card(
            label=b.get("name", b.get("id", "")),
            sublabel=b.get("category", ""),
            palette=list(brand_palette(b.get("id", ""))),
            fonts=list(brand_fonts(b.get("id", ""))),
            blurb=b.get("description", ""),
        )
        for b in brands
    )
    return _SHELL.format(heading="Brand — pick the visual identity", cards=cards)


@lru_cache(maxsize=1)
def load_styles() -> list[dict]:
    try:
        return json.loads(_STYLES_INDEX.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        logger.warning("style index unavailable at %s", _STYLES_INDEX)
        return []


def style_specimens_html(styles: list[dict]) -> str:
    """Specimen strip for the style picker."""
    cards = "".join(
        _card(
            label=s.get("name", s.get("id", "")),
            sublabel=(s.get("mood") or ["style"])[0],
            palette=list(s.get("palette", {}).values()),
            fonts=list(s.get("fonts", [])),
            blurb=s.get("tagline", ""),
        )
        for s in styles
    )
    return _SHELL.format(heading="Style — pick the visual treatment", cards=cards)


def reference_specimen_html(reference: dict) -> str:
    """Specimen for a design system derived from a user's reference deck.

    Shown before any deck is generated: extraction from rendered pages is
    inference, so the user gets to see and correct it first.
    """
    palette = [v for v in (reference.get("palette") or {}).values() if v]
    typo = reference.get("typography") or {}
    fonts = [f for f in (typo.get("display"), typo.get("body")) if f]
    tells = reference.get("tells") or []

    card = _card(
        label=reference.get("name", "Reference deck"),
        sublabel=f"{reference.get('confidence', 'unknown')} confidence",
        palette=palette,
        fonts=fonts,
        blurb=reference.get("summary", ""),
    )
    notes = ""
    if tells:
        items = "".join(f"<li>{html_lib.escape(str(t))}</li>" for t in tells[:6])
        notes = (
            '<div class="notes"><b>Design habits detected in your deck</b>'
            f"<ul>{items}</ul>"
            "<span>These will be reproduced, because you asked to match this "
            "deck.</span></div>"
        )
    shell = _SHELL.format(
        heading=(
            f"Derived from your reference deck "
            f"({reference.get('page_count', '?')} pages analysed)"
        ),
        cards=card,
    )
    extra = (
        "<style>.notes{margin-top:14px;padding:11px 13px;border:1px solid #dadce0;"
        "border-radius:10px;font-size:11.5px;color:#5f6368;background:#fff}"
        ".notes b{display:block;color:#202124;margin-bottom:5px}"
        ".notes ul{margin:0 0 6px 16px;padding:0}"
        ".notes span{font-style:italic}</style>"
    )
    return shell.replace("</body>", f"{extra}{notes}</body>")
